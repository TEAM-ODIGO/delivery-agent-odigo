"""결제 승인 때 '이번 달 받은 혜택'을 쌓고, 환불하면 되돌린다 (로컬 확장, 팀 합의 전).

시드: 신한 요기요 체크(CARD001)는 월 한도 6,000원, 이번 달 이미 받은 혜택 2,000원 → 남은 혜택 4,000원.
"""

from app.modules.benefit import facade, seed, service

CALC = {"request_id": "REQ001", "user_id": "U001", "restaurant_id": "STORE101", "menu_subtotal_krw": 18000,
        "delivery_fee_krw": 2000, "payment_method_id": "CARD001", "benefit_rule_version": "BEN-V1"}


def _remaining(method_id: str = "CARD001") -> int | None:
    got = {m["payment_method_id"]: m for m in facade.list_payment_methods("U001")["results"]}
    return got[method_id]["monthly_benefit_remaining_krw"]


def test_결제가_승인되면_남은_혜택이_받은_할인만큼_줄어든다():
    assert _remaining() == 4000
    out = facade.record_usage("PAY001", "CARD001", 2000)
    assert out["recorded"] is True and out["used_krw"] == 4000
    assert _remaining() == 2000


def test_카드_혜택_화면의_남은_혜택도_같이_줄어든다():
    facade.record_usage("PAY001", "CARD001", 2000)
    d = facade.card_dashboard("U001", "CARD001")["monthly_benefit"]
    assert d["cap_krw"] == 6000 and d["used_krw"] == 4000 and d["remaining_krw"] == 2000


def test_같은_결제는_한_번만_더한다():
    facade.record_usage("PAY001", "CARD001", 2000)
    again = facade.record_usage("PAY001", "CARD001", 2000)  # reconcile이 같은 결과를 다시 알려 준 경우
    assert again["recorded"] is False
    assert _remaining() == 2000


def test_할인이_없는_결제는_남기지_않는다():
    out = facade.record_usage("PAY002", "LOCAL001", 0)  # 서울페이: 충전식이라 할인 없음
    assert out["recorded"] is False and out["amount_krw"] == 0
    assert seed.BENEFIT_USED_KRW.get("LOCAL001", 0) == 0


def test_환불하면_그_결제로_받은_혜택만_되돌린다():
    facade.record_usage("PAY001", "CARD001", 2000)
    facade.record_usage("PAY002", "CARD001", 1500)
    assert _remaining() == 500
    out = facade.reverse_usage("PAY001")
    assert out["reversed"] is True and out["amount_krw"] == 2000
    assert _remaining() == 2500  # PAY002의 1,500원은 그대로


def test_환불을_두_번_알려도_한_번만_되돌린다():
    facade.record_usage("PAY001", "CARD001", 2000)
    facade.reverse_usage("PAY001")
    again = facade.reverse_usage("PAY001")
    assert again["reversed"] is False
    assert _remaining() == 4000  # 시드 값(2,000원 사용) 아래로 내려가지 않는다


def test_기록이_없는_결제를_환불해도_아무_일도_없다():
    assert facade.reverse_usage("PAY-NOPE")["reversed"] is False
    assert _remaining() == 4000


def test_한도를_다_쓰면_다음_계산은_BENEFIT_LIMIT_EXCEEDED로_할인_0원():
    facade.record_usage("PAY001", "CARD001", 2000)
    facade.record_usage("PAY002", "CARD001", 2000)
    assert _remaining() == 0
    d = facade.calculate(CALC)
    assert d["eligible"] is False and d["instant_discount_krw"] == 0
    assert d["reason_codes"] == ["BENEFIT_LIMIT_EXCEEDED"]
    facade.reverse_usage("PAY002")  # 한 건 환불하면 다시 할인을 받을 수 있다
    assert facade.calculate(CALC)["instant_discount_krw"] == 2000


def test_남은_한도가_할인보다_작으면_남은_만큼만_깎는다():
    facade.record_usage("PAY001", "CARD001", 1500)  # 남은 혜택 4,000원에서 1,500원씩 두 번
    facade.record_usage("PAY002", "CARD001", 1500)
    assert _remaining() == 1000
    assert facade.calculate(CALC)["instant_discount_krw"] == 1000


def test_상태를_초기화하면_기록도_사라진다():
    facade.record_usage("PAY001", "CARD001", 2000)
    service.reset_state()
    assert _remaining() == 4000
    assert facade.record_usage("PAY001", "CARD001", 2000)["recorded"] is True  # 새로 시작한 것처럼 다시 반영
