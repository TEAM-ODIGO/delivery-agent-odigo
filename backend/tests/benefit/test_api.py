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
    # 서울페이는 충전식이라 할인이 없다 (명세 예시의 5% 즉시할인은 2026-11-24에 뺐다)
    assert (o2["payment_method_id"], o2["instant_discount_krw"], o2["charge_krw"]) == ("LOCAL001", 0, 20000)
    assert o2["applied_benefits"] == []
    assert o2["reasons"] == ["충전한 잔액에서 사용한 만큼 차감 (가맹점 여부는 추정)"]
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
    assert local["balance_krw"] == 120000 and local["monthly_benefit_remaining_krw"] is None  # 충전식: 월 할인 한도 없음
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
    assert d["options"][1]["reasons"] == ["충전한 잔액에서 사용한 만큼 차감 (가맹점 여부는 추정)"]
    assert d["options"][1]["instant_discount_krw"] == 0 and d["options"][1]["charge_krw"] == 20000
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


# ---------- 혜택 규칙이 없는 카드 (내 카드 추가, 로컬 확장) ----------
class _LedgerWithExternalCard:
    def get_payment_methods(self, user_id, request_id=None):
        from app.modules.benefit.domain import PaymentMethod

        return [
            PaymentMethod("CARD001", "PRD-CARD-A", "CARD", "요기요 신한카드 체크", monthly_performance_krw=350000),
            PaymentMethod("CARD900", "PRD-EXT-CARD", "CARD", "KB국민 노리2 체크카드 (1209)",
                          monthly_performance_krw=85000),
        ]


def test_c02_혜택_규칙_유무를_알려_준다(client, svc):
    ledger.set_ledger(_LedgerWithExternalCard())
    got = {m["payment_method_id"]: m for m in client.get("/benefit/users/U001/payment-methods", headers=svc).json()["results"]}
    assert got["CARD001"]["has_benefit_rule"] is True
    assert got["CARD900"]["has_benefit_rule"] is False and got["CARD900"]["monthly_benefit_remaining_krw"] is None


def test_c01_규칙_없는_카드는_후보에서_빠지고_사유가_남는다(client, svc):
    ledger.set_ledger(_LedgerWithExternalCard())
    rec = client.post("/benefit/recommendations", json=BODY, headers=svc).json()
    assert [o["payment_method_id"] for o in rec["options"]] == ["CARD001"]
    assert {"payment_method_id": "CARD900", "reason_codes": ["RULE_NOT_FOUND"]} in rec["excluded"]


def test_c05_규칙_없는_카드는_계속_404(client, svc):
    ledger.set_ledger(_LedgerWithExternalCard())
    body = {k: BODY[k] for k in ("request_id", "user_id", "restaurant_id", "menu_subtotal_krw", "delivery_fee_krw") if k in BODY}
    r = client.post("/benefit/calculations", json={**body, "payment_method_id": "CARD900",
                                                    "benefit_rule_version": "BEN-V1"}, headers=svc)
    assert r.status_code == 404 and r.json()["error"]["code"] == "RULE_NOT_FOUND"


# ---------- KB국민 노리2(PRD-CARD-D) · 현대카드 ZERO Up(PRD-CARD-E) (내 카드 추가, 로컬 확장) ----------
class _LedgerWithKbAndHyundai:
    def __init__(self, kb_performance=250000):
        self.kb_performance = kb_performance

    def get_payment_methods(self, user_id, request_id=None):
        from app.modules.benefit.domain import PaymentMethod

        return [
            PaymentMethod("CARD201", "PRD-CARD-D", "CARD", "KB국민 노리2 체크카드 (KB Pay)",
                          monthly_performance_krw=self.kb_performance),
            PaymentMethod("CARD202", "PRD-CARD-E", "CARD", "현대카드 ZERO Up (포인트형)",
                          monthly_performance_krw=150000),
        ]


def test_노리2는_건당_1만원_이상이면_1000원_할인되고_ZERO_Up은_추천에서_빠진다(client, svc):
    ledger.set_ledger(_LedgerWithKbAndHyundai())
    rec = client.post("/benefit/recommendations", json=BODY, headers=svc).json()
    [kb] = rec["options"]
    assert (kb["payment_method_id"], kb["instant_discount_krw"], kb["charge_krw"]) == ("CARD201", 1000, 19000)
    assert kb["applied_benefits"] == [{"rule_id": "RULE-CARD-D-DELIVERY", "type": "INSTANT_DISCOUNT", "amount_krw": 1000}]
    assert rec["excluded"] == [{"payment_method_id": "CARD202", "reason_codes": ["RULE_NOT_FOUND"]}]


def test_노리2는_주문이_1만원_미만이면_할인이_없고_전월실적_20만원_미만이면_제외된다(client, svc):
    ledger.set_ledger(_LedgerWithKbAndHyundai())
    small = {**BODY, "menu_subtotal_krw": 7000, "delivery_fee_krw": 2000}  # 합계 9,000원
    [kb] = client.post("/benefit/recommendations", json=small, headers=svc).json()["options"]
    assert kb["instant_discount_krw"] == 0 and kb["charge_krw"] == 9000
    ledger.set_ledger(_LedgerWithKbAndHyundai(kb_performance=199999))
    rec = client.post("/benefit/recommendations", json=BODY, headers=svc).json()
    assert rec["options"] == []
    assert {"payment_method_id": "CARD201", "reason_codes": ["PERFORMANCE_NOT_MET"]} in rec["excluded"]


def test_노리2_월_한도_1000원이_C02에_나온다(client, svc):
    ledger.set_ledger(_LedgerWithKbAndHyundai())
    got = {m["payment_method_id"]: m for m in client.get("/benefit/users/U001/payment-methods", headers=svc).json()["results"]}
    assert got["CARD201"]["has_benefit_rule"] is True and got["CARD201"]["monthly_benefit_remaining_krw"] == 1000
    assert got["CARD202"]["has_benefit_rule"] is False and got["CARD202"]["monthly_benefit_remaining_krw"] is None


def test_카드_혜택_화면은_두_카드의_혜택_정보를_보여_준다():
    from app.modules.benefit import facade

    ledger.set_ledger(_LedgerWithKbAndHyundai())
    kb = facade.card_dashboard("U001", "CARD201")
    assert kb["has_benefit_rule"] is True and kb["checked_at"] == "2026-11-24"
    assert kb["product"]["headline"] == "요기요 건당 1만원 이상 결제 시 1,000원 할인"
    assert [b["title"] for b in kb["product"]["benefits"]][0] == "배달앱"
    assert kb["monthly_benefit"] == {"cap_krw": 1000, "used_krw": 0, "remaining_krw": 1000, "min_performance_krw": 200000}
    assert kb["performance"]["eligible"] is True
    zero = facade.card_dashboard("U001", "CARD202")
    assert zero["has_benefit_rule"] is False and zero["product"]["product_name"] == "현대카드 ZERO Up (포인트형)"
    assert [b["rate"] for b in zero["product"]["benefits"]] == ["2.4% 적립", "1.2% 적립"]
    assert zero["monthly_benefit"]["cap_krw"] is None and zero["performance"]["eligible"] is False
    assert zero["performance"]["tiers"] == [] and zero["performance"]["message"] is None


def test_신한카드_혜택_화면은_규칙_있음으로_나온다():
    from app.modules.benefit import facade

    ledger.set_ledger(_LedgerWithExternalCard())
    got = facade.card_dashboard("U001", "CARD001")
    assert got["has_benefit_rule"] is True and got["checked_at"] == "2026-11-22"
    unknown = facade.card_dashboard("U001", "CARD900")  # 정보를 모르는 카드
    assert unknown["has_benefit_rule"] is False and unknown["checked_at"] is None


# ---------- 요기요 삼성카드(PRD-CARD-F) (내 카드 추가, 로컬 확장) ----------
class _LedgerWithYogiyoSamsung:
    def __init__(self, performance=420000):
        self.performance = performance

    def get_payment_methods(self, user_id, request_id=None):
        from app.modules.benefit.domain import PaymentMethod

        return [PaymentMethod("CARD301", "PRD-CARD-F", "CARD", "요기요 삼성카드",
                              monthly_performance_krw=self.performance)]


def test_요기요_삼성카드는_전월_30만원_이상이면_10퍼센트_할인(client, svc):
    ledger.set_ledger(_LedgerWithYogiyoSamsung())
    [opt] = client.post("/benefit/recommendations", json=BODY, headers=svc).json()["options"]  # 합계 2만원
    assert (opt["payment_method_id"], opt["instant_discount_krw"], opt["charge_krw"]) == ("CARD301", 2000, 18000)
    assert opt["applied_benefits"][0]["rule_id"] == "RULE-CARD-F-DELIVERY"


def test_요기요_삼성카드는_월_한도_6000원까지만_할인하고_30만원_미만이면_제외된다(client, svc):
    ledger.set_ledger(_LedgerWithYogiyoSamsung())
    big = {**BODY, "menu_subtotal_krw": 98000, "delivery_fee_krw": 2000}  # 10만원의 10%는 1만원
    [opt] = client.post("/benefit/recommendations", json=big, headers=svc).json()["options"]
    assert opt["instant_discount_krw"] == 6000
    ledger.set_ledger(_LedgerWithYogiyoSamsung(performance=299999))
    rec = client.post("/benefit/recommendations", json=BODY, headers=svc).json()
    assert rec["options"] == []
    assert {"payment_method_id": "CARD301", "reason_codes": ["PERFORMANCE_NOT_MET"]} in rec["excluded"]


def test_요기요_삼성카드_혜택_화면(client):
    from app.modules.benefit import facade

    ledger.set_ledger(_LedgerWithYogiyoSamsung())
    d = facade.card_dashboard("U001", "CARD301")
    assert d["has_benefit_rule"] is True and d["product"]["product_name"] == "요기요 삼성카드"
    assert [b["rate"] for b in d["product"]["benefits"]] == ["10% 결제일할인", "1% 결제일할인", "5% 결제일할인"]
    assert d["monthly_benefit"] == {"cap_krw": 6000, "used_krw": 0, "remaining_krw": 6000, "min_performance_krw": 300000}
    perf = d["performance"]
    assert perf["eligible"] is True and [t["cap_krw"] for t in perf["tiers"]] == [6000, 12000, 20000]
    assert perf["current"]["min_krw"] == 300000 and perf["next"]["min_krw"] == 600000 and perf["to_next_krw"] == 180000


# ---------- 서울페이: 할인 없는 충전식 잔액 ----------
def test_서울페이는_할인_없이_쓴_만큼_차감된다(client, svc):
    r = client.post("/benefit/calculations", json={**CALC, "payment_method_id": "LOCAL001"}, headers=svc).json()
    assert r["eligible"] is True and r["instant_discount_krw"] == 0 and r["charge_krw"] == 20000
    assert r["applied_benefits"] == [] and r["reason_codes"] == []


def test_서울페이_혜택_화면은_잔액만_보여_준다():
    from app.modules.benefit import facade

    d = facade.card_dashboard("U001", "LOCAL001")
    assert d["has_benefit_rule"] is True and d["balance_krw"] == 120000 and d["performance"] is None
    assert d["monthly_benefit"]["cap_krw"] is None and d["monthly_benefit"]["remaining_krw"] is None
    assert d["product"]["benefits"][0]["rate"] == "쓴 만큼 차감"
