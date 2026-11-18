"""요기요 장바구니 담기 (O-13 시작 · O-14 결과). 가짜 구현(app/modules/cart/fake.py)으로 확인한다.

진짜 요기요(Playwright)는 CI에서 돌리지 않는다 → scripts/yogiyo_cart_try.py로 손으로 확인.
"""

from concurrent.futures import Executor, Future

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_session
from app.main import create_app
from app.modules.cart.service import CartService, CartSettings, _data_url
from app.modules.cart.yogiyo import CartItem, CartOrder, CartResult, _compare
from app.modules.orchestrator import router as o_router
from tests.orchestrator.conftest import OK_MESSAGE

USER = {"Authorization": "Bearer dev-token-U001"}
OTHER = {"Authorization": "Bearer dev-token-U002"}


class Inline(Executor):
    """작업을 바로 실행 (테스트용)."""

    def submit(self, fn, /, *args, **kwargs):
        f: Future = Future()
        f.set_result(fn(*args, **kwargs))
        return f


class Held(Executor):
    """작업을 실행하지 않고 쌓아만 둔다 (담는 중 상태 확인용)."""

    def __init__(self):
        self.jobs = []

    def submit(self, fn, /, *args, **kwargs):
        self.jobs.append((fn, args))
        return Future()


def _client(env, session_factory, clock, *, mode="fake", executor=None):
    app = create_app()

    def session():
        with session_factory() as s:
            yield s

    def cart():
        with session_factory() as s:
            yield CartService(
                s,
                session_factory,
                settings=CartSettings(mode=mode, out_dir="cart_runs_test"),
                executor=executor or Inline(),
                now=clock,
            )

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[o_router.get_service] = lambda: env.svc()
    app.dependency_overrides[o_router.get_cart] = cart
    return TestClient(app)


@pytest.fixture
def client(env, session_factory, clock):
    return _client(env, session_factory, clock)


def _url(v) -> str:
    return f"/orchestrator/runs/{v['request_id']}/yogiyo-cart"


def test_cart_needs_approval_stage(env, client):
    v = env.flow("PAYMENT_METHOD_SELECTION")
    r = client.post(_url(v), headers=USER)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CART_NOT_AVAILABLE"


def test_cart_ready_matches_quote(env, client):
    v = env.flow("AWAITING_APPROVAL")
    assert client.get(_url(v), headers=USER).json() == {"status": "NONE"}
    r = client.post(_url(v), headers=USER)
    assert r.status_code == 202
    assert r.json()["status"] in ("QUEUED", "CART_READY")

    got = client.get(_url(v), headers=USER).json()
    assert got["status"] == "CART_READY"
    assert got["mode"] == "fake"
    order = v["prompt"]["order"]
    assert got["menu_subtotal_krw"] == order["menu_subtotal_krw"]
    assert got["delivery_fee_krw"] == order["delivery_fee_krw"]
    assert [i["quantity"] for i in got["cart_items"]] == [i["quantity"] for i in order["items"]]
    assert got["mismatches"] == []
    assert got["finished_at"] is not None


def test_cart_mismatch_is_reported(env, client):
    v = env.flow("AWAITING_APPROVAL", message=OK_MESSAGE + " #요기요불일치")
    client.post(_url(v), headers=USER)
    got = client.get(_url(v), headers=USER).json()
    assert got["status"] == "CART_MISMATCH"
    assert any("메뉴 합계" in m for m in got["mismatches"])


def test_cart_failure_is_kept_as_result(env, client):
    v = env.flow("AWAITING_APPROVAL", message=OK_MESSAGE + " #요기요실패")
    client.post(_url(v), headers=USER)
    got = client.get(_url(v), headers=USER).json()
    assert got["status"] == "FAILED"
    assert "식당 페이지" in got["error"]


def test_cart_allowed_after_payment(env, client):
    v = env.flow("COMPLETED")
    client.post(_url(v), headers=USER)
    assert client.get(_url(v), headers=USER).json()["status"] == "CART_READY"


def test_other_user_cannot_see_or_start(env, client):
    v = env.flow("AWAITING_APPROVAL")
    assert client.post(_url(v), headers=OTHER).status_code == 404
    assert client.get(_url(v), headers=OTHER).status_code == 404
    assert client.get(_url(v)).status_code == 401


def test_double_click_reuses_running_job(env, session_factory, clock):
    held = Held()
    c = _client(env, session_factory, clock, executor=held)
    v = env.flow("AWAITING_APPROVAL")
    first = c.post(_url(v), headers=USER).json()
    second = c.post(_url(v), headers=USER).json()
    assert first["job_id"] == second["job_id"]
    assert len(held.jobs) == 1  # 요기요에는 한 번만 간다
    assert c.get(_url(v), headers=USER).json()["status"] == "QUEUED"


def test_cart_disabled(env, session_factory, clock):
    c = _client(env, session_factory, clock, mode="off")
    v = env.flow("AWAITING_APPROVAL")
    r = c.post(_url(v), headers=USER)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "CART_DISABLED"


def test_filler_crash_becomes_failed(env, session_factory, clock):
    v = env.flow("AWAITING_APPROVAL")

    def boom(order, **_):
        raise RuntimeError("브라우저 실행 실패")

    with session_factory() as s:
        cart = CartService(s, session_factory, filler=boom, executor=Inline(), now=clock)
        cart.start(**env.svc().cart_target("U001", v["request_id"]))
        got = cart.latest(v["request_id"])
    assert got["status"] == "FAILED"
    assert "브라우저 실행 실패" in got["error"]


# ---- 진짜 구현의 대조 규칙·캡처 (Playwright 없이 확인 가능한 부분) ----
def test_compare_finds_quantity_price_and_fee_differences():
    order = CartOrder(
        restaurant_id="1016816",
        items=[CartItem(menu_id="M1", quantity=2)],
        expected_menu_subtotal_krw=38000,
        expected_delivery_fee_krw=1000,
    )
    same = CartResult(
        status="",
        restaurant_id="1016816",
        cart_items=[{"menu_id": "M1", "quantity": 2, "unit_price_krw": 19000}],
        menu_subtotal_krw=38000,
        delivery_fee_krw=1000,
    )
    assert _compare(order, same) == []
    diff = CartResult(
        status="",
        restaurant_id="1016816",
        cart_items=[{"menu_id": "M1", "quantity": 1, "unit_price_krw": 19000}],
        menu_subtotal_krw=19000,
        delivery_fee_krw=2000,
    )
    msgs = _compare(order, diff)
    assert len(msgs) == 3


def test_screenshot_is_returned_as_data_url(tmp_path):
    p = tmp_path / "shot.jpg"
    p.write_bytes(b"\xff\xd8\xff")
    assert _data_url(str(p)).startswith("data:image/jpeg;base64,")
    assert _data_url(str(tmp_path / "none.jpg")) is None


# ---------------------------------------------------------------- 요기요 장바구니 형식 읽기
def test_parse_cart_handles_string_options_and_double_encoding():
    import json

    from app.modules.cart.yogiyo import _parse_cart

    raw = {
        "restaurant_id": 293700,
        "items": [
            {
                "id": 680482719,
                "name": "고구마통모짜 핫도그",
                "price": 4400,
                "amount": 10,
                "subchoices": ["설탕", {"name": "케찹"}, [{"title": "머스타드"}], None],
            },
            "깨진 항목",
        ],
    }
    items, rid = _parse_cart(json.dumps(json.dumps(raw)))
    assert rid == 293700
    assert items == [
        {
            "menu_id": "680482719",
            "name": "고구마통모짜 핫도그",
            "unit_price_krw": 4400,
            "quantity": 10,
            "options": ["설탕", "케찹", "머스타드"],
        }
    ]


def test_release_browser_closes_kept_window_on_new_order(monkeypatch):
    from app.modules.cart import service as cart_service

    calls = []

    class FakeKeeper:
        def close(self):
            calls.append("close")

    monkeypatch.setattr(cart_service, "_kept", FakeKeeper())
    monkeypatch.setattr(cart_service, "default_executor", lambda: Inline())
    cart_service.release_browser()
    assert calls == ["close"]


def test_settings_treat_blank_env_as_default(monkeypatch):
    from app.modules.cart.service import CartSettings

    for k in (
        "YOGIYO_CART_MODE",
        "YOGIYO_CART_HEADLESS",
        "YOGIYO_CART_KEEP_BROWSER",
        "YOGIYO_CART_KEEP_OPEN_SECONDS",
        "YOGIYO_CART_DIR",
    ):
        monkeypatch.setenv(k, "")
    cfg = CartSettings()
    assert (cfg.mode, cfg.headless, cfg.keep_browser, cfg.keep_open_seconds, cfg.out_dir) == (
        "fake",
        True,
        True,
        0.0,
        "cart_runs",
    )
    monkeypatch.setenv("YOGIYO_CART_KEEP_OPEN_SECONDS", "abc")
    assert CartSettings().keep_open_seconds == 0.0


# ---------------------------------------------------------------- 담기 실패 시 승인 막기
def _approve(client, v):
    return client.post(
        f"/orchestrator/runs/{v['request_id']}/reply",
        json={"type": "APPROVE", "expected_state_version": v["state_version"]},
        headers=USER,
    )


def test_approve_blocked_after_cart_failure(env, client):
    v = env.flow("AWAITING_APPROVAL", message=OK_MESSAGE + " #요기요실패")
    client.post(_url(v), headers=USER)
    r = _approve(client, v)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CART_FAILED"


def test_approve_blocked_while_cart_running(env, session_factory, clock):
    c = _client(env, session_factory, clock, executor=Held())
    v = env.flow("AWAITING_APPROVAL")
    c.post(_url(v), headers=USER)
    r = _approve(c, v)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CART_IN_PROGRESS"


def test_approve_allowed_without_cart_or_after_success(env, client):
    v = env.flow("AWAITING_APPROVAL")
    assert _approve(client, v).status_code == 200  # 담기를 안 해도 승인 가능

    v2 = env.flow("AWAITING_APPROVAL")
    client.post(_url(v2), headers=USER)
    assert _approve(client, v2).status_code == 200


def test_approve_allowed_on_mismatch(env, client):
    v = env.flow("AWAITING_APPROVAL", message=OK_MESSAGE + " #요기요불일치")
    client.post(_url(v), headers=USER)
    assert _approve(client, v).status_code == 200


def test_other_user_approve_still_404(env, client):
    v = env.flow("AWAITING_APPROVAL")
    r = client.post(
        f"/orchestrator/runs/{v['request_id']}/reply",
        json={"type": "APPROVE", "expected_state_version": v["state_version"]},
        headers=OTHER,
    )
    assert r.status_code == 404
