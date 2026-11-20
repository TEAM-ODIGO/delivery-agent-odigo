"""MVP 2 API: C-08 가맹점 매칭, C-10 규칙 초안 추출, C-11 규칙 활성화"""
from app.modules.benefit import matching, service, sources
from app.modules.benefit.domain import Restaurant

REC = {"request_id": "REQ001", "user_id": "U001", "restaurant_id": "STORE101",
       "menu_subtotal_krw": 18000, "delivery_fee_krw": 2000}
SYNC = {"source": "KOMSCO_MERCHANT", "region_codes": ["11680"], "mode": "FULL"}


# ---------- 매칭 규칙 ----------
def test_주소_정규화():
    assert (matching.normalize_address("서울특별시 강남구 테헤란로 123 (역삼동)")
            == matching.normalize_address("서울 강남구 테헤란로123"))


def test_상호_주소_좌표_중_두_개가_맞아야_매칭():
    r = Restaurant("S", None, None, None, name="가상치킨 강남점", address="서울 강남구 테헤란로 123", lat=37.5, lng=127.03)
    assert matching.is_match(r, {"name": "가상치킨강남점", "address": "서울 강남구 테헤란로 123"})            # 상호+주소
    assert matching.is_match(r, {"name": "가상치킨 강남점", "address": "다른주소", "lat": 37.5001, "lng": 127.0301})  # 상호+좌표
    assert not matching.is_match(r, {"name": "가상치킨 강남점", "address": "다른주소"})                      # 한 개만 일치
    assert not matching.is_match(r, {"name": "다른가게", "address": "서울 강남구 테헤란로 123", "lat": 37.6, "lng": 127.1})


# ---------- C-08 ----------
def test_c08_접수는_202_QUEUED_완료되면_건수가_남는다(client, admin):
    r = client.post("/benefit/local-currency/restaurants/sync", json=SYNC, headers=admin)
    assert r.status_code == 202 and r.json() == {"sync_id": "sync_001", "status": "QUEUED", "mock": True}
    assert service.get_sync("sync_001") == {"sync_id": "sync_001", "status": "DONE", "total": 2, "matched": 1, "no_match": 1}


def test_c08_매칭되면_true_못찾으면_false가_아니라_null(client, admin):
    client.post("/benefit/local-currency/restaurants/sync", json=SYNC, headers=admin)
    hit = client.get("/benefit/local-currency/restaurants/STORE101", headers=admin).json()
    miss = client.get("/benefit/local-currency/restaurants/STORE102", headers=admin).json()   # 원래 false(추정)
    other = client.get("/benefit/local-currency/restaurants/STORE103", headers=admin).json()  # 다른 지역은 그대로
    assert (hit["local_currency_merchant"], hit["local_currency_basis"]) == (True, "KOMSCO_MATCHED")
    assert hit["checked_at"].endswith("+09:00")
    assert (miss["local_currency_merchant"], miss["local_currency_basis"]) == (None, "NO_MATCH")
    assert other["local_currency_basis"] is None and other["checked_at"] is None


def test_c08_매칭_결과가_추천에_반영된다(client, admin, svc):
    client.post("/benefit/local-currency/restaurants/sync", json=SYNC, headers=admin)
    d = client.post("/benefit/recommendations", json=REC, headers=svc).json()
    local = next(o for o in d["options"] if o["payment_method_id"] == "LOCAL001")
    assert local["reasons"] == ["지역화폐 즉시할인 5%"]                     # 더 이상 '(추정)'이 아님
    d = client.post("/benefit/recommendations", json={**REC, "restaurant_id": "STORE102"}, headers=svc).json()
    assert {"payment_method_id": "LOCAL001", "reason_codes": ["LOCAL_CURRENCY_UNVERIFIED"]} in d["excluded"]


def test_c08_진행_중이면_409(client, admin):
    service._syncs["sync_x"] = {"sync_id": "sync_x", "status": "RUNNING", "request": {}}
    r = client.post("/benefit/local-currency/restaurants/sync", json=SYNC, headers=admin)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SYNC_IN_PROGRESS"


def test_c08_멱등키(client, admin):
    h = {**admin, "Idempotency-Key": "k1"}
    a = client.post("/benefit/local-currency/restaurants/sync", json=SYNC, headers=h).json()
    b = client.post("/benefit/local-currency/restaurants/sync", json=SYNC, headers=h).json()
    assert a["sync_id"] == b["sync_id"]
    r = client.post("/benefit/local-currency/restaurants/sync", json={**SYNC, "region_codes": ["11650"]}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_c08_요청값_오류와_권한(client, admin, svc):
    for bad in ({**SYNC, "source": "OTHER"}, {**SYNC, "region_codes": []}, {**SYNC, "mode": "X"}):
        assert client.post("/benefit/local-currency/restaurants/sync", json=bad, headers=admin).status_code == 400
    assert client.post("/benefit/local-currency/restaurants/sync", json=SYNC, headers=svc).status_code == 403


def test_c08_실패해도_FAILED로_남고_다음_동기화를_막지_않는다(client, admin):
    class Broken:
        def fetch(self, region_codes):
            raise RuntimeError("down")
    sources.set_merchant_source(Broken())
    client.post("/benefit/local-currency/restaurants/sync", json=SYNC, headers=admin)
    assert service.get_sync("sync_001")["status"] == "FAILED"
    sources.set_merchant_source(sources.MockMerchantSource())
    assert client.post("/benefit/local-currency/restaurants/sync", json=SYNC, headers=admin).status_code == 202


# ---------- C-10 ----------
DOC = {"product_id": "PRD-CARD-A", "document": {"document_id": "DOC-PRD-CARD-A-2026", "title": "가상카드 A 상품설명서",
                                                "text": "…배달앱 결제 시 2천원 즉시할인…"}}


class FakeLLM:
    def __init__(self, rules, warnings=()):
        self.rules, self.warnings = rules, list(warnings)

    def extract_benefit_rules(self, product_id, document):
        return {"rules": self.rules, "warnings": self.warnings, "llm": {"route": "LLM_API", "model": "fake"}}


EXTRACTED = [{"type": "INSTANT_DISCOUNT", "conditions": {"min_order_krw": 15000, "min_performance_krw": 300000},
              "effect": {"discount_krw": 3000}, "monthly_cap_krw": 10000,
              "source": {"page": 3, "evidence": "배달앱 결제 시 3천원 즉시할인"}}]


def test_c10_LLM이_연결되지_않으면_503(client, admin):
    r = client.post("/benefit/rules/extractions", json=DOC, headers=admin)
    assert r.status_code == 503 and r.json()["error"]["code"] == "INFERENCE_UNAVAILABLE"


def test_c10_초안은_DRAFT이고_계산에_쓰이지_않는다(client, admin, svc):
    sources.set_inference(FakeLLM(EXTRACTED, ["'전월 실적' 산정 기간 표현이 애매함: 검토 필요"]))
    d = client.post("/benefit/rules/extractions", json=DOC, headers=admin).json()
    rule = d["rules"][0]
    assert d["status"] == "DRAFT" and d["extraction_id"] == "EXT001" and d["warnings"]
    assert rule["rule_id"] == "DRAFT-0001" and rule["status"] == "DRAFT" and rule["product_id"] == "PRD-CARD-A"
    assert rule["source"] == {"document_id": "DOC-PRD-CARD-A-2026", "title": "가상카드 A 상품설명서",
                              "page": 3, "evidence": "배달앱 결제 시 3천원 즉시할인"}
    assert d["llm"] == {"route": "LLM_API", "model": "fake"}
    rec = client.post("/benefit/recommendations", json=REC, headers=svc).json()
    assert rec["options"][0]["instant_discount_krw"] == 2000            # 기존 ACTIVE 규칙 그대로
    assert client.get("/benefit/rules", headers=admin).json()["count"] == 4  # 카드 A·B·C + 지역화폐 규칙


def test_c10_혜택을_못_찾으면_422(client, admin):
    sources.set_inference(FakeLLM([]))
    r = client.post("/benefit/rules/extractions", json=DOC, headers=admin)
    assert r.status_code == 422 and r.json()["error"]["code"] == "EXTRACTION_FAILED"


def test_c10_LLM이_이상한_규칙을_주면_400(client, admin):
    sources.set_inference(FakeLLM([{"effect": {"discount_krw": -5}, "conditions": {}}]))
    r = client.post("/benefit/rules/extractions", json=DOC, headers=admin)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_RULE"
    assert "effect" in r.json()["error"]["details"]["fields"]


def test_c10_멱등키와_권한(client, admin, svc):
    sources.set_inference(FakeLLM(EXTRACTED))
    h = {**admin, "Idempotency-Key": "DOC-PRD-CARD-A-2026:extract"}
    a = client.post("/benefit/rules/extractions", json=DOC, headers=h).json()
    b = client.post("/benefit/rules/extractions", json=DOC, headers=h).json()
    assert a["extraction_id"] == b["extraction_id"]
    assert client.post("/benefit/rules/extractions", json=DOC, headers=svc).status_code == 403


# ---------- C-11 ----------
NEW_RULE = {"rule_id": "RULE-CARD-A-DELIVERY", "product_id": "PRD-CARD-A", "type": "INSTANT_DISCOUNT",
            "conditions": {"min_order_krw": 15000, "min_performance_krw": 300000}, "effect": {"discount_krw": 3000},
            "monthly_cap_krw": 10000, "status": "ACTIVE",
            "source": {"document_id": "DOC-PRD-CARD-A-2026", "title": "가상카드 A 상품설명서", "page": 3,
                       "evidence": "배달앱 결제 시 3천원 즉시할인"}}
ACTIVATE = {"base_version": "BEN-V1", "rules": [NEW_RULE], "deactivate_rule_ids": []}
CALC = {"request_id": "REQ001", "user_id": "U001", "restaurant_id": "STORE101", "menu_subtotal_krw": 18000,
        "delivery_fee_krw": 2000, "payment_method_id": "CARD001"}


def test_c11_새_버전이_발급되고_이전_버전은_남는다(client, admin, svc):
    r = client.post("/benefit/rule-versions", json=ACTIVATE, headers=admin)
    assert r.status_code == 201
    d = r.json()
    assert (d["benefit_rule_version"], d["previous_version"], d["activated_rule_ids"]) == \
        ("BEN-V2", "BEN-V1", ["RULE-CARD-A-DELIVERY"])
    rec = client.post("/benefit/recommendations", json=REC, headers=svc).json()
    assert rec["benefit_rule_version"] == "BEN-V2" and rec["options"][0]["instant_discount_krw"] == 3000
    old = client.post("/benefit/calculations", json={**CALC, "benefit_rule_version": "BEN-V1"}, headers=svc).json()
    new = client.post("/benefit/calculations", json={**CALC, "benefit_rule_version": "BEN-V2"}, headers=svc).json()
    assert (old["instant_discount_krw"], new["instant_discount_krw"]) == (2000, 3000)    # 승인 때 버전으로 재검증 가능
    detail = client.get("/benefit/rules/RULE-CARD-A-DELIVERY", headers=admin).json()
    assert detail["source"]["evidence"] == "배달앱 결제 시 3천원 즉시할인"


def test_c11_base_version이_다르면_409(client, admin):
    client.post("/benefit/rule-versions", json=ACTIVATE, headers=admin)
    r = client.post("/benefit/rule-versions", json=ACTIVATE, headers=admin)         # 이미 BEN-V2
    assert r.status_code == 409 and r.json()["error"]["code"] == "RULE_VERSION_CONFLICT"
    assert r.json()["error"]["details"] == {"current_version": "BEN-V2"}


def test_c11_형식이_틀리면_400이고_버전은_그대로(client, admin):
    bad = {**ACTIVATE, "rules": [{**NEW_RULE, "effect": {"discount_percent": 150}}]}
    r = client.post("/benefit/rule-versions", json=bad, headers=admin)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_RULE"
    assert client.get("/benefit/rules", headers=admin).json()["results"][0]["benefit_rule_version"] == "BEN-V1"
    draft = {**ACTIVATE, "rules": [{**NEW_RULE, "status": "DRAFT"}]}                  # DRAFT는 활성화할 수 없다
    assert client.post("/benefit/rule-versions", json=draft, headers=admin).status_code == 400


def test_c11_규칙을_비활성화하면_계산에서_빠진다(client, admin, svc):
    body = {**ACTIVATE, "deactivate_rule_ids": ["RULE-LOCAL-A-DISCOUNT"]}
    client.post("/benefit/rule-versions", json=body, headers=admin)
    r = client.post("/benefit/calculations", json={**CALC, "payment_method_id": "LOCAL001"}, headers=svc)
    assert r.status_code == 404 and r.json()["error"]["code"] == "RULE_NOT_FOUND"
    inactive = client.get("/benefit/rules?status=INACTIVE", headers=admin).json()
    assert [x["rule_id"] for x in inactive["results"]] == ["RULE-LOCAL-A-DISCOUNT"]
    unknown = client.post("/benefit/rule-versions", json={**ACTIVATE, "base_version": "BEN-V2",
                          "deactivate_rule_ids": ["NOPE"]}, headers=admin)
    assert unknown.status_code == 400


def test_c11_멱등키(client, admin):
    h = {**admin, "Idempotency-Key": "BEN-V1:k1"}
    a = client.post("/benefit/rule-versions", json=ACTIVATE, headers=h).json()
    b = client.post("/benefit/rule-versions", json=ACTIVATE, headers=h).json()
    assert a == b and b["benefit_rule_version"] == "BEN-V2"                           # 새 버전이 또 만들어지지 않음
    other = {**ACTIVATE, "deactivate_rule_ids": ["RULE-LOCAL-A-DISCOUNT"]}
    r = client.post("/benefit/rule-versions", json=other, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
