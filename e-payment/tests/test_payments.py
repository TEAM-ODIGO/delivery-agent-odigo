import pytest

from tests.conftest import H, pay_body

APPROVED_KEYS = {"payment_id", "execution_id", "transaction_id", "request_id", "status", "charge_krw",
                 "refunded_krw", "payment_method_id", "approved_at", "mock"}


def methods(client, user="U001"):
    r = client.get("/internal/payment-methods", params={"user_id": user}, headers=H)
    return {m["payment_method_id"]: m for m in r.json()["results"]}


# ---------------- E-01 승인·원장 ----------------
def test_e01_approved_matches_spec_shape(pay):
    r = pay()
    assert r.status_code == 200
    j = r.json()
    assert set(j) == APPROVED_KEYS
    assert j["status"] == "APPROVED" and j["mock"] is True and j["refunded_krw"] == 0
    assert j["approved_at"].endswith("+09:00")
    assert j["payment_id"].startswith("PAY") and j["transaction_id"].startswith("TXN")


def test_e01_ledger_updates_card_performance_and_local_balance(client, pay):
    before = methods(client)
    pay(payment_method_id="CARD001", charge_krw=18000)
    pay(payment_method_id="LOCAL001", charge_krw=10000)
    after = methods(client)
    assert after["CARD001"]["monthly_performance_krw"] == before["CARD001"]["monthly_performance_krw"] + 18000
    assert after["LOCAL001"]["balance_krw"] == before["LOCAL001"]["balance_krw"] - 10000


def test_e01_calls_d06_with_exactly_the_six_fields(pay, d):
    body = pay_body()
    pay(body)
    sent, rid = d.calls[0]
    assert set(sent) == {"execution_id", "approval_id", "quote_id", "proposal_hash", "charge_krw", "payment_method_id"}
    assert sent["execution_id"] == body["execution_id"] and rid == "REQ001"


# ---------------- 멱등 ----------------
def test_same_execution_id_returns_same_payment_and_charges_once(client, pay, d):
    body = pay_body(payment_method_id="LOCAL001", charge_krw=10000)
    a, b = pay(body), pay(body)
    assert a.json() == b.json()
    assert len(d.calls) == 1  # 두 번째는 D-06도 부르지 않는다
    assert methods(client)["LOCAL001"]["balance_krw"] == 110000  # 한 번만 차감


def test_same_execution_id_different_content_is_409(pay):
    body = pay_body()
    pay(body)
    r = pay({**body, "charge_krw": 99999})
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_idempotency_key_must_equal_execution_id(pay):
    r = pay(headers={"Idempotency-Key": "OTHER"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------- D-06 ----------------
def test_d06_block_means_no_payment_and_retry_allowed(client, pay, d):
    body = pay_body()
    d.allowed, d.reasons = False, ["PAYMENT_AMOUNT_MISMATCH"]
    r = pay(body)
    assert r.status_code == 200
    assert r.json() == {"payment_id": None, "execution_id": body["execution_id"], "status": "BLOCKED",
                        "reason_codes": ["PAYMENT_AMOUNT_MISMATCH"]}
    assert client.get(f"/payments/executions/{body['execution_id']}", headers=H).status_code == 404
    d.allowed, d.reasons = True, []
    assert pay(body).json()["status"] == "APPROVED"  # 차단은 결제가 아니므로 같은 키 재시도 가능


def test_d_unavailable_is_blocked_not_paid(client, pay, d):
    d.raises = True
    j = pay().json()
    assert j["status"] == "BLOCKED" and j["reason_codes"] == ["DELEGATION_UNAVAILABLE"]
    assert methods(client)["CARD001"]["monthly_performance_krw"] == 350000


# ---------------- 거절·잔액·미확정 ----------------
def test_decline_is_failed_and_does_not_touch_ledger(make):
    c = make(mock_mode="DECLINE")
    body = pay_body(payment_method_id="LOCAL001", charge_krw=5000)
    j = c.post("/payments", json=body, headers={**H, "Idempotency-Key": body["execution_id"]}).json()
    assert j["status"] == "FAILED" and j["reason_codes"] == ["MOCK_DECLINED"] and j["approved_at"] is None
    assert methods(c)["LOCAL001"]["balance_krw"] == 120000


def test_insufficient_local_balance_is_failed(client, pay):
    j = pay(user_id="U002", payment_method_id="LOCAL101", charge_krw=5000).json()
    assert j["status"] == "FAILED" and j["reason_codes"] == ["INSUFFICIENT_BALANCE"]


def test_other_users_payment_method_is_failed(pay):
    j = pay(user_id="U001", payment_method_id="CARD101").json()
    assert j["status"] == "FAILED" and j["reason_codes"] == ["PAYMENT_METHOD_NOT_FOUND"]


def test_unknown_then_confirmed_by_e03_lookup(make):
    c = make(mock_mode="UNKNOWN")
    body = pay_body(payment_method_id="LOCAL001", charge_krw=10000)
    h = {**H, "Idempotency-Key": body["execution_id"]}
    j = c.post("/payments", json=body, headers=h).json()
    assert j["status"] == "UNKNOWN" and j["reason_codes"] == ["FEP_TIMEOUT"]
    assert methods(c)["LOCAL001"]["balance_krw"] == 120000  # 확정 전에는 원장 그대로
    got = c.get(f"/payments/executions/{body['execution_id']}", headers=H).json()
    assert got["status"] == "APPROVED" and got["approved_at"]
    assert methods(c)["LOCAL001"]["balance_krw"] == 110000  # 확정 시점에 한 번 차감
    c.get(f"/payments/executions/{body['execution_id']}", headers=H)
    assert methods(c)["LOCAL001"]["balance_krw"] == 110000  # 다시 읽어도 중복 차감 없음


def test_unknown_stays_unknown_without_reconcile_then_e10_confirms(make):
    c = make(mock_mode="UNKNOWN", reconcile_on_read=False)
    body = pay_body()
    j = c.post("/payments", json=body, headers={**H, "Idempotency-Key": body["execution_id"]}).json()
    assert c.get(f"/payments/executions/{body['execution_id']}", headers=H).json()["status"] == "UNKNOWN"
    r = c.post("/internal/reconciliations", json={"transaction_ids": [j["transaction_id"]], "reason": "UNKNOWN_RECONCILIATION"},
               headers={**H, "Idempotency-Key": "recon-1"})
    assert r.status_code == 202 and r.json()["status"] == "QUEUED"
    assert c.get(f"/payments/executions/{body['execution_id']}", headers=H).json()["status"] == "APPROVED"
    again = c.post("/internal/reconciliations", json={"transaction_ids": [j["transaction_id"]], "reason": "UNKNOWN_RECONCILIATION"},
                   headers={**H, "Idempotency-Key": "recon-1"})
    assert again.json()["reconciliation_id"] == r.json()["reconciliation_id"]
    diff = c.post("/internal/reconciliations", json={"transaction_ids": ["TXN-OTHER"]}, headers={**H, "Idempotency-Key": "recon-1"})
    assert diff.status_code == 409


def test_lost_stays_unknown_even_after_lookup(make):
    c = make(mock_mode="LOST")
    body = pay_body()
    c.post("/payments", json=body, headers={**H, "Idempotency-Key": body["execution_id"]})
    assert c.get(f"/payments/executions/{body['execution_id']}", headers=H).json()["status"] == "UNKNOWN"


def test_mock_override_header_only_when_enabled(make, d):
    off = make()
    body = pay_body()
    r = off.post("/payments", json=body, headers={**H, "Idempotency-Key": body["execution_id"], "X-Mock-Outcome": "DECLINE"})
    assert r.json()["status"] == "APPROVED"  # 꺼져 있으면 무시
    on = make(allow_mock_override=True)
    body = pay_body()
    r = on.post("/payments", json=body, headers={**H, "Idempotency-Key": body["execution_id"], "X-Mock-Outcome": "DECLINE"})
    assert r.json()["status"] == "FAILED"


# ---------------- 검증·인증 ----------------
def test_validation_error_matches_spec_example(pay):
    r = pay(charge_krw=0)
    assert r.status_code == 400
    j = r.json()
    assert j["error"]["code"] == "VALIDATION_ERROR"
    assert j["error"]["details"]["fields"] == [{"loc": "body.charge_krw", "reason": "must_be_positive"}]
    assert j["request_id"] == "REQ001"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic x"}])
def test_service_token_required(client, headers):
    body = pay_body()
    r = client.post("/payments", json=body, headers={**headers, "Idempotency-Key": body["execution_id"]})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED_SERVICE"


def test_empty_service_token_denies_everything(engine, d):
    from tests.conftest import make_client

    c = make_client(engine, d)
    c.app.dependency_overrides  # noqa: B018
    from e_payment.config import Settings
    from e_payment.main import create_app
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    app = create_app(Settings(service_token=""), d, sessionmaker(bind=engine, expire_on_commit=False))
    with TestClient(app) as cl:
        assert cl.get("/internal/payment-methods", params={"user_id": "U001"}, headers={"Authorization": "Bearer "}).status_code == 401


def test_health_needs_no_token(client):
    assert client.get("/health").json()["status"] == "UP"


# ---------------- E-02·E-03 ----------------
def test_e03_missing_is_404_payment_not_found(client):
    r = client.get("/payments/executions/NOPE", headers=H)
    assert r.status_code == 404 and r.json()["error"]["code"] == "PAYMENT_NOT_FOUND"


def test_e02_returns_payment_with_refunds_list(client, pay):
    j = pay().json()
    client.post(f"/payments/{j['payment_id']}/refunds", json={"user_id": "U001", "amount_krw": 18000, "reason": "사용자 환불 요청"},
                headers={**H, "Idempotency-Key": "K1"})
    got = client.get(f"/payments/{j['payment_id']}", headers=H).json()
    assert got["refunded_krw"] == 18000
    assert got["refunds"][0]["status"] == "REFUNDED" and got["refunds"][0]["amount_krw"] == 18000
    assert client.get("/payments/NOPE", headers=H).status_code == 404


# ---------------- E-04 ----------------
def test_e04_pagination_newest_first(client, pay):
    ids = [pay().json()["payment_id"] for _ in range(5)]
    pay(user_id="U002", payment_method_id="CARD101")
    first = client.get("/payments", params={"user_id": "U001", "limit": 2}, headers=H).json()
    assert [i["payment_id"] for i in first["items"]] == ids[::-1][:2] and first["next_cursor"]
    assert set(first["items"][0]) == {"payment_id", "request_id", "status", "charge_krw", "refunded_krw", "approved_at", "mock"}
    second = client.get("/payments", params={"user_id": "U001", "limit": 2, "cursor": first["next_cursor"]}, headers=H).json()
    third = client.get("/payments", params={"user_id": "U001", "limit": 2, "cursor": second["next_cursor"]}, headers=H).json()
    got = [i["payment_id"] for p in (first, second, third) for i in p["items"]]
    assert got == ids[::-1] and third["next_cursor"] is None


def test_e04_invalid_cursor_and_limit(client):
    r = client.get("/payments", params={"user_id": "U001", "cursor": "###"}, headers=H)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_CURSOR"
    assert client.get("/payments", params={"user_id": "U001", "limit": 0}, headers=H).status_code == 400


# ---------------- E-05·E-06 환불 ----------------
def refund(client, pid, amount=18000, key="K1", user="U001"):
    return client.post(f"/payments/{pid}/refunds", json={"user_id": user, "amount_krw": amount, "reason": "사용자 환불 요청"},
                       headers={**H, "Idempotency-Key": key})


def test_refund_flow_restores_ledger(client, pay):
    pid = pay(payment_method_id="LOCAL001", charge_krw=10000).json()["payment_id"]
    r = refund(client, pid, 10000)
    assert r.status_code == 202
    j = r.json()
    assert j["status"] == "PENDING" and j["mock"] is True and j["refund_id"].startswith("RF")
    got = client.get(f"/payments/{pid}/refunds/{j['refund_id']}", headers=H).json()
    assert got == {**j, "status": "REFUNDED"}
    assert methods(client)["LOCAL001"]["balance_krw"] == 120000
    assert client.get(f"/payments/{pid}", headers=H).json()["refunded_krw"] == 10000


def test_refund_amount_exceeded_and_partial(client, pay):
    pid = pay(charge_krw=18000).json()["payment_id"]
    assert refund(client, pid, 8000, "A").status_code == 202
    r = refund(client, pid, 11000, "B")
    assert r.status_code == 409 and r.json()["error"]["code"] == "REFUND_AMOUNT_EXCEEDED"
    assert refund(client, pid, 10000, "C").status_code == 202
    assert refund(client, pid, 1, "D").status_code == 409


def test_refund_idempotency(client, pay):
    pid = pay().json()["payment_id"]
    a, b = refund(client, pid, 5000, "SAME"), refund(client, pid, 5000, "SAME")
    assert a.json()["refund_id"] == b.json()["refund_id"]
    assert client.get(f"/payments/{pid}", headers=H).json()["refunded_krw"] == 5000
    assert refund(client, pid, 6000, "SAME").status_code == 409


def test_refund_guards(client, pay, make):
    pid = pay().json()["payment_id"]
    assert refund(client, pid, user="U002").status_code == 403
    assert refund(client, "NOPE").status_code == 404
    c = make(mock_mode="DECLINE")
    body = pay_body()
    failed = c.post("/payments", json=body, headers={**H, "Idempotency-Key": body["execution_id"]}).json()
    r = c.post(f"/payments/{failed['payment_id']}/refunds", json={"user_id": "U001", "amount_krw": 100},
               headers={**H, "Idempotency-Key": "X"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATUS"
    assert client.get(f"/payments/{pid}/refunds/NOPE", headers=H).json()["error"]["code"] == "REFUND_NOT_FOUND"


def test_refund_failure_keeps_payment_unrefunded(make):
    c = make(mock_refund_mode="FAILED")
    body = pay_body()
    pid = c.post("/payments", json=body, headers={**H, "Idempotency-Key": body["execution_id"]}).json()["payment_id"]
    rid = c.post(f"/payments/{pid}/refunds", json={"user_id": "U001", "amount_krw": 1000}, headers={**H, "Idempotency-Key": "K"}).json()["refund_id"]
    assert c.get(f"/payments/{pid}/refunds/{rid}", headers=H).json()["status"] == "FAILED"
    assert c.get(f"/payments/{pid}", headers=H).json()["refunded_krw"] == 0


# ---------------- E-07·E-08·E-09 (모의 기관) ----------------
def test_fep_authorize_lookup_and_refund(client):
    h = {**H, "Idempotency-Key": "TXN-F1"}
    body = {"transaction_id": "TXN-F1", "payment_id": "PAY-F1", "amount_krw": 18000, "payment_method_token": "tok", "mock": True}
    a = client.post("/internal/fep/authorizations", json=body, headers=h).json()
    assert a["status"] == "APPROVED" and a["approval_code"].startswith("TEST") and a["mock"] is True
    assert client.post("/internal/fep/authorizations", json=body, headers=h).json() == a  # 멱등
    assert client.get("/internal/fep/transactions/TXN-F1", headers=H).json() == a
    nf = client.get("/internal/fep/transactions/NOPE", headers=H)
    assert nf.status_code == 503 and nf.json()["error"]["code"] == "STATUS_LOOKUP_UNAVAILABLE"
    rf = {"refund_transaction_id": "RTXN-1", "original_transaction_id": "TXN-F1", "refund_id": "RF-1", "amount_krw": 18000, "mock": True}
    ok = client.post("/internal/fep/refunds", json=rf, headers={**H, "Idempotency-Key": "RTXN-1"})
    assert ok.json() == {"refund_transaction_id": "RTXN-1", "status": "REFUNDED", "mock": True}
    again = client.post("/internal/fep/refunds", json={**rf, "refund_transaction_id": "RTXN-2", "refund_id": "RF-2"},
                        headers={**H, "Idempotency-Key": "RTXN-2"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_REFUNDED"


def test_fep_unavailable_is_503(make):
    c = make(mock_mode="UNKNOWN")
    body = {"transaction_id": "TXN-U", "payment_id": "P", "amount_krw": 1000, "payment_method_token": "t", "mock": True}
    r = c.post("/internal/fep/authorizations", json=body, headers={**H, "Idempotency-Key": "TXN-U"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "FEP_UNAVAILABLE"
    assert c.get("/internal/fep/transactions/TXN-U", headers=H).json()["status"] == "APPROVED"  # 기관은 처리했다


# ---------------- E-11 ----------------
def test_e11_shape_filter_and_errors(client):
    j = client.get("/internal/payment-methods", params={"user_id": "U001"}, headers=H).json()
    assert j["user_id"] == "U001" and j["count"] == 3 and j["mock"] is True
    card = next(m for m in j["results"] if m["payment_method_id"] == "CARD001")
    assert set(card) == {"payment_method_id", "product_id", "type", "name", "monthly_performance_krw", "status", "updated_at"}
    local = next(m for m in j["results"] if m["payment_method_id"] == "LOCAL001")
    assert set(local) == {"payment_method_id", "product_id", "type", "name", "balance_krw", "status", "updated_at"}
    only = client.get("/internal/payment-methods", params={"user_id": "U001", "type": "LOCAL_CURRENCY"}, headers=H).json()
    assert [m["payment_method_id"] for m in only["results"]] == ["LOCAL001"] and only["count"] == 1
    r = client.get("/internal/payment-methods", params={"user_id": "NOBODY"}, headers=H)
    assert r.status_code == 404 and r.json()["error"]["code"] == "USER_NOT_FOUND"
    assert client.get("/internal/payment-methods", params={"user_id": "U001", "type": "X"}, headers=H).status_code == 400
