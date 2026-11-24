"""요기요 실시간 확인 remote 모드 (#80): 한국 PC 작업기가 후보 식당을 대신 확인한다."""

import asyncio
import functools

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_session
from app.main import create_app
from app.modules.livecheck import service as live_service
from app.modules.livecheck.checker import CheckResult
from app.modules.livecheck.remote import check_on_browser, target_from_json
from app.modules.livecheck.service import LiveCheckService, LiveCheckSettings
from app.modules.orchestrator import router as o_router

TOKEN = {"Authorization": "Bearer dev-internal-token"}


@pytest.fixture
def remote(env, monkeypatch):
    monkeypatch.setattr(live_service, "_remote", {})
    settings = LiveCheckSettings(
        mode="remote", timeout_seconds=0, want=2, cache_minutes=15,
        remote_wait_seconds=20, remote_timeout_seconds=90,
    )
    svc = LiveCheckService(env.sf, settings=settings, now=env.clock)
    env.svc = functools.partial(type(env).svc, env, live_check=svc)
    return svc


def _view(env, v):
    return env.svc().live_check_view("U001", v["request_id"])


def _open_page(t: dict) -> dict:
    menus = [{"id": i["menu_id"], "text": f"{i.get('name')} {i.get('expected_price_krw') or 0:,}원"} for i in t["items"]]
    return {"url": f"/#/{t['restaurant_id']}/", "menus": menus, "denied": False, "not_serving": False}


NOT_SERVING = {"url": "/#/x/", "menus": [], "denied": False, "not_serving": True}


def _work(svc, pages):
    """작업기 흉내: 작업을 가져가 후보별 화면 원본을 올린다. pages: 순번 → 원본(없으면 열린 화면)."""
    job = svc.claim("pc-1")
    assert job is not None
    results = []
    for t in job["targets"]:
        if t["cached"]:
            results.append({"index": t["index"], "restaurant_id": t["restaurant_id"], "status": t["cached"]["status"], "cached": True})
            continue
        page = pages.get(t["index"], _open_page(t))
        status = "UNAVAILABLE" if page.get("not_serving") else "OK"
        results.append({"index": t["index"], "restaurant_id": t["restaurant_id"], "status": status, "page": page, "seconds": 1.2})
    return job, svc.submit(job["job_id"], results)


def test_queued_until_worker_reports_then_not_serving_is_blocked(env, remote):
    v = env.flow("CANDIDATES_READY")
    lc = _view(env, v)
    assert lc["mode"] == "remote" and lc["state"] == "RUNNING"
    assert {r["status"] for r in lc["results"]} == {"CHECKING"}

    job, out = _work(remote, {0: NOT_SERVING})
    assert out == {"accepted": True, "current": True}
    assert job["want"] == 2 and len(job["targets"]) == 3
    lc = _view(env, v)
    assert lc["state"] == "DONE"
    first = lc["results"][0]
    assert first["status"] == "UNAVAILABLE" and first["selectable"] is False
    assert "서비스 미제공" in first["reason"]
    assert [r["status"] for r in lc["results"][1:]] == ["OK", "OK"]
    assert remote.claim("pc-1") is None  # 작업은 한 번만 가져간다


def test_no_worker_means_unchecked_and_not_blocking(env, remote):
    v = env.flow("CANDIDATES_READY")
    env.clock.advance(seconds=21)
    lc = _view(env, v)
    assert lc["state"] == "DONE"
    assert {r["status"] for r in lc["results"]} == {"ERROR"}
    assert all(r["selectable"] for r in lc["results"])
    assert "작업기" in lc["results"][0]["reason"]
    assert remote.claim("pc-1") is None
    v = env.reply(v, type="SELECT_CANDIDATE", candidate_index=0)  # 확인 못 한 후보는 막지 않는다
    assert v["state"] != "CANDIDATES_READY"


def test_claimed_but_no_result_times_out_and_late_result_is_refused(env, remote):
    v = env.flow("CANDIDATES_READY")
    job = remote.claim("pc-1")
    env.clock.advance(seconds=91)
    assert _view(env, v)["results"][0]["status"] == "ERROR"
    assert remote.submit(job["job_id"], []) == {"accepted": False}


def test_recent_checks_are_sent_as_cached(env, remote):
    v = env.flow("CANDIDATES_READY")
    _work(remote, {})
    env.clock.advance(minutes=1)
    v2 = env.flow("CANDIDATES_READY")
    job = remote.claim("pc-1")
    assert all(t["cached"] and t["cached"]["status"] == "OK" for t in job["targets"])
    remote.submit(job["job_id"], [
        {"index": t["index"], "restaurant_id": t["restaurant_id"], "status": "OK", "cached": True}
        for t in job["targets"]
    ])
    assert [r["status"] for r in _view(env, v2)["results"]] == ["OK", "OK", "OK"]


def test_recheck_replaces_queued_job(env, remote):
    v = env.flow("CANDIDATES_READY")
    env.svc().live_recheck("U001", v["request_id"])
    assert len(live_service._remote) == 1


def test_worker_api_claim_and_result(env, remote, session_factory, monkeypatch):
    from app.modules.cart import router as cart_router

    monkeypatch.setattr(cart_router, "_live", lambda: remote)
    app = create_app()

    def session():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[o_router.get_service] = lambda: env.svc()
    client = TestClient(app)
    assert client.post("/orchestrator/cart-worker/live-checks/claim", json={}).status_code in (401, 403)
    from app.modules.discovery.security import require_service_token

    app.dependency_overrides[require_service_token] = lambda: None
    assert client.post("/orchestrator/cart-worker/live-checks/claim", json={}, headers=TOKEN).status_code == 204
    v = env.flow("CANDIDATES_READY")
    job = client.post("/orchestrator/cart-worker/live-checks/claim", json={"worker": "pc"}, headers=TOKEN).json()
    body = {"results": [{"index": t["index"], "restaurant_id": t["restaurant_id"], "status": "OK", "page": _open_page(t)} for t in job["targets"]]}
    r = client.post(f"/orchestrator/cart-worker/live-checks/{job['job_id']}/result", json=body, headers=TOKEN)
    assert r.status_code == 200 and r.json()["current"] is True
    again = client.post(f"/orchestrator/cart-worker/live-checks/{job['job_id']}/result", json=body, headers=TOKEN)
    assert again.status_code == 409
    assert _view(env, v)["state"] == "DONE"


def test_worker_side_check_uses_cache_and_opens_each_store_once(monkeypatch):
    from app.modules.livecheck import checker

    opened = []

    def fake_browser_checker(context, **kw):
        async def check(t):
            opened.append(t.restaurant_id)
            return CheckResult(t.restaurant_id, "OK", page={"menus": [{"id": "1", "text": "메뉴 1,000원"}]}, seconds=0.1)
        return check

    class Ctx:
        async def add_init_script(self, s): pass
        async def close(self): self.closed = True

    class Browser:
        async def new_context(self, **kw):
            self.ctx = Ctx(); return self.ctx

    monkeypatch.setattr(checker, "browser_checker", fake_browser_checker)
    raw = [{"restaurant_id": rid, "items": []} for rid in ("A", "A", "B", "C")]
    targets = [target_from_json(t) for t in raw]
    b = Browser()
    out = asyncio.run(check_on_browser(b, targets, cached={2: {"status": "OK", "reason": None}}, want=5))
    assert [r.status for r in out] == ["OK", "OK", "OK", "OK"]
    assert opened == ["A", "C"]  # 같은 식당은 한 번, 최근 확인(B)은 열지 않음
    assert out[2].cached is True and b.ctx.closed
