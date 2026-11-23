from datetime import timedelta

import httpx
import pytest

from app.modules.benefit import ledger, service
from app.modules.benefit.errors import AppError
from app.modules.benefit.ledger import HttpLedger

BODY = {"request_id": "REQ001", "user_id": "U001", "restaurant_id": "STORE101",
        "menu_subtotal_krw": 18000, "delivery_fee_krw": 2000, "catalog_version": "CAT-V1",
        "payment_requirements": {"local_currency_only": False}, "candidate_payment_method_ids": []}


# ---------- C-01 ----------
def test_c01_명세_예시와_같은_결과(client, svc):
    r = client.post("/benefit/recommendations", json=BODY, headers=svc)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ACTIVE" and d["benefit_rule_version"] == "BEN-V1"
    assert d["expires_at"].endswith("+09:00")
    o1, o2 = d["options"]
    assert (o1["rank"], o1["payment_method_id"], o1["instant_discount_krw"], o1["charge_krw"]) == (1, "CARD001", 2000, 18000)
    assert o1["applied_benefits"] == [{"rule_id": "RULE-CARD-A-DELIVERY", "type": "INSTANT_DISCOUNT", "amount_krw": 2000}]
    assert (o2["payment_method_id"], o2["instant_discount_krw"], o2["charge_krw"]) == ("LOCAL001", 1000, 19000)
    assert o2["reasons"] == ["지역화폐 즉시할인 5% (가맹점 여부는 추정)"]
    assert d["excluded"] == [{"payment_method_id": "CARD002", "reason_codes": ["PERFORMANCE_NOT_MET"]}]


def test_c01_위임_범위_밖_결제수단은_추천하지_않는다(client, svc):
    r = client.post("/benefit/recommendations", json={**BODY, "candidate_payment_method_ids": ["LOCAL001"]}, headers=svc)
    assert [o["payment_method_id"] for o in r.json()["options"]] == ["LOCAL001"]


def test_c01_비가맹점과_확인불가_식당(client, svc):
    for store, code in (("STORE102", "LOCAL_CURRENCY_NOT_ACCEPTED"), ("STORE103", "LOCAL_CURRENCY_UNVERIFIED")):
        d = client.post("/benefit/recommendations", json={**BODY, "restaurant_id": store}, headers=svc).json()
        assert {"payment_method_id": "LOCAL001", "reason_codes": [code]} in d["excluded"]
        assert all(o["payment_method_id"] != "LOCAL001" for o in d["options"])


def test_c01_지역화폐만_조건이면_카드는_제외(client, svc):
    body = {**BODY, "payment_requirements": {"local_currency_only": True}}
    d = client.post("/benefit/recommendations", json=body, headers=svc).json()
    assert [o["payment_method_id"] for o in d["options"]] == ["LOCAL001"]


def test_c01_추천_가능한_수단이_없으면_200과_빈_options(client, svc):
    d = client.post("/benefit/recommendations", json={**BODY, "user_id": "U002"}, headers=svc)
    assert d.status_code == 200
    d = d.json()
    assert d["options"] == [] and d["reason_codes"] == ["NO_ELIGIBLE_PAYMENT_METHOD"]
    codes = {e["payment_method_id"]: e["reason_codes"] for e in d["excluded"]}
    assert codes == {"CARD101": ["PERFORMANCE_NOT_MET"], "LOCAL101": ["LOCAL_CURRENCY_INSUFFICIENT_BALANCE"]}


def test_c01_같은_request_id로_재추천하면_이전_추천은_SUPERSEDED(client, svc):
    first = client.post("/benefit/recommendations", json=BODY, headers=svc).json()
    second = client.post("/benefit/recommendations", json=BODY, headers=svc).json()
    assert first["recommendation_id"] != second["recommendation_id"]
    old = client.get(f"/benefit/recommendations/{first['recommendation_id']}", headers=svc).json()
    assert old["status"] == "SUPERSEDED" and old["options"] == []
    assert client.get(f"/benefit/recommendations/{second['recommendation_id']}", headers=svc).json()["status"] == "ACTIVE"


def test_c01_멱등키_같은_내용은_같은_결과_다른_내용은_409(client, svc):
    h = {**svc, "Idempotency-Key": "REQ001:c01:1"}
    a = client.post("/benefit/recommendations", json=BODY, headers=h).json()
    b = client.post("/benefit/recommendations", json=BODY, headers=h).json()
    assert a["recommendation_id"] == b["recommendation_id"]
    r = client.post("/benefit/recommendations", json={**BODY, "menu_subtotal_krw": 25000}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_c01_요청값_오류는_400_공통_형식(client, svc):
    r = client.post("/benefit/recommendations", json={**BODY, "menu_subtotal_krw": -1},
                    headers={**svc, "X-Request-ID": "REQ001"})   # 팀 규칙: request_id는 헤더에서 읽는다
    assert r.status_code == 400
    d = r.json()
    assert d["error"]["code"] == "VALIDATION_ERROR"
    assert any("menu_subtotal_krw" in f["loc"] for f in d["error"]["details"]["fields"])  # 팀 공통 오류 형식
    assert d["request_id"] == "REQ001"


def test_c01_ID_형식_위반은_400(client, svc):
    r = client.post("/benefit/recommendations", json={**BODY, "user_id": "U 001!"}, headers=svc)
    assert r.status_code == 400


def test_c01_없는_사용자와_식당은_404(client, svc):
    r = client.post("/benefit/recommendations", json={**BODY, "user_id": "NOBODY"}, headers=svc)
    assert r.status_code == 404 and r.json()["error"]["code"] == "USER_NOT_FOUND"
    r = client.post("/benefit/recommendations", json={**BODY, "restaurant_id": "NOSHOP"}, headers=svc)
    assert r.status_code == 404 and r.json()["error"]["code"] == "RESTAURANT_NOT_FOUND"


# ---------- 인증 ----------
def test_토큰이_없거나_틀리면_401(client):
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        r = client.post("/benefit/recommendations", json=BODY, headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED_SERVICE"


def test_관리자_API에_서비스_토큰은_403(client, svc, admin):
    assert client.get("/benefit/rules", headers=svc).status_code == 403
    assert client.get("/benefit/rules", headers=admin).status_code == 200


def test_health는_인증_없음(client):
    r = client.get("/benefit/health")
    assert r.status_code == 200 and r.json() == {"service": "benefit", "status": "UP"}


# ---------- C-04 ----------
def test_c04_없는_추천은_404(client, svc):
    r = client.get("/benefit/recommendations/none", headers=svc)
    assert r.status_code == 404 and r.json()["error"]["code"] == "RECOMMENDATION_NOT_FOUND"


def test_c04_유효시간이_지나면_EXPIRED(client, svc, monkeypatch):
    rec = client.post("/benefit/recommendations", json=BODY, headers=svc).json()
    monkeypatch.setattr(service, "now", lambda: service.datetime.now(service.KST) + timedelta(minutes=11))
    d = client.get(f"/benefit/recommendations/{rec['recommendation_id']}", headers=svc).json()
    assert d["status"] == "EXPIRED" and d["options"] == []


# ---------- C-02 ----------
def test_c02_보유_결제수단_조회(client, svc):
    d = client.get("/benefit/users/U001/payment-methods", headers=svc).json()
    assert d["count"] == 3
    card, local = d["results"][0], d["results"][2]
    assert card["monthly_performance_krw"] == 350000
    assert card["monthly_benefit_remaining_krw"] == 4000  # 월 한도 6,000원 - 이미 받은 2,000원
    assert local["balance_krw"] == 120000 and local["monthly_benefit_remaining_krw"] == 15000
    assert card["product_id"] == "PRD-CARD-A"
    only_local = client.get("/benefit/users/U001/payment-methods?type=LOCAL_CURRENCY", headers=svc).json()
    assert [m["payment_method_id"] for m in only_local["results"]] == ["LOCAL001"]


def test_c02_없는_사용자는_404(client, svc):
    r = client.get("/benefit/users/NOBODY/payment-methods", headers=svc)
    assert r.status_code == 404 and r.json()["error"]["code"] == "USER_NOT_FOUND"


# ---------- C-05 ----------
CALC = {"request_id": "REQ001", "user_id": "U001", "restaurant_id": "STORE101", "menu_subtotal_krw": 18000,
        "delivery_fee_krw": 2000, "payment_method_id": "CARD001", "benefit_rule_version": "BEN-V1"}


def test_c05_혜택_재계산(client, svc):
    d = client.post("/benefit/calculations", json=CALC, headers=svc).json()
    assert d["eligible"] is True and d["instant_discount_krw"] == 2000 and d["charge_krw"] == 18000
    assert d["reason_codes"] == []


def test_c05_적용_불가면_할인_0원과_사유(client, svc):
    d = client.post("/benefit/calculations", json={**CALC, "payment_method_id": "CARD002"}, headers=svc).json()
    assert d["eligible"] is False and d["instant_discount_krw"] == 0 and d["charge_krw"] == 20000
    assert d["reason_codes"] == ["PERFORMANCE_NOT_MET"]


def test_c05_규칙_버전과_결제수단이_없으면_404(client, svc):
    r = client.post("/benefit/calculations", json={**CALC, "benefit_rule_version": "BEN-V9"}, headers=svc)
    assert r.status_code == 404 and r.json()["error"]["code"] == "RULE_NOT_FOUND"
    r = client.post("/benefit/calculations", json={**CALC, "payment_method_id": "NOPE"}, headers=svc)
    assert r.status_code == 404 and r.json()["error"]["code"] == "PAYMENT_METHOD_NOT_FOUND"


# ---------- C-03 · C-06 · C-07 ----------
def test_관리자_조회_API(client, admin):
    d = client.get("/benefit/local-currency/restaurants/STORE101", headers=admin).json()
    assert d["local_currency_merchant"] is True and d["local_currency_basis"] == "ASSUMED_FROM_VOUCHER"
    rules = client.get("/benefit/rules?product_id=PRD-CARD-A", headers=admin).json()
    assert rules["count"] == 1
    assert rules["results"][0]["conditions"] == {"min_performance_krw": 200000}
    assert "evidence" not in rules["results"][0]["source"]
    detail = client.get("/benefit/rules/RULE-CARD-A-DELIVERY", headers=admin).json()
    assert detail["source"]["evidence"].startswith("배달앱(요기요) 결제 시")
    assert client.get("/benefit/rules/NOPE", headers=admin).status_code == 404


# ---------- E-11 연동 ----------
def _ledger(handler):
    return HttpLedger("http://e.test", "tok", transport=httpx.MockTransport(handler))


def test_e11_응답을_결제수단으로_변환():
    def handler(request):
        assert request.url.params["user_id"] == "U001" and request.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json={"user_id": "U001", "count": 1, "results": [
            {"payment_method_id": "CARD001", "product_id": "PRD-CARD-A", "type": "CARD",
             "name": "요기요 신한카드 체크",
             "monthly_performance_krw": 350000, "status": "ACTIVE"}]})
    m = _ledger(handler).get_payment_methods("U001", "REQ001")[0]
    assert (m.payment_method_id, m.product_id, m.monthly_performance_krw) == ("CARD001", "PRD-CARD-A", 350000)


def test_e11_404는_USER_NOT_FOUND_연결실패는_503():
    with pytest.raises(AppError) as e:
        _ledger(lambda r: httpx.Response(404)).get_payment_methods("X")
    assert (e.value.status, e.value.code) == (404, "USER_NOT_FOUND")

    def boom(request):
        raise httpx.ConnectError("down")
    with pytest.raises(AppError) as e:
        _ledger(boom).get_payment_methods("X")
    assert (e.value.status, e.value.code) == (503, "DEPENDENCY_UNAVAILABLE")


def test_추천은_E가_내려주는_원장을_쓴다(client, svc):
    def handler(request):
        return httpx.Response(200, json={"results": [
            {"payment_method_id": "LOCAL999", "product_id": "PRD-LOCAL-A", "type": "LOCAL_CURRENCY",
             "balance_krw": 50000, "status": "ACTIVE"}]})
    ledger.set_ledger(_ledger(handler))
    d = client.post("/benefit/recommendations", json=BODY, headers=svc).json()
    assert [o["payment_method_id"] for o in d["options"]] == ["LOCAL999"]


def test_E_장애면_503(client, svc):
    def boom(request):
        raise httpx.ConnectError("down")
    ledger.set_ledger(_ledger(boom))
    r = client.post("/benefit/recommendations", json=BODY, headers=svc)
    assert r.status_code == 503 and r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_명세_예시_응답_형태(client, svc, admin):
    """API 명세서(0572ac87) 예시와 같은 필드·문구인지 확인"""
    body = {"request_id": "REQ001", "user_id": "U001", "restaurant_id": "STORE101", "menu_subtotal_krw": 18000,
            "delivery_fee_krw": 2000, "catalog_version": "CAT-V1", "payment_requirements": {"local_currency_only": False},
            "candidate_payment_method_ids": ["CARD001", "LOCAL001"]}
    d = client.post("/benefit/recommendations", json=body, headers=svc).json()
    assert d["mock"] is True and list(d["options"][0])[0] == "rank"
    assert d["options"][0]["reasons"] == ["배달앱 결제 즉시할인 15%"]
    assert d["options"][1]["reasons"] == ["지역화폐 즉시할인 5% (가맹점 여부는 추정)"]
    assert d["options"][1]["instant_discount_krw"] == 1000 and d["options"][1]["charge_krw"] == 19000
    assert client.get("/benefit/local-currency/restaurants/STORE101", headers=admin).json() == {
        "restaurant_id": "STORE101", "accepts_livelihood_voucher": True, "local_currency_merchant": True,
        "local_currency_basis": "ASSUMED_FROM_VOUCHER", "checked_at": None, "mock": True}
    assert "mock" not in client.get("/benefit/health").json()


# ---------- 최소주문금액 사전 확인 (로컬 확장, 명세 밖) ----------
def test_최소주문금액_사전_확인(client):
    from app.modules.benefit import facade

    # 최소 주문이 있는 카드는 Deep On Platinum+(CARD003, 2만원)뿐이라 E 원장으로 확인한다.
    def handler(request):
        return httpx.Response(200, json={"results": [
            {"payment_method_id": "CARD003", "product_id": "PRD-CARD-C", "type": "CARD",
             "name": "신한카드 Deep On Platinum+", "monthly_performance_krw": 350000,
             "status": "ACTIVE"},
            {"payment_method_id": "LOCAL001", "product_id": "PRD-LOCAL-A", "type": "LOCAL_CURRENCY",
             "balance_krw": 120000, "status": "ACTIVE"}]})
    ledger.set_ledger(_ledger(handler))
    body = {
        "user_id": "U001", "request_id": "REQ001",
        "candidate_payment_method_ids": ["CARD003", "LOCAL001"],
        "orders": [
            {"restaurant_id": "STORE102", "order_krw": 13000},  # 지역화폐 불가, 카드 최소 2만원
            {"restaurant_id": "STORE102", "order_krw": 20000},
            {"restaurant_id": "STORE101", "order_krw": 13000},  # 지역화폐 가맹 → 결제 가능
            {"restaurant_id": "NOWHERE", "order_krw": 1000},  # 모르는 식당은 판단하지 않음
        ],
    }
    got = [r["required_order_krw"] for r in facade.check_min_order(body)["results"]]
    assert got == [20000, None, None, None]


# ---------- 실제 신한카드 3장의 규칙 값 ----------
def test_신한카드_3장_규칙_값():
    from app.modules.benefit import seed
    from app.modules.benefit.domain import PaymentMethod, calculate

    rules = {r.product_id: r for r in seed.RULE_VERSIONS["BEN-V1"]}
    shop = seed.RESTAURANTS["STORE101"]
    a = PaymentMethod("CARD001", "PRD-CARD-A", "CARD", monthly_performance_krw=350000)
    b = PaymentMethod("CARD002", "PRD-CARD-B", "CARD", monthly_performance_krw=550000)
    c = PaymentMethod("CARD003", "PRD-CARD-C", "CARD", monthly_performance_krw=350000)

    # 요기요 신한카드 체크: 15%, 건당 최대 2,000원, 최소 주문 없음
    assert calculate(a, rules["PRD-CARD-A"], 10000, shop).instant_discount_krw == 1500
    assert calculate(a, rules["PRD-CARD-A"], 30000, shop).instant_discount_krw == 2000
    # 신한카드 구독 좋아요: 10%, 월 한도 2,000원 (남은 한도 500원이면 500원)
    assert calculate(b, rules["PRD-CARD-B"], 15000, shop).instant_discount_krw == 1500
    assert calculate(b, rules["PRD-CARD-B"], 15000, shop, used_krw=1500).instant_discount_krw == 500
    # Deep On Platinum+: 20%, 건당 최대 10,000원, 최소 주문 20,000원
    assert calculate(c, rules["PRD-CARD-C"], 19999, shop).reason_codes == ["MIN_ORDER_NOT_MET"]
    assert calculate(c, rules["PRD-CARD-C"], 20000, shop).instant_discount_krw == 4000
    assert calculate(c, rules["PRD-CARD-C"], 60000, shop).instant_discount_krw == 10000
    # 전월 실적 미달이면 제외 (A 20만원, B 40만원, C 30만원)
    assert calculate(PaymentMethod("X", "PRD-CARD-A", "CARD", monthly_performance_krw=199999),
                     rules["PRD-CARD-A"], 20000, shop).reason_codes == ["PERFORMANCE_NOT_MET"]
    assert calculate(PaymentMethod("X", "PRD-CARD-B", "CARD", monthly_performance_krw=399999),
                     rules["PRD-CARD-B"], 20000, shop).reason_codes == ["PERFORMANCE_NOT_MET"]
    assert calculate(PaymentMethod("X", "PRD-CARD-C", "CARD", monthly_performance_krw=299999),
                     rules["PRD-CARD-C"], 20000, shop).reason_codes == ["PERFORMANCE_NOT_MET"]


def test_서울페이_이름():
    from app.modules.benefit import products
    names = {m.payment_method_id: m.name for methods in ledger.MOCK_USERS.values() for m in methods}
    assert names["LOCAL001"] == names["LOCAL101"] == "서울페이(서울사랑상품권)"
    assert products.CARD_PRODUCTS["PRD-LOCAL-A"]["product_name"] == "서울페이(서울사랑상품권)"
