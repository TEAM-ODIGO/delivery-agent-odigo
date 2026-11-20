"""O HTTP 입구 (O-01~O-05): 인증, 상태 코드, 공통 에러 형식.

TestClient로 진짜 FastAPI 앱을 부르고, DB·가짜 모듈만 테스트용으로 바꿔 끼운다.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_session
from app.main import create_app
from app.modules.orchestrator import router as o_router
from tests.orchestrator.conftest import ADDRESS, DELEGATION, OK_MESSAGE

USER = {"Authorization": "Bearer dev-token-U001"}


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


def test_o01_requires_login(client):
    r = client.post("/orchestrator/runs", json={"message": "치킨", "delivery_address": ADDRESS})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_full_flow_over_http(client):
    r = client.post(
        "/orchestrator/runs",
        headers={**USER, "Idempotency-Key": "k1"},
        json={"message": OK_MESSAGE, "delivery_address": ADDRESS, "delegation": DELEGATION},
    )
    assert r.status_code == 201
    v = r.json()
    rid = v["request_id"]
    steps = [
        {"type": "SELECT_CANDIDATE", "candidate_index": 0},
        {"type": "SELECT_PAYMENT_METHOD", "payment_method_id": "CARD001"},
        {"type": "APPROVE"},
    ]
    for i, body in enumerate(steps):
        r = client.post(
            f"/orchestrator/runs/{rid}/reply",
            headers={**USER, "X-Request-ID": rid, "Idempotency-Key": f"r{i}"},
            json={**body, "expected_state_version": v["state_version"]},
        )
        assert r.status_code == 200, r.text
        v = r.json()
    assert v["state"] == "COMPLETED"

    r = client.get(f"/orchestrator/runs/{rid}", headers=USER)
    assert r.json()["result"]["payment_status"] == "APPROVED"

    assert client.get(f"/orchestrator/runs/{rid}/events", headers=USER).status_code == 403
    r = client.get(
        f"/orchestrator/runs/{rid}/events", headers={"Authorization": "Bearer admin-secret"}
    )
    assert r.status_code == 200 and r.json()["count"] > 10


def test_o02_requires_expected_state_version(client):
    r = client.post(
        "/orchestrator/runs",
        headers=USER,
        json={"message": OK_MESSAGE, "delivery_address": ADDRESS},
    )
    rid = r.json()["request_id"]
    r = client.post(
        f"/orchestrator/runs/{rid}/reply",
        headers={**USER, "X-Request-ID": rid},
        json={"type": "SELECT_CANDIDATE", "candidate_index": 0},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["request_id"] == rid  # 공통 에러 형식


def test_o02_stale_version_is_409(client):
    r = client.post(
        "/orchestrator/runs",
        headers=USER,
        json={"message": OK_MESSAGE, "delivery_address": ADDRESS},
    )
    v = r.json()
    r = client.post(
        f"/orchestrator/runs/{v['request_id']}/reply",
        headers=USER,
        json={
            "type": "SELECT_CANDIDATE",
            "candidate_index": 0,
            "expected_state_version": v["state_version"] + 5,
        },
    )
    assert r.status_code == 409
    assert r.json()["error"]["details"]["state"] == "CANDIDATES_READY"


def test_o03_unknown_run_is_404(client):
    r = client.get("/orchestrator/runs/REQNOPE", headers=USER)
    assert r.status_code == 404 and r.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_o05_health(client):
    r = client.get("/orchestrator/health")
    assert r.json() == {"service": "orchestrator", "status": "UP"}
