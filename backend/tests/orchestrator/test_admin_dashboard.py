"""관리자 대시보드 API (이슈 #46): 오늘 운영 개요(O-17), 조치할 주문, 주문 상세(O-18~O-20)."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_session
from app.main import create_app
from app.modules.orchestrator import router as o_router
from app.modules.orchestrator.models import Run

ADMIN = {"Authorization": "Bearer admin-secret"}


@pytest.fixture
def client(env, session_factory):
    app = create_app()

    def session():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[o_router.get_service] = lambda: env.svc()
    app.dependency_overrides[o_router.get_admin_token] = lambda: "admin-secret"
    return TestClient(app)


def set_state(env, rid, state, *, minutes_ago=0, error=None, blocked=None, user=None):
    with env.sf() as s:
        run = s.scalars(select(Run).where(Run.request_id == rid)).one()
        run.state = state
        run.updated_at = env.clock() - timedelta(minutes=minutes_ago)
        if error is not None:
            run.error = error
        if blocked is not None:
            run.blocked = blocked
        if user is not None:
            run.user_id = user
        s.commit()


@pytest.fixture
def runs(env):
    done = env.flow("COMPLETED")["request_id"]
    waiting = env.flow("CANDIDATES_READY")["request_id"]  # 사용자 차례: 오래돼도 조치 아님
    unknown = env.flow("CANDIDATES_READY")["request_id"]
    stuck = env.flow("CANDIDATES_READY")["request_id"]
    fail1 = env.flow("CANDIDATES_READY")["request_id"]
    fail2 = env.flow("CANDIDATES_READY")["request_id"]
    other_fail = env.flow("CANDIDATES_READY")["request_id"]
    env.clock.advance(minutes=30)
    set_state(env, waiting, "CANDIDATES_READY", minutes_ago=30)
    set_state(env, unknown, "PAYMENT_UNKNOWN", minutes_ago=1)
    set_state(env, stuck, "VALIDATING", minutes_ago=15)
    set_state(env, fail1, "FAILED", error={"code": "NO_CANDIDATE"}, user="U009")
    set_state(env, fail2, "BLOCKED", blocked={"reason_codes": ["BUDGET_EXCEEDED"]}, user="U009")
    set_state(env, other_fail, "FAILED", error={"code": "NO_CANDIDATE"})
    return {
        "done": done,
        "waiting": waiting,
        "unknown": unknown,
        "stuck": stuck,
        "fail1": fail1,
        "fail2": fail2,
        "other_fail": other_fail,
    }


def test_summary_counts_and_reasons(client, runs):
    r = client.get("/orchestrator/admin/summary", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    c = body["counts"]
    assert (c["started"], c["completed"], c["payment_unknown"], c["failed"], c["blocked"]) == (
        7,
        1,
        1,
        2,
        1,
    )
    assert c["in_progress"] == 3  # 후보 선택 대기 · 결제 확인 중 · 검증 중
    assert body["top_reasons"][0] == {"code": "NO_CANDIDATE", "count": 2}
    labels = {s["label"]: s["count"] for s in body["by_step"]}
    assert labels == {"후보 선택": 1, "주문 검증": 1, "결제 확인 중": 1}


def test_action_list_rules_and_order(client, runs):
    body = client.get("/orchestrator/admin/summary", headers=ADMIN).json()
    got = [(a["request_id"], a["action"]) for a in body["actions"]]
    assert got[0] == (runs["unknown"], "PAYMENT_UNKNOWN")
    assert got[1] == (runs["stuck"], "STUCK")
    assert body["actions"][1]["stuck_minutes"] == 15
    assert {(runs["fail1"], "REPEATED_FAILURE"), (runs["fail2"], "REPEATED_FAILURE")} <= set(got)
    assert (runs["other_fail"], "FAILED") in got
    ids = {rid for rid, _ in got}
    assert runs["waiting"] not in ids and runs["done"] not in ids  # 사용자 차례·완료는 조치 아님
    assert body["action_count"] == 5


def test_run_detail_and_sub_resources(client, runs):
    rid = runs["done"]
    d = client.get(f"/orchestrator/admin/runs/{rid}", headers=ADMIN).json()
    assert d["state"] == "COMPLETED" and d["selected"]["items"]
    assert d["payment"]["payment_status"] == "APPROVED"
    assert d["constraints"]["people"] == 2
    live = client.get(f"/orchestrator/admin/runs/{rid}/live-check", headers=ADMIN)
    assert live.status_code == 200 and "state" in live.json()
    cart = client.get(f"/orchestrator/admin/runs/{rid}/yogiyo-cart", headers=ADMIN)
    assert cart.status_code == 200


def test_admin_endpoints_need_admin_token(client, runs):
    for path in ["/orchestrator/admin/summary", f"/orchestrator/admin/runs/{runs['done']}"]:
        assert client.get(path).status_code == 403
        assert (
            client.get(path, headers={"Authorization": "Bearer dev-token-U001"}).status_code == 403
        )
    assert client.get("/orchestrator/admin/runs/NOPE", headers=ADMIN).status_code == 404


# ---------------------------------------------------------------- 이슈 #94
def test_summary_metrics_hourly_rankings_services(client, runs):
    body = client.get("/orchestrator/admin/summary", headers=ADMIN).json()
    m = body["metrics"]["today"]
    assert (m["started"], m["completed"]) == (7, 1)
    assert m["completion_rate"] == 14  # 1 / 7
    assert m["avg_charge_krw"] and m["avg_charge_krw"] > 0
    assert m["avg_order_seconds"] is not None and m["saved_krw"] >= 0
    assert body["metrics"]["yesterday"]["started"] == 0
    assert body["metrics"]["yesterday"]["completion_rate"] is None
    assert len(body["hourly"]["today"]) == 24 and sum(body["hourly"]["today"]) == 7
    assert sum(body["hourly"]["yesterday"]) == 0
    r = body["rankings"]
    assert r["restaurants"] and r["restaurants"][0]["count"] >= 1
    assert r["payment_methods"] and r["payment_methods"][0]["count"] == 1
    assert {s["key"] for s in body["services"]} == {"LIVE_CHECK", "YOGIYO_CART", "AI"}
    assert all(s["status"] in {"OK", "WARN", "DOWN", "IDLE", "OFF"} for s in body["services"])


def test_admin_events_feed(client, runs):
    r = client.get("/orchestrator/admin/events", headers=ADMIN)
    assert r.status_code == 200
    events = r.json()["events"]
    kinds = [e["kind"] for e in events]
    assert "STARTED" in kinds and "COMPLETED" in kinds
    assert events == sorted(events, key=lambda e: e["at"], reverse=True)
    done = next(e for e in events if e["kind"] == "COMPLETED")
    assert done["request_id"] == runs["done"] and done["charge_krw"]
    assert client.get("/orchestrator/admin/events").status_code == 403
