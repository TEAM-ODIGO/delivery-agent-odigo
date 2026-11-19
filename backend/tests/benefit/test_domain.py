from app.modules.benefit.domain import (
    BenefitRule, PaymentMethod, Restaurant, calculate, min_order_shortfall, recommend,
)

CARD = PaymentMethod("CARD001", "PRD-CARD-A", "CARD", monthly_performance_krw=350000)
LOCAL = PaymentMethod("LOCAL001", "PRD-LOCAL-A", "LOCAL_CURRENCY", balance_krw=120000)
RULES = {
    "PRD-CARD-A": BenefitRule("R-CARD", "PRD-CARD-A", discount_krw=2000, min_order_krw=15000,
                              min_performance_krw=300000, monthly_cap_krw=10000),
    "PRD-LOCAL-A": BenefitRule("R-LOCAL", "PRD-LOCAL-A", discount_percent=5),
}
OK_SHOP = Restaurant("S1", True, True, "KOMSCO_MATCHED")


def ids(options):
    return [o["payment_method_id"] for o in options]


def test_추천_순위와_결제금액():
    options, _ = recommend([CARD, LOCAL], RULES, 35000, OK_SHOP)
    assert ids(options) == ["CARD001", "LOCAL001"]
    assert [o["charge_krw"] for o in options] == [33000, 33250]
    assert [o["rank"] for o in options] == [1, 2]


def test_최소주문금액_미달은_할인_0원으로_후보에_남는다():
    options, excluded = recommend([CARD, LOCAL], RULES, 10000, OK_SHOP)
    card = next(o for o in options if o["payment_method_id"] == "CARD001")
    assert card["instant_discount_krw"] == 0 and card["reasons"] == ["최소 주문금액 미달로 혜택 없음"]
    assert excluded == []


def test_전월실적_미달은_제외():
    low = PaymentMethod("CARD001", "PRD-CARD-A", "CARD", monthly_performance_krw=100000)
    options, excluded = recommend([low], RULES, 35000, OK_SHOP)
    assert options == [] and excluded[0]["reason_codes"] == ["PERFORMANCE_NOT_MET"]


def test_가맹점_아님과_확인불가는_지역화폐_제외():
    _, ex_false = recommend([LOCAL], RULES, 35000, Restaurant("S", False, False, "ASSUMED_FROM_VOUCHER"))
    _, ex_null = recommend([LOCAL], RULES, 35000, Restaurant("S", None, None, None))
    assert ex_false[0]["reason_codes"] == ["LOCAL_CURRENCY_NOT_ACCEPTED"]
    assert ex_null[0]["reason_codes"] == ["LOCAL_CURRENCY_UNVERIFIED"]


def test_지역화폐_잔액부족은_제외():
    poor = PaymentMethod("LOCAL001", "PRD-LOCAL-A", "LOCAL_CURRENCY", balance_krw=1000)
    options, excluded = recommend([CARD, poor], RULES, 35000, OK_SHOP)
    assert ids(options) == ["CARD001"]
    assert excluded[0]["reason_codes"] == ["LOCAL_CURRENCY_INSUFFICIENT_BALANCE"]


def test_지역화폐_필수이면_카드는_제외():
    options, excluded = recommend([CARD, LOCAL], RULES, 35000, OK_SHOP, local_currency_only=True)
    assert ids(options) == ["LOCAL001"]
    assert excluded[0]["reason_codes"] == ["PAYMENT_REQUIREMENT_NOT_MET"]


def test_월한도가_남은_만큼만_할인():
    c = calculate(CARD, RULES["PRD-CARD-A"], 35000, OK_SHOP, used_krw=9000)   # 남은 한도 1,000원
    assert c.instant_discount_krw == 1000 and c.charge_krw == 34000


def test_월한도_소진이면_혜택없음():
    c = calculate(CARD, RULES["PRD-CARD-A"], 35000, OK_SHOP, used_krw=10000)
    assert not c.eligible and c.reason_codes == ["BENEFIT_LIMIT_EXCEEDED"] and c.instant_discount_krw == 0


def test_할인은_주문금액을_넘지_않는다():
    rule = BenefitRule("R", "PRD-CARD-A", discount_krw=50000)
    assert calculate(CARD, rule, 10000, OK_SHOP).charge_krw == 0


def test_정률할인은_정수로_내림():
    rule = BenefitRule("R", "PRD-LOCAL-A", discount_percent=5)
    assert calculate(LOCAL, rule, 19999, OK_SHOP).instant_discount_krw == 999   # 999.95 → 999


def test_동률이면_결제수단_ID순():
    a = PaymentMethod("CARD_B", "PRD-CARD-A", "CARD", monthly_performance_krw=350000)
    b = PaymentMethod("CARD_A", "PRD-CARD-A", "CARD", monthly_performance_krw=350000)
    options, _ = recommend([a, b], RULES, 35000, OK_SHOP)
    assert ids(options) == ["CARD_A", "CARD_B"]


# ---------- 최소주문금액 사전 확인 (로컬 확장) ----------
def test_최소주문금액_미달이면_필요한_금액을_돌려준다():
    # 카드만 있고 주문 13,000원 < 카드 혜택 최소주문금액 15,000원 → B-05가 거절하는 주문
    assert min_order_shortfall([CARD], RULES, 13000, OK_SHOP) == 15000


def test_최소주문금액을_채우면_None():
    assert min_order_shortfall([CARD], RULES, 15000, OK_SHOP) is None


def test_쓸_수_있는_수단이_하나라도_있으면_None():
    # 지역화폐는 최소주문금액이 없어 13,000원도 결제할 수 있다
    assert min_order_shortfall([CARD, LOCAL], RULES, 13000, OK_SHOP) is None


def test_금액이_아닌_사유로_못_쓰면_None():
    low = PaymentMethod("CARD001", "PRD-CARD-A", "CARD", monthly_performance_krw=100000)
    # 전월실적 미달은 금액 문제가 아니다
    assert min_order_shortfall([low], RULES, 13000, OK_SHOP) is None
    assert min_order_shortfall([], RULES, 13000, OK_SHOP) is None


def test_지역화폐_불가_가게는_카드의_최소주문금액이_기준():
    shop = Restaurant("S", False, False, "ASSUMED_FROM_VOUCHER")
    assert min_order_shortfall([CARD, LOCAL], RULES, 13000, shop) == 15000
