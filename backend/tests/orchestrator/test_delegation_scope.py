"""맡긴 일(allowed_actions)에 따라 주문 흐름이 바뀐다 (이슈 #111).

예전에는 맡기지 않은 일이 있어도 끝까지 진행하다가 D에서 '맡기지 않은 작업'으로 막혔다
(주문안 만들기: 결제수단까지 고른 뒤, 결제 요청: 카드를 태그한 뒤).
"""

from tests.orchestrator.conftest import DELEGATION


def flow(env, actions, until="COMPLETED"):
    return env.flow(until, delegation=dict(DELEGATION, allowed_actions=actions))


def states(env, view) -> list[str]:
    return [e["to_state"] for e in env.events(view) if e["event_type"] == "STATE_CHANGED"]


def test_all_delegated_pays_as_before(env):
    v = flow(env, ["SEARCH", "BUILD_ORDER", "REQUEST_PAYMENT"])
    assert v["state"] == "COMPLETED"
    assert v["allowed_actions"] == ["SEARCH", "BUILD_ORDER", "REQUEST_PAYMENT"]


def test_without_build_order_stops_at_recommendation(env):
    v = flow(env, ["SEARCH"])
    assert v["state"] == "RECOMMENDED" and v["blocked"] is None
    rec = v["result"]["recommendation"]
    assert rec["restaurant_name"] and rec["items"]
    assert rec["yogiyo_url"].startswith("https://www.yogiyo.co.kr/mobile/#/")
    seen = states(env, v)
    assert "PAYMENT_METHOD_SELECTION" not in seen and "AWAITING_APPROVAL" not in seen


def test_without_request_payment_hands_off_without_paying(env):
    v = flow(env, ["SEARCH", "BUILD_ORDER"])
    assert v["state"] == "HANDED_OFF" and v["blocked"] is None
    assert v["result"]["order"]["charge_krw"] > 0
    assert v["result"]["yogiyo_url"]
    seen = states(env, v)
    assert "AWAITING_APPROVAL" in seen and "PAYING" not in seen
    assert not [e for e in env.events(v) if e["event_type"] == "PAYMENT_RESULT"]


def test_search_is_always_delegated(env):
    v = flow(env, ["BUILD_ORDER", "REQUEST_PAYMENT"], until="CANDIDATES_READY")
    assert v["allowed_actions"][0] == "SEARCH"
    v = flow(env, [])
    assert v["state"] == "RECOMMENDED"  # 아무것도 안 맡겨도 가게 찾기는 하고 추천까지만
