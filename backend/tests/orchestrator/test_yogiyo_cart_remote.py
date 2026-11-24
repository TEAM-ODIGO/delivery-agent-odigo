"""요기요 장바구니 remote 모드: 서버는 작업을 쌓고, PC 작업기가 가져가서 담은 결과를 올린다.

작업기 API(cart/router.py)는 서비스 토큰이 필요하다. 테스트에서는 토큰 확인을 끄고,
토큰이 없을 때 거절되는지만 따로 본다.
"""

import base64

from fastapi.testclient import TestClient

from app.core.db import get_session
from app.main import create_app
from app.modules.cart import router as cart_router
from app.modules.cart.service import CartService, CartSettings
from app.modules.discovery.security import require_service_token
from app.modules.orchestrator import router as o_router

USER = {"Authorization": "Bearer dev-token-U001"}
CLAIM = "/orchestrator/cart-worker/jobs/claim"


def _client(env, session_factory, clock, tmp_path, *, wait=30, timeout=180, auth=False):
    app = create_app()

    def session():
        with session_factory() as s:
            yield s

    def cart():
        with session_factory() as s:
            yield CartService(
                s,
                session_factory,
                settings=CartSettings(
                    mode="remote",
                    out_dir=str(tmp_path),
                    remote_wait_seconds=wait,
                    remote_timeout_seconds=timeout,
                ),
                now=clock,
            )

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[o_router.get_service] = lambda: env.svc()
    app.dependency_overrides[o_router.get_cart] = cart
    app.dependency_overrides[cart_router.get_cart] = cart
    if not auth:
        app.dependency_overrides[require_service_token] = lambda: None
    return TestClient(app)


def _url(v) -> str:
    return f"/orchestrator/runs/{v['request_id']}/yogiyo-cart"


def _result(job, **over):
    body = {
        "status": "CART_READY",
        "cart_items": [
            {
                "menu_id": i["menu_id"],
                "name": i["name"],
                "unit_price_krw": i["unit_price_krw"],
                "quantity": i["quantity"],
                "options": [],
            }
            for i in job["items"]
        ],
        "menu_subtotal_krw": job["expected_menu_subtotal_krw"],
        "delivery_fee_krw": job["expected_delivery_fee_krw"],
        "mismatches": [],
        "url": f"https://www.yogiyo.co.kr/mobile/#/{job['restaurant_id']}/",
        "screenshot_base64": base64.b64encode(b"\xff\xd8fake-jpeg").decode(),
    }
    body.update(over)
    return body


def test_remote_start_waits_for_worker(env, session_factory, clock, tmp_path):
    c = _client(env, session_factory, clock, tmp_path)
    v = env.flow("AWAITING_APPROVAL")
    r = c.post(_url(v), headers=USER)
    assert r.status_code == 202
    assert (r.json()["status"], r.json()["mode"]) == ("QUEUED", "remote")
    assert c.get(_url(v), headers=USER).json()["status"] == "QUEUED"  # 서버가 직접 담지 않는다


def test_worker_claims_and_submits_result(env, session_factory, clock, tmp_path):
    c = _client(env, session_factory, clock, tmp_path)
    v = env.flow("AWAITING_APPROVAL")
    c.post(_url(v), headers=USER)

    job = c.post(CLAIM, json={"worker": "pc-1"}).json()
    order = v["prompt"]["order"]
    assert job["expected_menu_subtotal_krw"] == order["menu_subtotal_krw"]
    assert c.get(_url(v), headers=USER).json()["status"] == "RUNNING"
    assert c.post(CLAIM, json={}).status_code == 204  # 같은 작업을 두 번 가져가지 않는다

    r = c.post(f"/orchestrator/cart-worker/jobs/{job['job_id']}/result", json=_result(job))
    assert r.status_code == 200
    got = c.get(_url(v), headers=USER).json()
    assert (got["status"], got["mode"]) == ("CART_READY", "remote")
    assert got["screenshot"].startswith("data:image/jpeg;base64,")
    assert got["finished_at"] is not None


def test_worker_failure_blocks_approval(env, session_factory, clock, tmp_path):
    c = _client(env, session_factory, clock, tmp_path)
    v = env.flow("AWAITING_APPROVAL")
    c.post(_url(v), headers=USER)
    job = c.post(CLAIM, json={}).json()
    body = _result(job, status="FAILED", cart_items=[], error="RuntimeError: 메뉴를 찾지 못함")
    c.post(f"/orchestrator/cart-worker/jobs/{job['job_id']}/result", json=body)
    r = c.post(
        f"/orchestrator/runs/{v['request_id']}/reply",
        json={"type": "APPROVE", "expected_state_version": v["state_version"]},
        headers=USER,
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CART_FAILED"


def test_falls_back_to_fake_when_no_worker(env, session_factory, clock, tmp_path):
    c = _client(env, session_factory, clock, tmp_path, wait=30)
    v = env.flow("AWAITING_APPROVAL")
    c.post(_url(v), headers=USER)
    clock.advance(seconds=31)
    got = c.get(_url(v), headers=USER).json()
    assert (got["status"], got["mode"]) == ("CART_READY", "fake")
    assert "가져가지 않아" in got["error"]
    assert c.post(CLAIM, json={}).status_code == 204  # 마무리된 작업은 가져가지 않는다


def test_late_result_after_timeout_is_rejected(env, session_factory, clock, tmp_path):
    c = _client(env, session_factory, clock, tmp_path, timeout=180)
    v = env.flow("AWAITING_APPROVAL")
    c.post(_url(v), headers=USER)
    job = c.post(CLAIM, json={}).json()
    clock.advance(seconds=181)
    got = c.get(_url(v), headers=USER).json()
    assert (got["status"], got["mode"]) == ("CART_READY", "fake")
    assert "결과를 보내지 않아" in got["error"]

    r = c.post(f"/orchestrator/cart-worker/jobs/{job['job_id']}/result", json=_result(job))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CART_JOB_CLOSED"


def test_zero_wait_means_wait_forever(env, session_factory, clock, tmp_path):
    c = _client(env, session_factory, clock, tmp_path, wait=0, timeout=0)
    v = env.flow("AWAITING_APPROVAL")
    c.post(_url(v), headers=USER)
    clock.advance(hours=1)
    assert c.get(_url(v), headers=USER).json()["status"] == "QUEUED"


def test_bad_screenshot_and_unknown_job(env, session_factory, clock, tmp_path):
    c = _client(env, session_factory, clock, tmp_path)
    v = env.flow("AWAITING_APPROVAL")
    c.post(_url(v), headers=USER)
    job = c.post(CLAIM, json={}).json()
    bad = _result(job, screenshot_base64="이건-base64가-아님")
    url = f"/orchestrator/cart-worker/jobs/{job['job_id']}/result"
    assert c.post(url, json=bad).status_code == 400
    r = c.post("/orchestrator/cart-worker/jobs/CART-none/result", json=_result(job))
    assert r.status_code == 404


def test_worker_api_needs_service_token(env, session_factory, clock, tmp_path):
    c = _client(env, session_factory, clock, tmp_path, auth=True)
    assert c.post(CLAIM, json={}).status_code == 401
    assert c.post(CLAIM, json={}, headers=USER).status_code == 401  # 사용자 토큰으로는 안 됨


def test_remote_wait_settings_from_env(monkeypatch):
    monkeypatch.setenv("YOGIYO_CART_REMOTE_WAIT_SECONDS", "")
    monkeypatch.setenv("YOGIYO_CART_REMOTE_TIMEOUT_SECONDS", "abc")
    cfg = CartSettings()
    assert (cfg.remote_wait_seconds, cfg.remote_timeout_seconds) == (30.0, 180.0)
