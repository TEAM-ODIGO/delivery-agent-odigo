"""E가 별도 서버일 때(HTTP)의 결제 경로: O → (HTTP) 가짜 E 서버 → D-06.

진짜 E가 오면 같은 HttpPaymentPort를 그대로 쓴다.
"""

from fastapi.testclient import TestClient

from app.modules.orchestrator.ports import HttpPaymentPort
from scripts.fake_e_server import create_app


def _http_e(env, monkeypatch, mode="APPROVE"):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "svc-token")
    e_app = create_app(check_authorization=env._check, mode=mode)
    return HttpPaymentPort("http://e", "svc-token", client=TestClient(e_app))


def test_http_e_happy_path(env, monkeypatch):
    env.payment = _http_e(env, monkeypatch)
    v = env.flow("COMPLETED")
    assert v["state"] == "COMPLETED"
    assert v["result"]["payment_status"] == "APPROVED"


def test_http_e_unknown_then_e03(env, monkeypatch):
    env.payment = _http_e(env, monkeypatch, mode="UNKNOWN")
    v = env.flow("COMPLETED")
    assert v["state"] == "PAYMENT_UNKNOWN"
    assert env.get(v)["state"] == "COMPLETED"  # E-03(HTTP)로 확정


def test_http_e_rejects_wrong_token(env, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "svc-token")
    e_app = create_app(check_authorization=env._check)
    env.payment = HttpPaymentPort("http://e", "wrong", client=TestClient(e_app))
    v = env.flow("COMPLETED")
    # E가 요청 자체를 거절(401) → 결제 없음 → FAILED(REQUEST_REJECTED)
    assert v["state"] == "FAILED"
    assert v["error"]["code"] == "REQUEST_REJECTED"
