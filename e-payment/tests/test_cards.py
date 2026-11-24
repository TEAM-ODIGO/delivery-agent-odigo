"""내 카드 추가 · 모의 마이데이터 (로컬 확장, 팀 합의 전)."""

from datetime import timedelta

from sqlalchemy.orm import sessionmaker

from e_payment.db import now
from e_payment.models import MyDataConsent
from tests.conftest import H
from tests.test_payments import methods


def consent(client, user="U002"):
    r = client.post("/internal/mock-mydata/consents", json={"user_id": user}, headers=H)
    assert r.status_code == 201
    return r.json()["consent_id"]


def mydata_cards(client, user="U002", consent_id=None):
    return client.get("/internal/mock-mydata/cards",
                      params={"user_id": user, "consent_id": consent_id or consent(client, user)}, headers=H)


def add(client, **body):
    return client.post("/internal/payment-methods", json={"user_id": "U002", **body}, headers=H)


def add_mydata(client, card_id, user="U002", consent_id=None):
    return add(client, user_id=user, source="MYDATA", consent_id=consent_id or consent(client, user),
               mydata_card_id=card_id)


# ---------------- 카탈로그 · 동의 · 카드 목록 ----------------
def test_catalog_lists_the_three_cards_with_benefit_rules(client):
    r = client.get("/internal/card-catalog", headers=H)
    assert [c["product_id"] for c in r.json()["supported"]] == ["PRD-CARD-A", "PRD-CARD-B", "PRD-CARD-C"]


def test_consent_returns_scopes_and_expiry(client):
    r = client.post("/internal/mock-mydata/consents", json={"user_id": "U002"}, headers=H)
    j = r.json()
    assert r.status_code == 201 and j["consent_id"].startswith("MDC") and j["mock"] is True
    assert j["scopes"] == ["CARD_LIST", "CARD_USAGE"] and j["expires_at"].endswith("+09:00")


def test_mydata_cards_flag_cards_the_user_already_has(client):
    got = {c["card_id"]: c for c in mydata_cards(client, "U002").json()["results"]}
    assert len(got) == 5
    assert got["MD-CARD-001"]["already_added"] is True  # U002는 요기요 신한카드 체크를 이미 가짐
    assert got["MD-CARD-002"]["already_added"] is False and got["MD-CARD-002"]["supported"] is True
    assert got["MD-CARD-004"]["supported"] is False
    assert all("product_id" not in c for c in got.values())  # 내부 상품 ID는 밖으로 내지 않는다
    u1 = {c["card_id"]: c for c in mydata_cards(client, "U001").json()["results"]}
    assert [u1[k]["already_added"] for k in ("MD-CARD-001", "MD-CARD-002", "MD-CARD-003")] == [True] * 3


def test_mydata_cards_reject_unknown_other_users_and_expired_consent(client, engine):
    assert mydata_cards(client, "U002", "MDC-NOPE").status_code == 404
    other = consent(client, "U001")
    r = mydata_cards(client, "U002", other)  # 남의 동의는 없는 것과 같다
    assert r.status_code == 404 and r.json()["error"]["code"] == "CONSENT_NOT_FOUND"
    cid = consent(client, "U002")
    with sessionmaker(bind=engine)() as s:
        s.get(MyDataConsent, cid).expires_at = now() - timedelta(seconds=1)
        s.commit()
    r = mydata_cards(client, "U002", cid)
    assert r.status_code == 410 and r.json()["error"]["code"] == "CONSENT_EXPIRED"


# ---------------- 모의 마이데이터로 추가 ----------------
def test_add_mydata_card_without_rules_appears_in_e11_with_ledger_fields(client):
    r = add_mydata(client, "MD-CARD-004")
    j = r.json()
    assert r.status_code == 201 and j["registration"] == {
        "source": "MYDATA", "issuer": "KB국민카드", "last4": "1209", "supported": False}
    pm = j["payment_method"]
    assert pm["payment_method_id"].startswith("CARD") and pm["type"] == "CARD" and pm["status"] == "ACTIVE"
    assert pm["name"] == "KB국민 노리2 체크카드 (1209)" and pm["product_id"] == "PRD-EXT-CARD"
    assert pm["monthly_performance_krw"] == 85000
    assert methods(client, "U002")[pm["payment_method_id"]] == pm  # E-11에도 같은 모양으로 나온다


def test_add_mydata_supported_card_uses_the_rule_product_and_its_performance(client):
    pm = add_mydata(client, "MD-CARD-002").json()["payment_method"]
    assert pm["product_id"] == "PRD-CARD-B" and pm["name"] == "신한카드 구독 좋아요"
    assert pm["monthly_performance_krw"] == 430000


def test_add_mydata_twice_or_a_product_already_held_is_409(client):
    assert add_mydata(client, "MD-CARD-004").status_code == 201
    again = add_mydata(client, "MD-CARD-004")
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_REGISTERED"
    held = add_mydata(client, "MD-CARD-001")  # U002가 이미 가진 상품
    assert held.status_code == 409
    assert len(methods(client, "U002")) == 3  # 처음 2장 + 1장


def test_add_mydata_needs_a_valid_consent_and_a_known_card(client):
    assert add_mydata(client, "MD-CARD-004", consent_id="MDC-NOPE").status_code == 404
    r = add_mydata(client, "MD-CARD-999")
    assert r.status_code == 404 and r.json()["error"]["code"] == "MYDATA_CARD_NOT_FOUND"
    assert len(methods(client, "U002")) == 2


# ---------------- 직접 입력 ----------------
def test_add_manual_supported_card(client):
    r = add(client, source="MANUAL", product_id="PRD-CARD-C")
    pm = r.json()["payment_method"]
    assert r.status_code == 201 and pm["product_id"] == "PRD-CARD-C" and pm["monthly_performance_krw"] == 0
    assert pm["name"] == "신한카드 Deep On Platinum+"
    assert add(client, source="MANUAL", product_id="PRD-CARD-C").status_code == 409


def test_add_manual_other_card_keeps_name_and_last4_and_allows_different_last4(client):
    r = add(client, source="MANUAL", name="삼성 iD ON 카드", issuer="삼성카드", last4="1234")
    assert r.status_code == 201 and r.json()["payment_method"]["name"] == "삼성 iD ON 카드 (1234)"
    assert r.json()["registration"]["supported"] is False
    assert add(client, source="MANUAL", name="삼성 iD ON 카드", last4="1234").status_code == 409
    assert add(client, source="MANUAL", name="삼성 iD ON 카드", last4="9999").status_code == 201


def test_add_validation(client):
    for body in (
        {"source": "MANUAL"},  # product_id도 name도 없음
        {"source": "MANUAL", "product_id": "PRD-CARD-A", "name": "x"},  # 둘 다
        {"source": "MANUAL", "name": "x", "last4": "12a4"},
        {"source": "MANUAL", "name": "x", "last4": "123456"},  # 카드 번호를 통째로 보내면 거부
        {"source": "MANUAL", "name": "x", "consent_id": "MDC1"},
        {"source": "MANUAL", "name": ""},
        {"source": "MYDATA", "consent_id": "MDC1"},  # 카드 ID 없음
        {"source": "MYDATA", "consent_id": "MDC1", "mydata_card_id": "MD-CARD-004", "name": "x"},
        {"source": "OTHER", "name": "x"},
        {"source": "MANUAL", "name": "x", "card_number": "1234123412341234"},  # 모르는 필드
    ):
        r = add(client, **body)
        assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR", body
    r = add(client, source="MANUAL", product_id="PRD-LOCAL-A")  # 카드가 아닌 상품
    assert r.status_code == 400
    assert len(methods(client, "U002")) == 2


def test_new_user_can_add_a_first_card(client):
    r = add(client, user_id="U777", source="MANUAL", name="내 체크카드")
    assert r.status_code == 201
    assert list(methods(client, "U777")) == [r.json()["payment_method"]["payment_method_id"]]


def test_card_routes_need_service_token(client):
    assert client.get("/internal/card-catalog").status_code == 401
    assert client.post("/internal/mock-mydata/consents", json={"user_id": "U002"}).status_code == 401
    assert client.post("/internal/payment-methods", json={"user_id": "U002", "source": "MANUAL", "name": "x"}).status_code == 401
    assert client.delete("/internal/payment-methods/CARD101", params={"user_id": "U002"}).status_code == 401


# ---------------- 결제에 쓰기 · 빼기 ----------------
def test_added_card_can_pay_and_its_performance_goes_up(client, pay):
    pm = add_mydata(client, "MD-CARD-002").json()["payment_method"]
    r = pay(user_id="U002", payment_method_id=pm["payment_method_id"], charge_krw=10000)
    assert r.json()["status"] == "APPROVED"
    assert methods(client, "U002")[pm["payment_method_id"]]["monthly_performance_krw"] == 440000


def test_remove_added_card(client):
    pid = add_mydata(client, "MD-CARD-004").json()["payment_method"]["payment_method_id"]
    r = client.delete(f"/internal/payment-methods/{pid}", params={"user_id": "U002"}, headers=H)
    assert r.status_code == 200 and r.json() == {"payment_method_id": pid, "removed": True}
    assert pid not in methods(client, "U002")
    assert add_mydata(client, "MD-CARD-004").status_code == 201  # 다시 추가할 수 있다


def test_remove_rejects_seed_other_users_and_cards_with_payments(client, pay):
    seed = client.delete("/internal/payment-methods/CARD101", params={"user_id": "U002"}, headers=H)
    assert seed.status_code == 409 and seed.json()["error"]["code"] == "NOT_REMOVABLE"
    pid = add_mydata(client, "MD-CARD-004").json()["payment_method"]["payment_method_id"]
    other = client.delete(f"/internal/payment-methods/{pid}", params={"user_id": "U001"}, headers=H)
    assert other.status_code == 404
    pay(user_id="U002", payment_method_id=pid, charge_krw=5000)
    used = client.delete(f"/internal/payment-methods/{pid}", params={"user_id": "U002"}, headers=H)
    assert used.status_code == 409 and used.json()["error"]["code"] == "HAS_PAYMENTS"
    assert pid in methods(client, "U002")


def test_existing_e11_shape_is_unchanged(client):
    assert set(methods(client)["CARD001"]) == {
        "payment_method_id", "product_id", "type", "name", "monthly_performance_krw", "status", "updated_at"}
    assert set(methods(client)["LOCAL001"]) == {
        "payment_method_id", "product_id", "type", "name", "balance_krw", "status", "updated_at"}


def test_registrations_list_only_cards_added_by_the_user(client):
    path = "/internal/card-registrations"
    assert client.get(path, params={"user_id": "U002"}, headers=H).json()["results"] == []
    a = add_mydata(client, "MD-CARD-004").json()["payment_method"]["payment_method_id"]
    b = add(client, source="MANUAL", name="내 카드", last4="0001").json()["payment_method"]["payment_method_id"]
    got = client.get(path, params={"user_id": "U002"}, headers=H).json()
    assert [(r["payment_method_id"], r["source"], r["last4"]) for r in got["results"]] == [
        (a, "MYDATA", "1209"), (b, "MANUAL", "0001")]
    assert client.get(path, params={"user_id": "U001"}, headers=H).json()["results"] == []
    assert client.get(path, params={"user_id": "U001"}).status_code == 401
