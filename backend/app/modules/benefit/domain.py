"""혜택 계산 핵심 로직. 프레임워크와 무관한 순수 Python이며, 금액은 모두 정수(원)로 계산한다.
같은 입력과 같은 규칙 버전이면 항상 같은 결과가 나온다. (LLM 미사용)"""
from dataclasses import dataclass


@dataclass(frozen=True)
class PaymentMethod:
    payment_method_id: str
    product_id: str
    type: str                          # "CARD" 또는 "LOCAL_CURRENCY"
    name: str = ""
    balance_krw: int = 0               # 지역화폐 잔액
    monthly_performance_krw: int = 0   # 카드 당월 실적
    status: str = "ACTIVE"


@dataclass(frozen=True)
class BenefitRule:
    rule_id: str
    product_id: str
    type: str = "INSTANT_DISCOUNT"
    discount_percent: int = 0          # 정률 할인 (5 = 5%)
    discount_krw: int = 0              # 정액 할인
    min_order_krw: int = 0
    min_performance_krw: int = 0
    max_discount_krw: int | None = None  # 1회 최대 할인
    monthly_cap_krw: int | None = None   # 월 할인 한도
    status: str = "ACTIVE"


@dataclass(frozen=True)
class Restaurant:
    restaurant_id: str
    accepts_livelihood_voucher: bool | None
    local_currency_merchant: bool | None     # true / false / None(확인 불가)
    local_currency_basis: str | None
    checked_at: str | None = None
    # C-08 매칭에 쓰는 식당 정보 (수집 DB의 restaurant 열)
    name: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    region_code: str | None = None


@dataclass(frozen=True)
class Calc:
    """결제수단 하나의 계산 결과. eligible=False이면 할인 0원이고 reason_codes에 이유가 담긴다."""
    payment_method_id: str
    eligible: bool
    instant_discount_krw: int
    charge_krw: int
    applied_benefits: list
    reason_codes: list
    reasons: list


def remaining_monthly_krw(rule: BenefitRule | None, used_krw: int) -> int | None:
    if rule is None or rule.monthly_cap_krw is None:
        return None
    return max(rule.monthly_cap_krw - used_krw, 0)


def _reason_text(method: PaymentMethod, rule: BenefitRule, discount: int, estimated: bool) -> str:
    label = "지역화폐" if method.type == "LOCAL_CURRENCY" else "배달앱 결제"
    detail = f"{rule.discount_percent}%" if rule.discount_percent and not rule.discount_krw else f"{discount:,}원"
    text = f"{label} 즉시할인 {detail}"
    return text + " (가맹점 여부는 추정)" if estimated and method.type == "LOCAL_CURRENCY" else text


def calculate(method: PaymentMethod, rule: BenefitRule | None, order_krw: int, restaurant: Restaurant,
              used_krw: int = 0, local_currency_only: bool = False, share_krw: int | None = None) -> Calc:
    """결제수단 하나의 혜택을 계산한다. 결제금액 = 주문금액(메뉴 합계 + 배달비) - 즉시할인.

    분할결제(로컬 확장): share_krw가 있으면 이 수단이 낼 몫만 계산한다.
    조건(최소 주문금액·실적)은 주문 전체 금액으로 판단하고, 할인은 몫에만 적용한다. 결제금액 = 몫 - 할인.
    """
    share = order_krw if share_krw is None else share_krw

    def fail(code: str) -> Calc:
        return Calc(method.payment_method_id, False, 0, share, [], [code], [])

    if method.status != "ACTIVE":
        return fail("PAYMENT_METHOD_NOT_ACTIVE")
    if local_currency_only and method.type != "LOCAL_CURRENCY":
        return fail("PAYMENT_REQUIREMENT_NOT_MET")
    if method.type == "LOCAL_CURRENCY":
        if restaurant.local_currency_merchant is False:
            return fail("LOCAL_CURRENCY_NOT_ACCEPTED")
        if restaurant.local_currency_merchant is None:
            return fail("LOCAL_CURRENCY_UNVERIFIED")
    if rule is None:
        return fail("RULE_NOT_FOUND")
    if method.type == "CARD" and method.monthly_performance_krw < rule.min_performance_krw:
        return fail("PERFORMANCE_NOT_MET")
    if order_krw < rule.min_order_krw:
        return fail("MIN_ORDER_NOT_MET")

    discount = rule.discount_krw + share * rule.discount_percent // 100
    if rule.max_discount_krw is not None:
        discount = min(discount, rule.max_discount_krw)
    remaining = remaining_monthly_krw(rule, used_krw)
    if remaining is not None:
        if remaining == 0:
            return fail("BENEFIT_LIMIT_EXCEEDED")
        discount = min(discount, remaining)
    discount = min(discount, share)

    estimated = restaurant.local_currency_basis == "ASSUMED_FROM_VOUCHER"
    return Calc(method.payment_method_id, True, discount, share - discount,
                [{"rule_id": rule.rule_id, "type": rule.type, "amount_krw": discount}] if discount else [],
                [], [_reason_text(method, rule, discount, estimated)] if discount else [])


# 추천에서 "후보 제외(excluded)"로 분류하는 사유. 나머지(MIN_ORDER_NOT_MET)는 할인 0원으로 후보에 남는다.
# RULE_NOT_FOUND(혜택 규칙이 없는 카드, 예: 내 카드 추가로 넣은 다른 카드사 카드)도 제외한다 (로컬 확장, 팀 합의 전):
# 규칙이 없으면 C-05 재계산이 404라 D·B가 결제를 막으므로, 처음부터 선택지로 보여 주지 않는다.
EXCLUDE_CODES = {"PAYMENT_METHOD_NOT_ACTIVE", "PAYMENT_REQUIREMENT_NOT_MET", "LOCAL_CURRENCY_NOT_ACCEPTED",
                 "LOCAL_CURRENCY_UNVERIFIED", "PERFORMANCE_NOT_MET", "RULE_NOT_FOUND"}


def recommend(methods, rules_by_product, order_krw, restaurant, used_by_method=None, local_currency_only=False):
    """후보를 결제금액이 낮은 순(동률이면 결제수단 ID 순)으로 정렬해 (options, excluded)를 반환한다."""
    used_by_method = used_by_method or {}
    options, excluded = [], []
    for m in methods:
        c = calculate(m, rules_by_product.get(m.product_id), order_krw, restaurant,
                      used_by_method.get(m.payment_method_id, 0), local_currency_only)
        code = c.reason_codes[0] if c.reason_codes else None
        if code in EXCLUDE_CODES:
            excluded.append({"payment_method_id": m.payment_method_id, "reason_codes": c.reason_codes})
            continue
        if m.type == "LOCAL_CURRENCY" and m.balance_krw < c.charge_krw:
            excluded.append({"payment_method_id": m.payment_method_id,
                             "reason_codes": ["LOCAL_CURRENCY_INSUFFICIENT_BALANCE"]})
            continue
        reasons = c.reasons or (["최소 주문금액 미달로 혜택 없음"] if code == "MIN_ORDER_NOT_MET"
                                else ["적용 가능한 혜택 없음"])
        options.append({"payment_method_id": m.payment_method_id, "instant_discount_krw": c.instant_discount_krw,
                        "charge_krw": c.charge_krw, "applied_benefits": c.applied_benefits, "reasons": reasons})
    options.sort(key=lambda o: (o["charge_krw"], o["payment_method_id"]))
    options = [{"rank": rank, **o} for rank, o in enumerate(options, 1)]
    return options, excluded


def min_order_shortfall(methods, rules_by_product, order_krw, restaurant,
                        used_by_method=None, local_currency_only=False) -> int | None:
    """주문 금액이 모자라 어떤 결제수단도 못 쓰는지 확인한다 (읽기 전용, 추천 기록을 남기지 않는다).

    B-05는 eligible=false인 수단을 거절하므로, 쓸 수 있는 수단이 하나도 없는데
    최소주문금액만 채우면 쓸 수 있는 수단이 있으면 그 중 가장 낮은 요구 금액을 돌려준다.
    쓸 수 있는 수단이 있거나, 금액 말고 다른 사유(실적·가맹점 등)로 못 쓰면 None.
    """
    used_by_method = used_by_method or {}
    required = []
    for m in methods:
        rule = rules_by_product.get(m.product_id)
        c = calculate(m, rule, order_krw, restaurant, used_by_method.get(m.payment_method_id, 0),
                      local_currency_only)
        if c.eligible:
            return None
        if rule is not None and c.reason_codes == ["MIN_ORDER_NOT_MET"]:
            required.append(rule.min_order_krw)
    return min(required) if required else None


def recommend_split(methods, rules_by_product, order_krw, local_amount_krw, restaurant,
                    used_by_method=None, local_currency_only=False):
    """분할결제(로컬 확장): 지역화폐로 local_amount_krw를 내고, 나머지는 카드로 낼 때의 추천.

    - 지역화폐 몫: 지역화폐 수단 중 쓸 수 있는 것(가맹점·잔액 충족)에서 결제금액이 가장 낮은 하나
    - 나머지 몫: 카드마다 계산해 옵션 하나씩. 옵션의 payment_method_id는 카드이고, 합계는 두 몫의 합이다.
    반환 (options, excluded). 옵션에는 split(지역화폐분·나머지분)이 붙는다.
    """
    used_by_method = used_by_method or {}
    remainder_krw = order_krw - local_amount_krw
    excluded = []

    local_calcs = []
    for m in (m for m in methods if m.type == "LOCAL_CURRENCY"):
        c = calculate(m, rules_by_product.get(m.product_id), order_krw, restaurant,
                      used_by_method.get(m.payment_method_id, 0), False, local_amount_krw)
        code = c.reason_codes[0] if c.reason_codes else None
        if code in EXCLUDE_CODES:
            excluded.append({"payment_method_id": m.payment_method_id, "reason_codes": c.reason_codes})
        elif m.balance_krw < c.charge_krw:
            excluded.append({"payment_method_id": m.payment_method_id,
                             "reason_codes": ["LOCAL_CURRENCY_INSUFFICIENT_BALANCE"]})
        else:
            local_calcs.append((m, c))
    if not local_calcs:
        return [], excluded
    local_m, local_c = min(local_calcs, key=lambda mc: (mc[1].charge_krw, mc[0].payment_method_id))

    options = []
    for m in (m for m in methods if m.type != "LOCAL_CURRENCY"):
        c = calculate(m, rules_by_product.get(m.product_id), order_krw, restaurant,
                      used_by_method.get(m.payment_method_id, 0), local_currency_only, remainder_krw)
        code = c.reason_codes[0] if c.reason_codes else None
        if code in EXCLUDE_CODES:
            excluded.append({"payment_method_id": m.payment_method_id, "reason_codes": c.reason_codes})
            continue
        reasons = [*local_c.reasons, *c.reasons] or ["적용 가능한 혜택 없음"]
        options.append({
            "payment_method_id": m.payment_method_id,
            "instant_discount_krw": local_c.instant_discount_krw + c.instant_discount_krw,
            "charge_krw": local_c.charge_krw + c.charge_krw,
            "applied_benefits": [*local_c.applied_benefits, *c.applied_benefits],
            "reasons": reasons,
            "split": {
                "local_currency": {"payment_method_id": local_m.payment_method_id,
                                   "amount_krw": local_amount_krw,
                                   "instant_discount_krw": local_c.instant_discount_krw,
                                   "charge_krw": local_c.charge_krw},
                "remainder": {"payment_method_id": m.payment_method_id,
                              "amount_krw": remainder_krw,
                              "instant_discount_krw": c.instant_discount_krw,
                              "charge_krw": c.charge_krw},
            },
        })
    options.sort(key=lambda o: (o["charge_krw"], o["payment_method_id"]))
    return [{"rank": rank, **o} for rank, o in enumerate(options, 1)], excluded
