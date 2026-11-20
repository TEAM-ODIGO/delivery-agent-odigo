"""최소주문금액 미달 후보(로컬 확장): 후보 목록에서 미리 막는다.

맡긴 결제수단을 쓸 수 없는 금액(예: 카드 혜택 최소주문금액 15,000원 < 주문 13,000원)이면
B-05가 PAYMENT_METHOD_NOT_ELIGIBLE로 거절해 주문이 BLOCKED되므로,
후보에 orderable=false·min_order를 붙이고 선택을 거절한다.
"""
import pytest

from app.core.errors import AppError


class _MinOrderC:
    """가짜 C에 check_min_order만 더한다. 첫 후보만 최소주문금액 미달로 답한다."""

    def __init__(self, inner, shortfall_at=(0,), required=15000, fail=False):
        self._inner, self._at, self._required, self._fail = inner, set(shortfall_at), required, fail
        self.calls = []

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def check_min_order(self, body):
        self.calls.append(body)
        if self._fail:
            raise AppError(404, "USER_NOT_FOUND", "사용자를 찾을 수 없습니다.")
        return {
            "results": [
                {
                    "restaurant_id": o["restaurant_id"],
                    "order_krw": o["order_krw"],
                    "required_order_krw": self._required if i in self._at else None,
                }
                for i, o in enumerate(body["orders"])
            ]
        }


@pytest.fixture
def mixed(env):
    """후보 중 0번만 최소주문금액 미달인 주문"""
    c = _MinOrderC(env.world.benefit, shortfall_at=(0,))
    env.world.benefit = c
    return c


def test_후보에_주문불가와_사유가_붙는다(env, mixed):
    v = env.start()
    cands = v["prompt"]["candidates"]
    assert len(cands) >= 2
    first, rest = cands[0], cands[1:]
    assert first["orderable"] is False
    assert first["min_order"] == {"required_krw": 15000, "order_krw": first["total_krw"]}
    assert first["meets_all_constraints"] is False
    assert first["violations"] == [
        {"code": "MIN_ORDER_NOT_MET", "message": "최소주문금액이 맞지 않아서 주문이 불가합니다"}
    ]
    for c in rest:
        assert c["orderable"] is True and c["min_order"] is None
        assert all(x["code"] != "MIN_ORDER_NOT_MET" for x in c["violations"])


def test_C에_후보_금액과_맡긴_결제수단을_보낸다(env, mixed):
    v = env.start()
    body = mixed.calls[0]
    assert body["user_id"] == "U001"
    assert body["candidate_payment_method_ids"] == ["CARD001", "LOCAL001"]
    totals = [c["total_krw"] for c in v["prompt"]["candidates"]]
    assert [o["order_krw"] for o in body["orders"]] == totals


def test_주문불가_후보를_고르면_서버도_거절한다(env, mixed):
    v = env.start()
    with pytest.raises(AppError) as e:
        env.reply(v, type="SELECT_CANDIDATE", candidate_index=0)
    assert e.value.status == 400 and e.value.code == "VALIDATION_ERROR"
    assert e.value.details["fields"][0]["reason"] == "min_order_not_met"
    assert e.value.details["required_krw"] == 15000
    assert env.get(v)["state"] == "CANDIDATES_READY"  # 상태는 그대로


def test_주문가능한_후보는_끝까지_진행한다(env, mixed):
    v = env.start()
    v = env.reply(v, type="SELECT_CANDIDATE", candidate_index=1)
    assert v["state"] == "PAYMENT_METHOD_SELECTION"


def test_확인하지_못하면_표시하지_않고_기존_흐름대로(env):
    env.world.benefit = _MinOrderC(env.world.benefit, fail=True)
    v = env.start()
    assert v["state"] == "CANDIDATES_READY"
    assert all(c["orderable"] is True and c["min_order"] is None for c in v["prompt"]["candidates"])


def test_C가_이_확인을_모르면_표시하지_않는다(env):
    v = env.start()  # 가짜 C에는 check_min_order가 없다
    assert all(c["orderable"] is True for c in v["prompt"]["candidates"])
