"""분할결제(로컬 확장, 팀 합의 전): 지역화폐 몫 + 나머지 카드 몫. 필드가 없으면 기존 동작 그대로."""

from tests.conftest import H, pay_body
from tests.test_payments import methods


def split_body(**over):
    # 전체 15,600원 = 지역화폐 8,000 + CARD001 7,600
    base = dict(
        payment_method_id="CARD001", charge_krw=15600,
        local_currency_payment_method_id="LOCAL001", local_currency_charge_krw=8000,
    )
    return pay_body(**{**base, **over})


def test_split_approved_updates_both_ledgers_and_shows_legs(client, pay):
    before = methods(client)
    r = pay(split_body())
    j = r.json()
    assert r.status_code == 200 and j["status"] == "APPROVED" and j["charge_krw"] == 15600
    assert j["payments"] == [
        {"payment_method_id": "LOCAL001", "charge_krw": 8000},
        {"payment_method_id": "CARD001", "charge_krw": 7600},
    ]
    after = methods(client)
    assert after["LOCAL001"]["balance_krw"] == before["LOCAL001"]["balance_krw"] - 8000
    assert after["CARD001"]["monthly_performance_krw"] == before["CARD001"]["monthly_performance_krw"] + 7600


def test_split_sends_local_fields_to_d06(pay, d):
    pay(split_body())
    sent, _ = d.calls[0]
    assert sent["local_currency_payment_method_id"] == "LOCAL001" and sent["local_currency_charge_krw"] == 8000


def test_non_split_still_sends_exactly_six_fields_and_no_legs(pay, d):
    r = pay()
    assert "payments" not in r.json()
    assert set(d.calls[0][0]) == {"execution_id", "approval_id", "quote_id", "proposal_hash", "charge_krw", "payment_method_id"}


def test_split_idempotent_and_conflict_when_split_differs(client, pay):
    body = split_body()
    a, b = pay(body), pay(body)
    assert a.json() == b.json()
    assert methods(client)["LOCAL001"]["balance_krw"] == 112000  # 한 번만 차감
    other = {**body, "local_currency_charge_krw": 5000}
    assert pay(other).status_code == 409


def test_split_insufficient_local_balance_fails_without_touching_card(client, pay):
    before = methods(client, "U002")
    r = pay(split_body(user_id="U002", payment_method_id="CARD101", local_currency_payment_method_id="LOCAL101"))
    assert r.json()["status"] == "FAILED" and r.json()["reason_codes"] == ["INSUFFICIENT_BALANCE"]
    assert methods(client, "U002") == before


def test_split_validation(pay):
    for over in (
        {"local_currency_charge_krw": None},  # 한쪽만
        {"local_currency_charge_krw": 15600},  # 몫이 전체 이상
        {"local_currency_payment_method_id": "CARD001"},  # 같은 수단
    ):
        body = {**split_body(), **over}
        if over.get("local_currency_charge_krw", 1) is None:
            del body["local_currency_charge_krw"]
        assert pay(body).status_code == 400


def test_split_refund_returns_local_first_then_card(client, pay):
    before = methods(client)
    pid = pay(split_body()).json()["payment_id"]
    h = {**H, "Idempotency-Key": "RF-1"}
    assert client.post(f"/payments/{pid}/refunds", json={"user_id": "U001", "amount_krw": 5000}, headers=h).status_code in (200, 202)
    mid = methods(client)
    assert mid["LOCAL001"]["balance_krw"] == before["LOCAL001"]["balance_krw"] - 3000  # 5,000 모두 지역화폐로
    h2 = {**H, "Idempotency-Key": "RF-2"}
    client.post(f"/payments/{pid}/refunds", json={"user_id": "U001", "amount_krw": 10600}, headers=h2)
    end = methods(client)
    assert end["LOCAL001"]["balance_krw"] == before["LOCAL001"]["balance_krw"]  # 지역화폐 8,000 전부 복구
    assert end["CARD001"]["monthly_performance_krw"] == before["CARD001"]["monthly_performance_krw"]  # 카드 7,600 복구
