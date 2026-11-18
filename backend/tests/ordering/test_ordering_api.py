"""B API 시나리오 테스트 (명세서 B-01·02·05·06·07·08·09).

가짜 수집 데이터: R1(지역화폐 O) 후라이드 18,000 / 양념 품절 / 생맥주 / 치킨무 500
                  배달비 15,000원 이상 3,000원, 30,000원 이상 1,000원, 최소주문 15,000원
                  R2(지역화폐 정보 없음) 간장치킨 18,000
"""

from tests.ordering.conftest import TOKEN

AUTH = {"Authorization": f"Bearer {TOKEN}"}


def headers(request_id="REQ001", key=None, user="U001"):
    h = {**AUTH, "X-Request-ID": request_id, "X-User-ID": user}
    if key is not None:
        h["Idempotency-Key"] = key
    return h


def plan_body(restaurant_id="R1", items=None, budget=25000, candidate="CAND001", local_only=False, **extra):
    body = {
        "request_id": "REQ001",
        "candidate_id": candidate,
        "restaurant_id": restaurant_id,
        "items": items or [{"menu_id": "M1", "option_ids": [], "quantity": 1}],
        "constraints_version": 2,
        "constraints": {
            "budget_krw": budget, "people": 2, "cuisine": "치킨", "excluded_ingredients": [],
            "max_spice_level": 0, "payment_requirements": {"local_currency_only": local_only},
            "delivery_address": {"region_code": "11140", "lat": 37.566, "lng": 126.9826},
            "max_delivery_minutes": 40, "menu_keywords": [],
        },
        "budget_basis": "POST_FINANCIAL",
        "catalog_version": "YGY-20261117120000",
        "policy_id": "550e8400-e29b-41d4-a716-446655440000",
    }
    body.update(extra)
    return body


def create_plan(env, **kwargs):
    body = plan_body(**kwargs)
    r = env.client.post("/ordering/order-plans", json=body,
                        headers=headers(key=f"REQ001:b01:{body['candidate_id']}"))
    return r


def bind(env, plan, method="CARD001", version=None):
    version = version or plan["order_version"]
    return env.client.post(
        f"/ordering/order-plans/{plan['order_plan_id']}/benefit-bindings",
        json={"base_order_version": version, "quote_id": plan["quote"]["quote_id"],
              "recommendation_id": "REC001", "payment_method_id": method, "benefit_rule_version": "BEN-V1"},
        headers=headers(key=f"REQ001:b05:{version}:{method}"),
    )


def target(env, plan, revision):
    return env.client.post(
        f"/ordering/order-plans/{plan['order_plan_id']}/approval-targets",
        json={"base_order_version": plan["order_version"], "quote_id": plan["quote"]["quote_id"],
              "expected_benefit_revision": revision},
        headers=headers(key=f"REQ001:b06:{plan['order_version']}:{revision}"),
    )


def approve(env, tgt, approval_id="APR001", execution_id="EXE-001", **overrides):
    record = {
        "approval_id": approval_id, "request_id": "REQ001",
        "approval_target_id": tgt["approval_target_id"], "order_version": tgt["order_version"],
        "quote_id": tgt["quote"]["quote_id"], "status": "APPROVED", "execution_id": execution_id,
        "approved_at": "2026-11-17T12:01:00+09:00", "expires_at": "2026-11-17T12:08:00+09:00",
        "revoke_reason": None, "proposal_hash": "sha256:abc",
    }
    record.update(overrides)
    env.approvals.approvals[approval_id] = record


def check(env, plan, tgt, approval_id="APR001", execution_id="EXE-001"):
    return env.client.post(
        f"/ordering/order-plans/{plan['order_plan_id']}/execution-checks",
        json={"base_order_version": plan["order_version"], "quote_id": plan["quote"]["quote_id"],
              "approval_target_id": tgt["approval_target_id"], "approval_id": approval_id,
              "execution_id": execution_id, "stage": "MOCK_PAYMENT"},
        headers=headers(),
    )


def event(env, plan, tgt, event_id="event_001", status="APPROVED", key=None, **overrides):
    body = {"event_id": event_id, "approval_target_id": tgt["approval_target_id"],
            "quote_id": tgt["quote"]["quote_id"], "execution_id": "EXE-001", "payment_id": "PAY001",
            "status": status, "observed_at": "2026-11-17T12:02:00+09:00", "reason_code": None}
    body.update(overrides)
    return env.client.post(f"/ordering/order-plans/{plan['order_plan_id']}/payment-events", json=body,
                           headers=headers(key=key or event_id))


def ready_plan(env):
    """B-01 → B-05 → B-06 까지 진행한 상태"""
    plan = create_plan(env).json()["order_plans"][0]
    bind(env, plan)
    tgt = target(env, plan, 1).json()["approval_target"]
    return plan, tgt


# ======================================================================= B-01
def test_b01_creates_plan_and_quote(env):
    r = create_plan(env)
    assert r.status_code == 201
    body = r.json()
    assert body["next_action"] == "CALCULATE_BENEFITS"
    assert body["issues"] == [] and body["excluded_candidate_ids"] == []
    plan = body["order_plans"][0]
    assert plan["order_version"] == 1
    assert plan["validation_status"] == "VALID" and plan["quote_status"] == "ACTIVE"
    assert plan["quote"]["menu_subtotal_krw"] == 18000
    assert plan["quote"]["delivery_fee_krw"] == 3000
    assert plan["quote"]["pre_financial_amount_krw"] == 21000
    assert plan["quote"]["expires_at"] == "2026-11-17T12:10:00+09:00"
    assert plan["budget_check"] == {"basis": "POST_FINANCIAL", "limit_krw": 25000,
                                    "compared_krw": None, "status": "PENDING_BENEFIT"}


def test_b01_delivery_fee_follows_order_amount_tier(env):
    plan = create_plan(env, items=[{"menu_id": "M1", "quantity": 2}]).json()["order_plans"][0]
    assert plan["quote"]["menu_subtotal_krw"] == 36000
    assert plan["quote"]["delivery_fee_krw"] == 1000  # 30,000원 이상 구간


def test_b01_is_idempotent(env):
    first = create_plan(env)
    again = create_plan(env)
    assert again.status_code == 201 and again.json() == first.json()

    other = env.client.post("/ordering/order-plans", json=plan_body(budget=30000),
                            headers=headers(key="REQ001:b01:CAND001"))
    assert other.status_code == 409 and other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_b01_idempotency_key_rules(env):
    missing = env.client.post("/ordering/order-plans", json=plan_body(), headers=headers())
    assert missing.status_code == 400
    wrong = env.client.post("/ordering/order-plans", json=plan_body(), headers=headers(key="REQ001:b01"))
    assert wrong.status_code == 400


def test_b01_validation_error_matches_spec(env):
    r = create_plan(env, items=[{"menu_id": "M1", "option_ids": [], "quantity": 0}])
    assert r.status_code == 422
    assert r.json() == {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "수량은 양의 정수여야 합니다.",
            "details": {
                "current_order_version": None, "current_benefit_revision": None,
                "current_approval_target_id": None, "next_action": "NONE", "retryable": False,
                "fields": [{"field": "items[0].quantity", "message": "수량은 양의 정수여야 합니다."}],
            },
        },
        "request_id": "REQ001",
    }


def test_b01_unavailable_returns_research_required(env):
    cases = {
        "MENU_SOLD_OUT": dict(items=[{"menu_id": "M2", "quantity": 1}]),
        "ALCOHOL_NOT_ALLOWED": dict(items=[{"menu_id": "M3", "quantity": 1}]),
        "BELOW_MIN_ORDER": dict(items=[{"menu_id": "M4", "quantity": 1}]),
        "OPTION_NOT_FOUND": dict(items=[{"menu_id": "M1", "option_ids": ["OPT001"], "quantity": 1}]),
        "MENU_NOT_FOUND": dict(items=[{"menu_id": "NOPE", "quantity": 1}]),
        "RESTAURANT_NOT_FOUND": dict(restaurant_id="NOPE"),
    }
    for index, (code, kwargs) in enumerate(cases.items()):
        r = create_plan(env, candidate=f"CAND{index}", **kwargs)
        assert r.status_code == 200, code
        body = r.json()
        assert body["order_plans"] == [] and body["next_action"] == "RESEARCH_REQUIRED"
        assert body["issues"][0]["code"] == code
        assert body["excluded_candidate_ids"] == [f"CAND{index}"]


def test_b01_catalog_version_change_is_reported(env):
    body = create_plan(env, catalog_version="YGY-OLD").json()
    assert body["order_plans"]
    assert body["issues"][0]["code"] == "CATALOG_VERSION_CHANGED"


# ======================================================================= B-02
def test_b02_view_and_errors(env):
    plan = create_plan(env).json()["order_plans"][0]
    r = env.client.get(f"/ordering/order-plans/{plan['order_plan_id']}", headers=headers())
    assert r.status_code == 200
    body = r.json()
    assert body["view"] == plan
    assert body["is_current_quote"] is True and body["current_order_version"] == 1
    assert body["observed_at"] == "2026-11-17T12:00:00+09:00"

    old = env.client.get(f"/ordering/order-plans/{plan['order_plan_id']}?quote_id=QT-OLD", headers=headers())
    assert old.json()["is_current_quote"] is False

    missing = env.client.get("/ordering/order-plans/op_nope", headers=headers())
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ORDER_PLAN_NOT_FOUND"
    assert missing.json()["error"]["details"]["next_action"] == "NONE"

    other = env.client.get(f"/ordering/order-plans/{plan['order_plan_id']}", headers=headers(request_id="REQ999"))
    assert other.status_code == 403


def test_b02_expired_quote_status(env):
    plan = create_plan(env).json()["order_plans"][0]
    env.clock.advance(minutes=11)
    view = env.client.get(f"/ordering/order-plans/{plan['order_plan_id']}", headers=headers()).json()["view"]
    assert view["quote_status"] == "EXPIRED"


# ======================================================================= B-05
def test_b05_binds_benefit_and_checks_budget(env):
    plan = create_plan(env).json()["order_plans"][0]
    r = bind(env, plan)
    assert r.status_code == 200
    body = r.json()
    assert body["benefit_binding"] == {"benefit_revision": 1, "payment_method_id": "CARD001",
                                       "instant_discount_krw": 2000, "charge_krw": 19000,
                                       "benefit_rule_version": "BEN-V1"}
    assert body["view"]["budget_check"] == {"basis": "POST_FINANCIAL", "limit_krw": 25000,
                                            "compared_krw": 19000, "status": "PASS"}
    assert body["next_action"] == "BUILD_APPROVAL_TARGET"
    # C-05에는 B 견적의 금액과 O가 인증한 사용자를 보낸다
    assert env.benefit.calls[0] == {
        "request_id": "REQ001", "user_id": "U001", "restaurant_id": "R1", "menu_subtotal_krw": 18000,
        "delivery_fee_krw": 3000, "payment_method_id": "CARD001", "benefit_rule_version": "BEN-V1",
    }
    assert bind(env, plan).json() == body  # 같은 키 → 같은 결과 (C를 다시 부르지 않음)
    assert len(env.benefit.calls) == 1


def test_b05_budget_fail(env):
    plan = create_plan(env, budget=18000).json()["order_plans"][0]
    assert bind(env, plan).json()["view"]["budget_check"]["status"] == "FAIL"


def test_b05_errors(env):
    plan = create_plan(env).json()["order_plans"][0]

    conflict = bind(env, plan, version=2)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "VERSION_CONFLICT"
    assert conflict.json()["error"]["details"]["current_order_version"] == 1

    env.benefit.mode = "unavailable"
    down = bind(env, plan)
    assert down.status_code == 503
    assert down.json()["error"]["code"] == "BENEFIT_UNAVAILABLE"
    assert down.json()["error"]["details"]["retryable"] is True

    env.benefit.mode = "mismatch"
    stale = bind(env, plan, method="CARD002")
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_CALCULATION"

    env.benefit.mode = "rejected"
    rejected = bind(env, plan, method="CARD003")
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "BENEFIT_REJECTED"


def test_b05_not_eligible_and_missing_user(env):
    plan = create_plan(env, restaurant_id="R2", items=[{"menu_id": "M21", "quantity": 1}]).json()["order_plans"][0]
    r = bind(env, plan, method="LOCAL001")
    assert r.status_code == 409 and r.json()["error"]["code"] == "PAYMENT_METHOD_NOT_ELIGIBLE"

    no_user = env.client.post(
        f"/ordering/order-plans/{plan['order_plan_id']}/benefit-bindings",
        json={"base_order_version": 1, "quote_id": plan["quote"]["quote_id"], "recommendation_id": "REC001",
              "payment_method_id": "CARD001", "benefit_rule_version": "BEN-V1"},
        headers={**AUTH, "Idempotency-Key": "REQ001:b05:1:CARD001"},
    )
    # B-01 때 X-User-ID를 저장해 두었으므로 헤더가 없어도 된다
    assert no_user.status_code == 200


def test_b05_expired_quote(env):
    plan = create_plan(env).json()["order_plans"][0]
    env.clock.advance(minutes=10)
    r = bind(env, plan)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "QUOTE_EXPIRED"
    assert r.json()["error"]["details"]["next_action"] == "REVALIDATE"


# ======================================================================= B-06
def test_b06_builds_full_quote_for_d(env):
    plan = create_plan(env).json()["order_plans"][0]
    bind(env, plan)
    r = target(env, plan, 1)
    assert r.status_code == 201
    body = r.json()
    assert body["next_action"] == "REQUEST_D_APPROVAL"
    tgt = body["approval_target"]
    assert tgt["status"] == "PREPARED" and tgt["benefit_revision"] == 1 and tgt["constraints_version"] == 2
    assert tgt["request_id"] == "REQ001" and tgt["order_plan_id"] == plan["order_plan_id"]
    assert tgt["quote"] == {
        "quote_id": plan["quote"]["quote_id"], "restaurant_id": "R1",
        "items": [{"menu_id": "M1", "name": "후라이드치킨", "description": "국내산 닭", "quantity": 1,
                   "unit_price_krw": 18000, "options": []}],
        "menu_subtotal_krw": 18000, "delivery_fee_krw": 3000, "payment_method_id": "CARD001",
        "instant_discount_krw": 2000, "charge_krw": 19000, "catalog_version": "YGY-20261117120000",
        "benefit_rule_version": "BEN-V1", "expires_at": "2026-11-17T12:10:00+09:00", "unverified_conditions": [],
    }
    assert target(env, plan, 1).json() == body  # 멱등


def test_b06_revision_mismatch_and_supersede(env):
    plan = create_plan(env).json()["order_plans"][0]
    bind(env, plan)
    wrong = target(env, plan, 2)
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "BENEFIT_RESULT_MISMATCH"
    assert wrong.json()["error"]["details"]["current_benefit_revision"] == 1

    first = target(env, plan, 1).json()["approval_target"]
    rebind = bind(env, plan, method="CARD002").json()  # 결제수단을 바꾸면 이전 승인 대상은 무효
    assert rebind["benefit_binding"]["benefit_revision"] == 2
    assert rebind["invalidated_approval_target_ids"] == [first["approval_target_id"]]


def test_b06_unknown_local_currency_is_marked_unverified(env):
    plan = create_plan(env, restaurant_id="R2", items=[{"menu_id": "M21", "quantity": 1}],
                       local_only=True).json()["order_plans"][0]
    bind(env, plan)
    tgt = target(env, plan, 1).json()["approval_target"]
    assert tgt["quote"]["unverified_conditions"] == ["LOCAL_CURRENCY_MERCHANT_UNKNOWN"]


# ======================================================================= B-07
def test_b07_ready_builds_execution_input(env):
    plan, tgt = ready_plan(env)
    approve(env, tgt)
    r = check(env, plan, tgt)
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True and body["reasons"] == [] and body["next_action"] == "NONE"
    assert body["stage"] == "MOCK_PAYMENT" and body["execution_check_id"].startswith("check_")
    assert body["execution_input"] == {
        "request_id": "REQ001", "execution_id": "EXE-001", "approval_id": "APR001",
        "quote_id": plan["quote"]["quote_id"], "proposal_hash": "sha256:abc",
        "payment_method_id": "CARD001", "charge_krw": 19000,
    }


def test_b07_not_ready_reasons(env):
    plan, tgt = ready_plan(env)
    approve(env, tgt, status="PENDING", execution_id=None, proposal_hash=None)
    body = check(env, plan, tgt).json()
    assert body["ready"] is False and body["execution_input"] is None
    assert body["reasons"] == ["APPROVAL_NOT_APPROVED", "EXECUTION_ID_MISMATCH", "APPROVAL_HASH_UNAVAILABLE"]
    assert body["next_action"] == "REQUEST_D_APPROVAL"

    missing = check(env, plan, tgt, approval_id="APR404").json()
    assert missing["reasons"] == ["APPROVAL_NOT_FOUND"]

    approve(env, tgt)
    env.clock.advance(minutes=9)  # 승인 만료(12:08) 뒤, 견적 만료(12:10) 전
    assert check(env, plan, tgt).json()["reasons"] == ["APPROVAL_EXPIRED"]


def test_b07_d_unavailable(env):
    plan, tgt = ready_plan(env)
    env.approvals.unavailable = True
    r = check(env, plan, tgt)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "APPROVAL_UNAVAILABLE"
    assert r.json()["error"]["details"] == {
        "current_order_version": None, "current_benefit_revision": None, "current_approval_target_id": None,
        "next_action": "CHECK_DATA", "retryable": True,
    }


def test_b07_superseded_target_is_not_ready(env):
    plan, tgt = ready_plan(env)
    approve(env, tgt)
    bind(env, plan, method="CARD002")
    assert "APPROVAL_TARGET_NOT_CURRENT" in check(env, plan, tgt).json()["reasons"]


# ======================================================================= B-08
def test_b08_approved_closes_plan(env):
    plan, tgt = ready_plan(env)
    r = event(env, plan, tgt)
    assert r.status_code == 200
    assert r.json() == {"event_id": "event_001", "payment_id": "PAY001",
                        "linked_quote_id": plan["quote"]["quote_id"], "application_status": "APPLIED",
                        "payment_status": "APPROVED", "applied_to_current_quote": True, "next_action": "NONE"}
    assert event(env, plan, tgt).json() == r.json()  # 같은 이벤트 다시 → 같은 결과

    view = env.client.get(f"/ordering/order-plans/{plan['order_plan_id']}", headers=headers()).json()["view"]
    assert view["quote_status"] == "USED"
    closed = bind(env, plan, method="CARD002")
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "ORDER_CLOSED"

    late = event(env, plan, tgt, event_id="event_002", status="FAILED").json()
    assert late["application_status"] == "IGNORED_AFTER_FINAL"


def test_b08_conflict_unknown_and_key(env):
    plan, tgt = ready_plan(env)
    unknown = event(env, plan, tgt, status="UNKNOWN", payment_id=None).json()
    assert unknown["application_status"] == "APPLIED" and unknown["next_action"] == "WAIT_PAYMENT_RESULT"

    conflict = event(env, plan, tgt, status="APPROVED")  # 같은 event_id, 다른 내용
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "EVENT_CONFLICT"

    wrong_key = event(env, plan, tgt, event_id="event_009", key="something-else")
    assert wrong_key.status_code == 400

    approved = event(env, plan, tgt, event_id="event_010", status="APPROVED").json()
    assert approved["application_status"] == "APPLIED"  # UNKNOWN → APPROVED는 반영


def test_b08_old_target_is_recorded_only(env):
    plan, old = ready_plan(env)
    bind(env, plan, method="CARD002")
    r = event(env, plan, old).json()
    assert r["application_status"] == "RECORDED_STALE" and r["applied_to_current_quote"] is False


# ======================================================================= 공통
def test_service_token_and_health(env):
    assert env.client.get("/ordering/order-plans/op_1").status_code == 401
    wrong = env.client.get("/ordering/order-plans/op_1", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "UNAUTHORIZED_SERVICE"
    assert env.client.get("/ordering/health").json() == {"service": "ordering", "status": "UP"}
