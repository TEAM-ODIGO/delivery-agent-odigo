"""후보 화면에 실제로 보인 후보 기록 (이슈 #48)."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.clock import KST
from app.core.db import get_session
from app.main import create_app
from app.modules.orchestrator import router as o_router
from app.modules.orchestrator import tracking
from app.modules.orchestrator.models import Run

SINCE = datetime(2026, 1, 1, tzinfo=KST)
USER = {"Authorization": "Bearer dev-token-U001"}


def run_ctx(session_factory, rid):
    with session_factory() as s:
        return s.scalars(select(Run).where(Run.request_id == rid)).one().context


def test_records_once_per_change_and_only_while_selecting(env, session_factory):
    v = env.flow("CANDIDATES_READY")
    rid = v["request_id"]
    svc = env.svc()
    first = [
        {"candidate_index": 0, "live_status": "OK"},
        {"candidate_index": 1, "live_status": "UNAVAILABLE"},
    ]
    assert svc.record_candidate_views("U001", rid, first) == {"recorded": True, "count": 2}
    assert env.svc().record_candidate_views("U001", rid, first)["reason"] == "SAME_AS_BEFORE"
    more = first + [{"candidate_index": 2, "collapsed": True}]
    assert env.svc().record_candidate_views("U001", rid, more)["recorded"] is True
    assert (
        env.svc().record_candidate_views("U001", rid, [{"candidate_index": 99}])["reason"]
        == "NO_CANDIDATE"
    )

    cands = run_ctx(session_factory, rid)["candidates"]
    r0, r1 = cands[0]["restaurant"]["restaurant_id"], cands[1]["restaurant"]["restaurant_id"]
    with session_factory() as s:
        assert tracking.shown_summary(s, r0, SINCE) == {"shown": 1, "shown_unavailable": 0}
        assert tracking.shown_summary(s, r1, SINCE) == {"shown": 1, "shown_unavailable": 1}

    v = env.reply(v, type="SELECT_CANDIDATE", candidate_index=0)
    assert env.svc().record_candidate_views("U001", rid, first)["reason"] == "NOT_SELECTING"


def test_completed_count(env, session_factory):
    v = env.flow("COMPLETED")
    store = run_ctx(session_factory, v["request_id"])["selected_candidate"]["restaurant"][
        "restaurant_id"
    ]
    with session_factory() as s:
        assert tracking.completed_count(s, store, SINCE) == 1
        assert tracking.completed_count(s, "NOPE", SINCE) == 0


@pytest.fixture
def client(env, session_factory):
    app = create_app()

    def session():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[o_router.get_service] = lambda: env.svc()
    return TestClient(app)


def test_http_endpoint(client, env):
    v = env.flow("CANDIDATES_READY")
    path = f"/orchestrator/runs/{v['request_id']}/candidate-views"
    body = {"shown": [{"candidate_index": 0}]}
    assert client.post(path, json=body).status_code == 401
    r = client.post(path, json=body, headers=USER)
    assert r.status_code == 200 and r.json()["recorded"] is True
    other = client.post(path, json=body, headers={"Authorization": "Bearer dev-token-U002"})
    assert other.status_code == 404  # 남의 주문은 존재 여부도 숨김
