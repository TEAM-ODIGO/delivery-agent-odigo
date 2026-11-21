"""화면(프론트 v0.3)이 기대하는 응답 모양 + O-09~O-12 + 분할결제(가짜 E)."""

from tests.orchestrator.conftest import OK_MESSAGE


def test_candidate_prompt_carries_restaurant_items_violations(env):
    v = env.start()
    c = v["prompt"]["candidates"][0]
    assert set(c["restaurant"]) == {
        "rating",
        "delivery_minutes",
        "delivery_fee_krw",
        "local_currency_merchant",
        "local_currency_basis",
        "cuisine",  # 카드 우측 음식 이미지용 (A 후보에서 그대로 전달)
    }
    assert c["items"] and set(c["items"][0]) == {"name", "quantity", "unit_price_krw"}
    assert all(set(x) == {"code", "message"} for x in c["violations"])


def test_payment_prompt_has_names_order_and_excluded(env):
    v = env.flow("PAYMENT_METHOD_SELECTION")
    p = v["prompt"]
    assert all(o["name"] for o in p["options"])
    assert {o["method_type"] for o in p["options"]} <= {"CARD", "LOCAL_CURRENCY"}
    assert p["order"]["total_krw"] == p["order"]["menu_subtotal_krw"] + p["order"]["delivery_fee_krw"]
    assert isinstance(p["excluded"], list)


def test_approval_and_result_carry_order_summary(env):
    v = env.flow("AWAITING_APPROVAL")
    order = v["prompt"]["order"]
    assert order["payment_method"]["payment_method_id"] == "CARD001"
    assert order["charge_krw"] == order["menu_subtotal_krw"] + order["delivery_fee_krw"] - order["instant_discount_krw"]
    done = env.reply(v, type="APPROVE")
    assert done["result"]["order"]["charge_krw"] == done["result"]["charge_krw"]


def test_o09_lists_only_my_runs_newest_first(env):
    a = env.flow("AWAITING_APPROVAL")
    env.flow("CANDIDATES_READY", user="U002")
    mine = env.svc().list_runs("U001")
    assert [r["request_id"] for r in mine["results"]] == [a["request_id"]]
    row = mine["results"][0]
    assert row["state"] == "AWAITING_APPROVAL" and row["restaurant_name"] and "user_id" not in row
    assert env.svc().list_runs("U001", limit=1)["count"] == 1


def test_o11_admin_list_has_user_and_filters_by_state(env):
    env.flow("AWAITING_APPROVAL")
    env.flow("CANDIDATES_READY", user="U002")
    rows = env.svc().list_runs(None, 100)["results"]
    assert {r["user_id"] for r in rows} == {"U001", "U002"}
    only = env.svc().list_runs(None, 100, "CANDIDATES_READY")["results"]
    assert [r["user_id"] for r in only] == ["U002"]


def test_o10_payment_methods_for_delegation_screen(env):
    res = env.svc().list_payment_methods("U001")
    assert res["count"] == len(res["results"]) >= 2
    assert {"payment_method_id", "name", "type", "status", "balance_krw", "monthly_benefit_remaining_krw"} == set(
        res["results"][0]
    )


def test_o12_audit_logs_of_a_run(env):
    v = env.flow("COMPLETED")
    logs = env.svc().list_audit_logs(v["request_id"])
    assert logs["count"] >= 1 and all(l["request_id"] == v["request_id"] for l in logs["results"])


# ---------------- 분할결제(로컬 확장): 가짜 E ----------------
def _e01(**over):
    body = {
        "request_id": "REQSPL1",
        "user_id": "U001",
        "execution_id": "EXE-SPL1",
        "approval_id": "AP1",
        "quote_id": "QT1",
        "proposal_hash": "sha256:" + "a" * 64,
        "payment_method_id": "CARD001",
        "charge_krw": 15600,
        "local_currency_payment_method_id": "LOCAL001",
        "local_currency_charge_krw": 8000,
    }
    body.update(over)
    return body


def test_fake_e_split_records_legs_sends_local_fields_to_d06_and_is_idempotent(env):
    from app.core.errors import AppError

    seen = []
    pay = env.payment
    pay.check_authorization = lambda body, rid: seen.append(body) or {"allowed": True}
    out = pay.request_payment(_e01())
    assert out["status"] == "APPROVED" and out["charge_krw"] == 15600
    assert out["payments"] == [
        {"payment_method_id": "LOCAL001", "charge_krw": 8000},
        {"payment_method_id": "CARD001", "charge_krw": 7600},
    ]
    assert seen[0]["local_currency_payment_method_id"] == "LOCAL001"
    assert pay.request_payment(_e01())["payment_id"] == out["payment_id"]  # 멱등
    try:
        pay.request_payment(_e01(local_currency_charge_krw=5000))
        raise AssertionError("같은 execution_id에 다른 분할은 409여야 한다")
    except AppError as exc:
        assert exc.status == 409


def test_fake_e_non_split_has_no_legs_and_six_d06_fields(env):
    seen = []
    pay = env.payment
    pay.check_authorization = lambda body, rid: seen.append(body) or {"allowed": True}
    out = pay.request_payment(
        _e01(execution_id="EXE-N1", local_currency_payment_method_id=None, local_currency_charge_krw=None)
    )
    assert "payments" not in out
    assert set(seen[0]) == {
        "execution_id", "approval_id", "quote_id", "proposal_hash", "charge_krw", "payment_method_id",
    }


def test_select_with_split_on_fake_stack_is_409_not_a_crash(env):
    """가짜 C는 분할 추천을 모른다 → 500이 아니라 409 SPLIT_NOT_AVAILABLE."""
    from app.core.errors import AppError

    v = env.flow("PAYMENT_METHOD_SELECTION", message=OK_MESSAGE)
    try:
        env.reply(v, type="SELECT_PAYMENT_METHOD", payment_method_id="CARD001", local_currency_amount_krw=8000)
        raise AssertionError("분할을 모르는 C에서는 거절돼야 한다")
    except AppError as exc:
        assert exc.status == 409 and exc.code == "SPLIT_NOT_AVAILABLE"
