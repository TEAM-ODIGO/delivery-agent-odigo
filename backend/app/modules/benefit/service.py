"""C 모듈의 업무 로직. O·B·D는 HTTP 대신 이 함수들을 같은 서버 안에서 직접 호출할 수 있다.
오류는 AppError로 던진다. (HTTP로 나갈 때는 errors.py가 공통 형식으로 변환)"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dataclasses import replace

from . import domain, matching, seed, sources
from .errors import AppError
from .ledger import get_ledger
from .schemas import CalculationRequest, ExtractionRequest, RecommendRequest, RuleVersionRequest, SyncRequest

KST = ZoneInfo("Asia/Seoul")
RECOMMENDATION_TTL = timedelta(minutes=10)

_recommendations: dict[str, dict] = {}     # MVP 1은 메모리 저장 (c_recommendations 테이블로 교체 예정)
_latest_by_request: dict[str, str] = {}
_idempotency: dict[str, tuple[str, dict]] = {}


def now() -> datetime:
    return datetime.now(KST)


_syncs: dict[str, dict] = {}               # c_local_currency_sync 테이블 대신 메모리
_extractions: dict[str, dict] = {}


def reset_state() -> None:
    _recommendations.clear(); _latest_by_request.clear(); _idempotency.clear()
    _syncs.clear(); _extractions.clear()
    seed.reset()


# ---------- 조회 도우미 ----------
def get_restaurant(restaurant_id: str) -> domain.Restaurant:
    r = seed.RESTAURANTS.get(restaurant_id)
    if r is None:
        raise AppError(404, "RESTAURANT_NOT_FOUND", "음식점을 찾을 수 없습니다.")
    return r


def rules_by_product(version: str | None = None) -> tuple[str, dict[str, domain.BenefitRule]]:
    version = version or seed.CURRENT_VERSION
    if version not in seed.RULE_VERSIONS:
        raise AppError(404, "RULE_NOT_FOUND", "혜택 규칙 버전을 찾을 수 없습니다.", {"benefit_rule_version": version})
    return version, {r.product_id: r for r in seed.RULE_VERSIONS[version] if r.status == "ACTIVE"}


def _rule_dict(rule: domain.BenefitRule, version: str, with_evidence: bool = False) -> dict:
    conditions = {k: v for k, v in (("min_order_krw", rule.min_order_krw),
                                    ("min_performance_krw", rule.min_performance_krw)) if v}
    effect = {k: v for k, v in (("discount_krw", rule.discount_krw), ("discount_percent", rule.discount_percent),
                                ("max_discount_krw", rule.max_discount_krw)) if v}
    source = dict(seed.SOURCES.get(rule.rule_id, {}))
    if source and not with_evidence:
        source.pop("evidence", None)
    d = {"rule_id": rule.rule_id, "product_id": rule.product_id, "type": rule.type, "conditions": conditions,
         "effect": effect, "monthly_cap_krw": rule.monthly_cap_krw, "benefit_rule_version": version,
         "status": rule.status}
    if source:
        d["source"] = source
    return d


# ---------- C-01 결제수단 추천 ----------
def recommend_payment_methods(req: RecommendRequest, idempotency_key: str | None = None) -> dict:
    fingerprint = hashlib.sha256(json.dumps(req.model_dump(), sort_keys=True).encode()).hexdigest()
    if idempotency_key and idempotency_key in _idempotency:
        saved_fp, saved = _idempotency[idempotency_key]
        if saved_fp != fingerprint:
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "같은 Idempotency-Key로 다른 내용이 요청되었습니다.")
        return view_recommendation(saved["recommendation_id"])

    methods = get_ledger().get_payment_methods(req.user_id, req.request_id)
    if req.candidate_payment_method_ids:
        methods = [m for m in methods if m.payment_method_id in req.candidate_payment_method_ids]
    restaurant = get_restaurant(req.restaurant_id)
    version, rules = rules_by_product()
    total = req.menu_subtotal_krw + req.delivery_fee_krw
    if req.local_currency_amount_krw is not None:
        # 분할결제(로컬 확장): 지역화폐로 낼 금액은 주문 금액보다 작아야 한다 (같으면 지역화폐 단독 결제)
        if req.local_currency_amount_krw >= total:
            raise AppError(400, "VALIDATION_ERROR", "지역화폐로 낼 금액은 주문 금액보다 작아야 합니다.",
                           {"fields": [{"loc": "body.local_currency_amount_krw", "reason": "must_be_less_than_total"}]})
        options, excluded = domain.recommend_split(
            methods, rules, total, req.local_currency_amount_krw, restaurant,
            seed.BENEFIT_USED_KRW, req.payment_requirements.local_currency_only)
    else:
        options, excluded = domain.recommend(
            methods, rules, total, restaurant,
            seed.BENEFIT_USED_KRW, req.payment_requirements.local_currency_only)

    previous = _latest_by_request.get(req.request_id)
    if previous and _recommendations[previous]["status"] == "ACTIVE":
        _recommendations[previous]["status"] = "SUPERSEDED"      # 같은 request_id로 재추천하면 이전 추천은 대체됨
    rec = {"recommendation_id": str(uuid.uuid4()), "request_id": req.request_id, "status": "ACTIVE",
           "benefit_rule_version": version, "expires_at": now() + RECOMMENDATION_TTL,
           "options": options, "excluded": excluded,
           "reason_codes": [] if options else ["NO_ELIGIBLE_PAYMENT_METHOD"]}
    _recommendations[rec["recommendation_id"]] = rec
    _latest_by_request[req.request_id] = rec["recommendation_id"]
    result = view_recommendation(rec["recommendation_id"])
    if idempotency_key:
        _idempotency[idempotency_key] = (fingerprint, result)
    return result


# ---------- 최소주문금액 사전 확인 (로컬 확장, 명세 밖) ----------
def check_min_order(
    user_id: str, request_id: str | None, orders: list[dict],
    payment_method_ids: list[str] | None = None, local_currency_only: bool = False,
) -> dict:
    """후보 주문마다 '주문 금액이 모자라 결제수단을 못 쓰는지' 확인한다. 상태를 바꾸지 않는다.

    orders: [{"restaurant_id", "order_krw"}] (order_krw = 메뉴 합계 + 배달비)
    결과 required_order_krw: 못 쓰는 경우 필요한 최소주문금액, 쓸 수 있거나 다른 사유면 None.
    """
    methods = get_ledger().get_payment_methods(user_id, request_id)
    if payment_method_ids:
        methods = [m for m in methods if m.payment_method_id in payment_method_ids]
    _, rules = rules_by_product()
    results = []
    for o in orders:
        required = None
        try:
            restaurant = get_restaurant(o["restaurant_id"])
        except AppError:  # 모르는 식당은 판단하지 않는다 (이후 단계가 그대로 검증)
            restaurant = None
        if restaurant is not None:
            required = domain.min_order_shortfall(
                methods, rules, o["order_krw"], restaurant,
                seed.BENEFIT_USED_KRW, local_currency_only,
            )
        results.append({"restaurant_id": o["restaurant_id"], "order_krw": o["order_krw"],
                        "required_order_krw": required})
    return {"results": results}


# ---------- C-04 추천 결과 조회 ----------
def view_recommendation(recommendation_id: str) -> dict:
    rec = _recommendations.get(recommendation_id)
    if rec is None:
        raise AppError(404, "RECOMMENDATION_NOT_FOUND", "추천 결과를 찾을 수 없습니다.")
    status = rec["status"]
    if status == "ACTIVE" and now() > rec["expires_at"]:
        status = "EXPIRED"
    out = {"recommendation_id": rec["recommendation_id"], "request_id": rec["request_id"], "status": status,
           "benefit_rule_version": rec["benefit_rule_version"],
           "expires_at": rec["expires_at"].isoformat(timespec="seconds"),
           "options": rec["options"] if status == "ACTIVE" else [],     # 만료·대체된 추천은 선택지를 비운다
           "excluded": rec["excluded"]}
    if rec["reason_codes"]:
        out["reason_codes"] = rec["reason_codes"]
    return out


# ---------- C-02 보유 결제수단 조회 ----------
def list_payment_methods(user_id: str, type: str | None = None, page: int = 1, page_size: int = 20,
                         request_id: str | None = None) -> dict:
    methods = get_ledger().get_payment_methods(user_id, request_id)
    if type:
        methods = [m for m in methods if m.type == type]
    _, rules = rules_by_product()
    results = []
    for m in methods:
        item = {"payment_method_id": m.payment_method_id, "product_id": m.product_id, "type": m.type, "name": m.name}
        item["monthly_performance_krw" if m.type == "CARD" else "balance_krw"] = (
            m.monthly_performance_krw if m.type == "CARD" else m.balance_krw)
        item["monthly_benefit_remaining_krw"] = domain.remaining_monthly_krw(
            rules.get(m.product_id), seed.BENEFIT_USED_KRW.get(m.payment_method_id, 0))
        item["status"] = m.status
        results.append(item)
    start = (page - 1) * page_size
    return {"count": len(results), "results": results[start:start + page_size]}


# ---------- C-05 혜택 재계산 ----------
def calculate_benefit(req: CalculationRequest) -> dict:
    methods = get_ledger().get_payment_methods(req.user_id, req.request_id)
    method = next((m for m in methods if m.payment_method_id == req.payment_method_id), None)
    if method is None:
        raise AppError(404, "PAYMENT_METHOD_NOT_FOUND", "결제수단을 찾을 수 없습니다.")
    restaurant = get_restaurant(req.restaurant_id)
    version, rules = rules_by_product(req.benefit_rule_version)
    rule = rules.get(method.product_id)
    if rule is None:
        raise AppError(404, "RULE_NOT_FOUND", "혜택 규칙을 찾을 수 없습니다.", {"product_id": method.product_id})
    total = req.menu_subtotal_krw + req.delivery_fee_krw
    if req.share_krw is not None and req.share_krw > total:
        raise AppError(400, "VALIDATION_ERROR", "결제수단이 낼 몫은 주문 금액을 넘을 수 없습니다.",
                       {"fields": [{"loc": "body.share_krw", "reason": "exceeds_total"}]})
    c = domain.calculate(method, rule, total, restaurant,
                         seed.BENEFIT_USED_KRW.get(method.payment_method_id, 0), share_krw=req.share_krw)
    return {"payment_method_id": c.payment_method_id, "eligible": c.eligible, "benefit_rule_version": version,
            "instant_discount_krw": c.instant_discount_krw, "charge_krw": c.charge_krw,
            "applied_benefits": c.applied_benefits, "reason_codes": c.reason_codes}


# ---------- C-03 · C-06 · C-07 관리자 점검용 ----------
def get_local_currency_restaurant(restaurant_id: str) -> dict:
    r = get_restaurant(restaurant_id)
    return {"restaurant_id": r.restaurant_id, "accepts_livelihood_voucher": r.accepts_livelihood_voucher,
            "local_currency_merchant": r.local_currency_merchant, "local_currency_basis": r.local_currency_basis,
            "checked_at": r.checked_at}


def list_rules(product_id: str | None = None, type: str | None = None, status: str = "ACTIVE",
               benefit_rule_version: str | None = None, page: int = 1, page_size: int = 20) -> dict:
    version = benefit_rule_version or seed.CURRENT_VERSION
    if version not in seed.RULE_VERSIONS:
        raise AppError(404, "RULE_NOT_FOUND", "혜택 규칙 버전을 찾을 수 없습니다.", {"benefit_rule_version": version})
    products = set(product_id.split(",")) if product_id else None
    rules = [r for r in seed.RULE_VERSIONS[version] if r.status == status
             and (products is None or r.product_id in products)
             and (type is None or seed.PRODUCT_TYPES.get(r.product_id) == type)]
    start = (page - 1) * page_size
    return {"count": len(rules), "results": [_rule_dict(r, version) for r in rules[start:start + page_size]]}


def get_rule(rule_id: str) -> dict:
    for version, rules in seed.RULE_VERSIONS.items():
        for r in rules:
            if r.rule_id == rule_id and version == seed.CURRENT_VERSION:
                return _rule_dict(r, version, with_evidence=True)
    raise AppError(404, "RULE_NOT_FOUND", "혜택 규칙을 찾을 수 없습니다.")


def _fingerprint(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _replay(key: str | None, data) -> dict | None:
    """같은 키 + 같은 내용이면 저장된 결과를, 같은 키 + 다른 내용이면 409를 돌려준다."""
    if not key or key not in _idempotency:
        return None
    saved_fp, saved = _idempotency[key]
    if saved_fp != _fingerprint(data):
        raise AppError(409, "IDEMPOTENCY_CONFLICT", "같은 Idempotency-Key로 다른 내용이 요청되었습니다.")
    return saved


# ---------- C-08 지역화폐 가맹점 매칭 (MVP 2) ----------
def start_sync(req: SyncRequest, idempotency_key: str | None = None) -> dict:
    """동기화 요청을 접수한다(QUEUED). 실제 작업은 run_sync(sync_id)로 실행한다. (라우터가 백그라운드로 실행)"""
    data = req.model_dump()
    saved = _replay(idempotency_key, data)
    if saved:
        return saved
    if any(r["status"] in ("QUEUED", "RUNNING") for r in _syncs.values()):
        raise AppError(409, "SYNC_IN_PROGRESS", "이미 갱신이 진행 중입니다.")
    sync_id = f"sync_{len(_syncs) + 1:03d}"
    _syncs[sync_id] = {"sync_id": sync_id, "status": "QUEUED", "request": data}
    result = {"sync_id": sync_id, "status": "QUEUED"}
    if idempotency_key:
        _idempotency[idempotency_key] = (_fingerprint(data), result)
    return result


def run_sync(sync_id: str) -> dict:
    """공공데이터 가맹점과 식당을 매칭해 restaurant.local_currency_* 열을 갱신한다.
    매칭되면 true(KOMSCO_MATCHED), 없으면 null(NO_MATCH). 공공데이터에 없다고 false로 바꾸지 않는다."""
    rec = _syncs[sync_id]
    rec["status"] = "RUNNING"
    try:
        regions = rec["request"]["region_codes"]
        merchants = sources.get_merchant_source().fetch(regions)
        total = matched = no_match = 0
        checked_at = now().isoformat(timespec="seconds")
        for rid, r in list(seed.RESTAURANTS.items()):
            if r.region_code not in regions:
                continue
            total += 1
            if any(matching.is_match(r, m) for m in merchants):
                seed.RESTAURANTS[rid] = replace(r, local_currency_merchant=True,
                                                local_currency_basis="KOMSCO_MATCHED", checked_at=checked_at)
                matched += 1
            else:
                seed.RESTAURANTS[rid] = replace(r, local_currency_merchant=None,
                                                local_currency_basis="NO_MATCH", checked_at=checked_at)
                no_match += 1
        rec.update(status="DONE", total=total, matched=matched, no_match=no_match)
    except Exception as e:                                   # 실패해도 다음 동기화를 막지 않도록 상태를 남긴다
        rec.update(status="FAILED", error=type(e).__name__)
    return get_sync(sync_id)


def get_sync(sync_id: str) -> dict:
    rec = _syncs.get(sync_id)
    if rec is None:
        raise AppError(404, "SYNC_NOT_FOUND", "동기화 기록을 찾을 수 없습니다.")
    return {k: v for k, v in rec.items() if k != "request"}


# ---------- C-10 설명서에서 혜택 규칙 초안 추출 (MVP 2) ----------
def _check_rule(rule: dict, *, allow_status: tuple[str, ...]) -> dict:
    """규칙 하나의 형식을 검사한다. 문제가 있으면 필드별 사유를 details에 담아 400 INVALID_RULE."""
    problems = {}

    def nonneg(value) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    for key in ("rule_id", "product_id"):
        if not isinstance(rule.get(key), str) or not rule.get(key):
            problems[key] = "필수 문자열입니다."
    if rule.get("type") != "INSTANT_DISCOUNT":
        problems["type"] = "현재는 INSTANT_DISCOUNT만 지원합니다."
    conditions, effect = rule.get("conditions", {}), rule.get("effect")
    if not isinstance(conditions, dict) or not all(nonneg(v) for v in conditions.values()):
        problems["conditions"] = "조건 값은 0 이상의 정수여야 합니다."
    if not isinstance(effect, dict) or not ({"discount_krw", "discount_percent"} & set(effect)):
        problems["effect"] = "discount_krw 또는 discount_percent가 필요합니다."
    elif not all(nonneg(v) for v in effect.values()) or effect.get("discount_percent", 0) > 100:
        problems["effect"] = "할인 값은 0 이상의 정수(할인율은 100 이하)여야 합니다."
    cap = rule.get("monthly_cap_krw")
    if cap is not None and not nonneg(cap):
        problems["monthly_cap_krw"] = "0 이상의 정수 또는 null이어야 합니다."
    if rule.get("status", "DRAFT") not in allow_status:
        problems["status"] = f"{'/'.join(allow_status)} 중 하나여야 합니다."
    if problems:
        raise AppError(400, "INVALID_RULE", "혜택 규칙의 조건·효과 형식이 올바르지 않습니다.",
                       {"rule_id": rule.get("rule_id"), "fields": problems})
    return rule


def extract_benefit_rules(req: ExtractionRequest, idempotency_key: str | None = None) -> dict:
    """상품 설명서 텍스트에서 혜택 규칙 초안(DRAFT)을 만든다. LLM은 A-09를 통해 호출한다.
    초안은 계산에 쓰이지 않으며, 사람이 확인한 뒤 C-11로 활성화해야 한다."""
    data = req.model_dump()
    saved = _replay(idempotency_key, data)
    if saved:
        return saved
    raw = sources.get_inference().extract_benefit_rules(req.product_id, data["document"])
    if not raw.get("rules"):
        raise AppError(422, "EXTRACTION_FAILED", "설명서에서 혜택을 찾지 못했습니다.")
    rules = []
    for i, r in enumerate(raw["rules"], 1):
        r = {**r, "rule_id": r.get("rule_id") or f"DRAFT-{i:04d}", "product_id": req.product_id, "status": "DRAFT"}
        r.setdefault("type", "INSTANT_DISCOUNT")
        r.setdefault("conditions", {})
        r["source"] = {"document_id": req.document.document_id, "title": req.document.title,
                       **(r.get("source") or {})}
        rules.append(_check_rule(r, allow_status=("DRAFT",)))
    extraction_id = f"EXT{len(_extractions) + 1:03d}"
    result = {"extraction_id": extraction_id, "product_id": req.product_id, "status": "DRAFT", "rules": rules,
              "warnings": raw.get("warnings", []), "llm": raw.get("llm", {})}
    _extractions[extraction_id] = result
    if idempotency_key:
        _idempotency[idempotency_key] = (_fingerprint(data), result)
    return result


# ---------- C-11 검토한 혜택 규칙 활성화 (MVP 2) ----------
def activate_rules(req: RuleVersionRequest, idempotency_key: str | None = None) -> dict:
    """검토를 마친 규칙을 ACTIVE로 등록하고 새 benefit_rule_version을 발급한다. 이전 버전은 지우지 않는다."""
    data = req.model_dump()
    saved = _replay(idempotency_key, data)
    if saved:
        return saved
    if req.base_version != seed.CURRENT_VERSION:
        raise AppError(409, "RULE_VERSION_CONFLICT", "규칙 버전이 이미 바뀌었습니다. 최신 버전으로 다시 검토해 주세요.",
                       {"current_version": seed.CURRENT_VERSION})
    for r in req.rules:
        _check_rule(r, allow_status=("ACTIVE",))
    current = {r.rule_id: r for r in seed.RULE_VERSIONS[seed.CURRENT_VERSION]}
    unknown = [i for i in req.deactivate_rule_ids if i not in current]
    if unknown:
        raise AppError(400, "INVALID_RULE", "비활성화할 규칙을 찾을 수 없습니다.", {"unknown_rule_ids": unknown})

    new_rules = dict(current)
    for rule_id in req.deactivate_rule_ids:
        new_rules[rule_id] = replace(new_rules[rule_id], status="INACTIVE")
    for r in req.rules:
        eff, cond = r["effect"], r["conditions"]
        new_rules[r["rule_id"]] = domain.BenefitRule(
            r["rule_id"], r["product_id"], r["type"], eff.get("discount_percent", 0), eff.get("discount_krw", 0),
            cond.get("min_order_krw", 0), cond.get("min_performance_krw", 0), eff.get("max_discount_krw"),
            r.get("monthly_cap_krw"), "ACTIVE")
        if r.get("source"):
            seed.SOURCES[r["rule_id"]] = r["source"]
        seed.PRODUCT_TYPES.setdefault(r["product_id"], "CARD")
    previous = seed.CURRENT_VERSION
    version = f"BEN-V{len(seed.RULE_VERSIONS) + 1}"
    seed.RULE_VERSIONS[version] = list(new_rules.values())
    seed.CURRENT_VERSION = version
    result = {"benefit_rule_version": version, "previous_version": previous,
              "activated_rule_ids": [r["rule_id"] for r in req.rules],
              "effective_at": now().isoformat(timespec="seconds")}
    if idempotency_key:
        _idempotency[idempotency_key] = (_fingerprint(data), result)
    return result
