"""결제가 승인되면 C의 '이번 달 남은 혜택'이 줄고, 환불이 끝나면 되돌아온다 (로컬 확장, 팀 합의 전).

진짜 C로만 확인한다: TEST_REAL_C=1 pytest tests/orchestrator/test_benefit_usage.py
시드: 신한 요기요 체크(CARD001) 월 한도 6,000원 · 이번 달 받은 혜택 2,000원 → 남은 4,000원. 이 주문은 2,000원 할인.
"""

import os

import pytest

from app.modules.benefit import facade
from app.modules.orchestrator.models import Run
from app.modules.orchestrator.schemas import RefundCreate

pytestmark = pytest.mark.skipif(os.getenv("TEST_REAL_C") != "1", reason="진짜 C(TEST_REAL_C=1)로만 확인")


def _remaining(method_id: str = "CARD001"):
    got = {m["payment_method_id"]: m for m in facade.list_payment_methods("U001")["results"]}
    return got[method_id]["monthly_benefit_remaining_krw"]


class _Refunds:
    """E 환불 응답만 흉내 내는 자리 (결제는 이미 끝났다)."""

    def __init__(self, payment_id: str, status: str):
        self.payment_id, self.status = payment_id, status

    def list_card_payments(self, user_id, payment_method_id, limit=3):
        return {"items": [{"payment_id": self.payment_id, "status": "APPROVED", "refundable_krw": 17000}]}

    def request_refund(self, payment_id, body, *, idempotency_key):
        return {"refund_id": "RFD001", "status": self.status, "amount_krw": body["amount_krw"]}

    def get_refund(self, payment_id, refund_id):
        return {"refund_id": refund_id, "status": self.status}


def _refund(env, v, status: str) -> dict:
    pid = v["result"]["payment_id"]
    return env.svc(payment=_Refunds(pid, status)).refund_payment(
        "U001", pid, RefundCreate(payment_method_id="CARD001"), "REFUND-KEY-1")


def test_결제가_승인되면_남은_혜택이_할인만큼_줄어든다(env):
    assert _remaining() == 4000
    v = env.flow("COMPLETED")
    assert v["state"] == "COMPLETED" and v["result"]["order"]["instant_discount_krw"] == 2000
    assert _remaining() == 2000
    assert facade.card_dashboard("U001", "CARD001")["monthly_benefit"]["used_krw"] == 4000


def test_결제가_끝나지_않았으면_줄지_않는다(env):
    v = env.flow("AWAITING_APPROVAL")
    assert v["state"] == "AWAITING_APPROVAL"
    assert _remaining() == 4000


def test_같은_결과를_다시_처리해도_두_번_줄지_않는다(env):
    v = env.flow("COMPLETED")
    svc = env.svc()
    run = svc.session.get(Run, v["request_id"])
    svc._record_benefit_usage(run, {"payment_id": v["result"]["payment_id"]})  # reconcile이 같은 결과를 다시 받은 경우
    assert _remaining() == 2000


def test_환불이_끝나면_받았던_혜택이_되돌아온다(env):
    v = env.flow("COMPLETED")
    assert _remaining() == 2000
    out = _refund(env, v, "REFUNDED")
    assert out["status"] == "REFUNDED"
    assert _remaining() == 4000


def test_환불이_아직_접수만_됐으면_되돌리지_않는다(env):
    v = env.flow("COMPLETED")
    out = _refund(env, v, "PENDING")
    assert out["status"] == "PENDING"
    assert _remaining() == 2000


def test_한도를_다_쓰면_다음_계산은_할인_0원과_BENEFIT_LIMIT_EXCEEDED(env):
    env.flow("COMPLETED")  # 4,000 → 2,000
    env.flow("COMPLETED")  # 2,000 → 0
    assert _remaining() == 0
    d = facade.calculate({"request_id": "REQ-X", "user_id": "U001", "restaurant_id": "STORE101",
                          "menu_subtotal_krw": 18000, "delivery_fee_krw": 2000,
                          "payment_method_id": "CARD001", "benefit_rule_version": "BEN-V1"})
    assert d["instant_discount_krw"] == 0 and d["reason_codes"] == ["BENEFIT_LIMIT_EXCEEDED"]


def test_C_기록이_실패해도_주문은_완료된다(env):
    def boom(*_a, **_k):
        raise RuntimeError("C down")

    env.world.benefit.record_usage = boom
    v = env.flow("COMPLETED")
    assert v["state"] == "COMPLETED" and v["result"]["payment_status"] == "APPROVED"
    errors = [e for e in env.events(v) if (e.get("detail") or {}).get("api") == "C-USAGE"]
    assert errors and errors[0]["detail"]["code"] == "RuntimeError"
