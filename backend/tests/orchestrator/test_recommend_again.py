"""후보 화면 '다시 추천' (이슈 #68): 이미 보여 준 가게를 빼고 다음 가게를 추천한다."""

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.modules.orchestrator.models import Run


def _ctx(session_factory, rid):
    with session_factory() as s:
        return s.scalars(select(Run).where(Run.request_id == rid)).one().context


def _stores(session_factory, rid):
    return [c["restaurant"]["restaurant_id"] for c in _ctx(session_factory, rid)["candidates"]]


@pytest.fixture
def one_at_a_time(env, monkeypatch):
    """가짜 카탈로그는 가게가 적어 한 번에 1곳씩 추천받게 한다."""
    a = env.world.discovery
    orig = a.search
    monkeypatch.setattr(a, "search", lambda rid, body: orig(rid, {**(body or {}), "top_n": 1}))


def test_recommend_again_excludes_shown_until_none_left(env, session_factory, one_at_a_time):
    v = env.flow("CANDIDATES_READY")
    rid = v["request_id"]
    seen = set(_stores(session_factory, rid))
    assert seen

    rounds = 0
    while True:
        try:
            v2 = env.reply(v, type="RECOMMEND_AGAIN")
        except AppError as e:  # 더 없음: 지금 후보는 그대로
            assert e.status == 409 and e.code == "NO_MORE_CANDIDATES"
            after = env.get(rid)
            assert after["state"] == "CANDIDATES_READY"
            assert after["state_version"] == v["state_version"]
            assert set(_stores(session_factory, rid)) <= seen
            break
        assert v2["state"] == "CANDIDATES_READY"
        assert v2["state_version"] == v["state_version"] + 1
        new = set(_stores(session_factory, rid))
        assert new and not (new & seen)  # 이미 보여 준 가게는 다시 나오지 않는다
        assert [c["index"] for c in v2["prompt"]["candidates"]] == list(range(len(new)))
        seen |= new
        v = v2
        rounds += 1
        assert rounds < 50
    assert rounds >= 1
    assert set(_ctx(session_factory, rid)["recommended_restaurant_ids"]) <= seen

    # 다시 추천한 후보로 이어서 주문할 수 있다
    v = env.get(rid)
    v = env.reply(v, type="SELECT_CANDIDATE", candidate_index=0)
    assert v["state"] in ("PAYMENT_METHOD_SELECTION", "AWAITING_APPROVAL", "ORDER_BUILT")


def test_recommend_again_only_on_candidate_screen(env):
    v = env.flow("PAYMENT_METHOD_SELECTION")
    with pytest.raises(AppError) as e:
        env.reply(v, type="RECOMMEND_AGAIN")
    assert e.value.status == 409


def test_recommend_again_records_event(env, one_at_a_time):
    v = env.flow("CANDIDATES_READY")
    v = env.reply(v, type="RECOMMEND_AGAIN")
    details = [e.get("detail") or {} for e in env.events(v)]
    assert any(d.get("recommend_again") for d in details)
