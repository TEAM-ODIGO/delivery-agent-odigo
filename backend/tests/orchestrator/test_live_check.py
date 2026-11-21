"""요기요 실시간 식당 확인 O 연결 (이슈 ②). 가짜 확인(LIVE_CHECK_MODE=fake)으로 확인한다.

- 후보 상위 6곳을 A-05에서 받고, 후보 화면에 들어서면(CANDIDATES_READY) 백그라운드로 확인한다.
- 주문 불가로 확인된 후보는 고를 수 없다 (409 CANDIDATE_UNAVAILABLE).
- 확인 결과는 식당당 한 행(restaurant_live_status)만 쓴다.
"""

import functools
from concurrent.futures import Executor, Future
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.db import get_session
from app.core.errors import AppError
from app.main import create_app
from app.modules.livecheck import service as live_service
from app.modules.livecheck.checker import CheckItem, CheckTarget
from app.modules.livecheck.models import RestaurantLiveStatus
from app.modules.livecheck.service import LiveCheckService, LiveCheckSettings
from app.modules.orchestrator import router as o_router
from tests.orchestrator.conftest import OK_MESSAGE

USER = {"Authorization": "Bearer dev-token-U001"}
OTHER = {"Authorization": "Bearer dev-token-U002"}


class Inline(Executor):
    def submit(self, fn, /, *args, **kwargs):
        f: Future = Future()
        f.set_result(fn(*args, **kwargs))
        return f


class Held(Executor):
    """작업을 실행하지 않고 쌓아 둔다 (확인 중 상태 시험용)."""

    def __init__(self):
        self.jobs = []

    def submit(self, fn, /, *args, **kwargs):
        self.jobs.append((fn, args))
        return Future()


def _live(env, executor=None, **cfg):
    settings = LiveCheckSettings(mode=cfg.pop("mode", "fake"), timeout_seconds=0, **cfg)
    return LiveCheckService(env.sf, settings=settings, executor=executor or Inline(), now=env.clock)


@pytest.fixture
def live(env):
    # 가짜 A는 이 문장에 후보 3곳을 준다 → 통과 2곳이면 멈추게 해서 예비 후보 동작을 본다
    svc = _live(env, want=2)
    env.svc = functools.partial(type(env).svc, env, live_check=svc)
    return svc


def _view(env, v):
    return env.svc().live_check_view("U001", v["request_id"])


def _select(env, v, index):
    return env.reply(v, type="SELECT_CANDIDATE", candidate_index=index)


# ---------------------------------------------------------------- 시작과 결과
def test_checks_top_candidates_when_candidates_ready(env, live):
    v = env.flow("CANDIDATES_READY")
    lc = _view(env, v)
    assert lc["mode"] == "fake" and lc["state"] == "DONE"
    assert lc["total"] == len(v["prompt"]["candidates"]) == 3
    assert [r["status"] for r in lc["results"]] == ["OK", "OK", "SKIPPED"]  # 2곳 통과 → 멈춤
    assert [r["visible"] for r in lc["results"]] == [True, True, False]
    assert [r["selectable"] for r in lc["results"]] == [True, True, True]
    assert lc["passed"] == 2 and lc["done"] == 3 and not lc["all_unavailable"]


def test_asks_a_for_top_n_candidates(env, monkeypatch):
    seen = []
    search = env.world.discovery.search
    monkeypatch.setattr(
        env.world.discovery, "search", lambda rid, body: seen.append(body) or search(rid, body)
    )
    live = _live(env)
    env.svc = functools.partial(type(env).svc, env, live_check=live)
    env.flow("CANDIDATES_READY")
    assert seen[-1]["top_n"] == 6


def test_closed_candidate_is_hidden_and_cannot_be_selected(env, live):
    v = env.flow("CANDIDATES_READY", message=OK_MESSAGE + " #요기요닫힘")
    lc = _view(env, v)
    first = lc["results"][0]
    assert first["status"] == "UNAVAILABLE" and first["reason"] == "오늘 임시 휴무예요"
    # 주문 불가 후보는 사유와 함께 제자리에 보이되 못 고르고, 다음 순번이 빈자리를 채운다
    assert [r["visible"] for r in lc["results"]] == [True, True, True]
    assert [r["selectable"] for r in lc["results"]] == [False, True, True]

    with pytest.raises(AppError) as e:
        _select(env, v, first["candidate_index"])
    assert e.value.status == 409 and e.value.code == "CANDIDATE_UNAVAILABLE"
    after = env.get(v)
    assert after["state"] == "CANDIDATES_READY" and after["state_version"] == v["state_version"]
    logged = [x for x in env.events(v) if x["event_type"] == "LIVE_CHECKED"]
    assert logged and logged[-1]["detail"]["status"] == "UNAVAILABLE"

    nxt = _select(env, after, lc["results"][1]["candidate_index"])
    assert nxt["state"] != "CANDIDATES_READY"


def test_price_change_is_shown_but_selectable(env, live):
    v = env.flow("CANDIDATES_READY", message=OK_MESSAGE + " #요기요가격변경")
    first = _view(env, v)["results"][0]
    assert first["status"] == "CHANGED" and "→" in first["changes"][0]
    nxt = _select(env, v, first["candidate_index"])
    assert nxt["state"] != "CANDIDATES_READY"
    logged = [x for x in env.events(v) if x["event_type"] == "LIVE_CHECKED"]
    assert logged[-1]["detail"]["status"] == "CHANGED"


def test_all_closed_is_reported(env, live):
    v = env.flow("CANDIDATES_READY", message=OK_MESSAGE + " #요기요전부닫힘")
    lc = _view(env, v)
    assert lc["all_unavailable"] is True and lc["passed"] == 0
    assert all(r["visible"] for r in lc["results"])  # 모두 사유와 함께 보인다
    assert not any(r["selectable"] for r in lc["results"])


def test_checking_candidate_is_not_blocked_after_wait(env):
    held = Held()
    live = _live(env, executor=held)
    env.svc = functools.partial(type(env).svc, env, live_check=live)
    v = env.flow("CANDIDATES_READY")
    lc = _view(env, v)
    assert lc["state"] == "RUNNING" and lc["done"] == 0
    assert {r["status"] for r in lc["results"]} == {"CHECKING"}
    assert len(held.jobs) == 1  # 저장 뒤에 한 번만 맡김
    # 확인이 안 끝났어도 막지 않는다 (확인 못 한 후보는 장바구니 단계에서 다시 걸러짐)
    assert _select(env, v, 0)["state"] != "CANDIDATES_READY"


def test_off_mode_keeps_old_flow(env):
    off = _live(env, mode="off")
    env.svc = functools.partial(type(env).svc, env, live_check=off)
    v = env.flow("CANDIDATES_READY")
    assert len(v["prompt"]["candidates"]) <= 3
    assert _view(env, v)["state"] == "OFF"


# ---------------------------------------------------------------- 식당당 한 행
def _rows(env):
    with env.sf() as s:
        return s.scalar(select(func.count()).select_from(RestaurantLiveStatus))


def test_one_row_per_restaurant_and_rechecks_overwrite(env, live):
    env.flow("CANDIDATES_READY")
    first = _rows(env)
    env.flow("CANDIDATES_READY")  # 같은 식당들을 다시 확인
    env.flow("CANDIDATES_READY")
    assert 0 < first == _rows(env)  # 주문이 늘어도 행은 식당 수만큼


def test_same_restaurant_in_two_candidates_opens_once(env, monkeypatch):
    live = _live(env)
    calls = []
    real_fake = live_service.fake_page

    def counting(t, **kw):
        calls.append(t.restaurant_id)
        return real_fake(t, **kw)

    monkeypatch.setattr(live_service, "fake_page", counting)
    cand = {
        "index": 0,
        "candidate_id": "C1",
        "restaurant": {"restaurant_id": "R1", "name": "가게"},
        "items": [{"menu_id": "m1", "name": "치킨", "unit_price_krw": 10000}],
    }
    other = {**cand, "index": 1, "candidate_id": "C2", "items": [{"menu_id": "m2", "name": "콜라"}]}
    live.start("REQ-SAME", [cand, other], "", env.clock())
    assert calls == ["R1"]


def test_real_cache_window(env):
    live = _live(env, mode="real", cache_minutes=15)
    now = env.clock()
    with env.sf() as s:
        for rid, ago, mode in (("R-new", 10, "real"), ("R-old", 20, "real"), ("R-fake", 1, "fake")):
            s.add(
                RestaurantLiveStatus(
                    restaurant_id=rid,
                    page_status="OPEN",
                    page={"menus": [{"id": "m1", "text": "치킨 1,000원"}]},
                    mode=mode,
                    checked_at=now - timedelta(minutes=ago),
                )
            )
        s.commit()
    assert live._fresh_row("R-new", now) is not None
    assert live._fresh_row("R-old", now) is None  # 15분 지남
    assert live._fresh_row("R-fake", now) is None  # fake 결과는 real에 안 씀


def test_target_uses_catalog_base_fee():
    from app.modules.livecheck.service import target_of

    t = target_of(
        {
            "restaurant": {"restaurant_id": "355987", "name": "써브웨이", "delivery_fee_krw": 0},
            "items": [{"menu_id": "845312757", "name": "칩", "unit_price_krw": 2100}],
        }
    )
    assert isinstance(t, CheckTarget) and t.items == [CheckItem("845312757", 2100, "칩")]
    assert t.expected_delivery_fee_krw == 3400  # 후보의 구간 배달비가 아니라 CSV 기본 배달비


# ---------------------------------------------------------------- API (O-15 · O-16)
@pytest.fixture
def client(env, live, session_factory):
    app = create_app()

    def session():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[o_router.get_service] = lambda: env.svc()
    return TestClient(app)


def test_api_view_and_recheck(env, client):
    v = env.flow("CANDIDATES_READY")
    url = f"/orchestrator/runs/{v['request_id']}/live-check"
    got = client.get(url, headers=USER)
    assert got.status_code == 200 and got.json()["state"] == "DONE"
    assert client.get(url, headers=OTHER).status_code == 404
    assert client.get(url).status_code == 401
    again = client.post(url, headers=USER)
    assert again.status_code == 200 and again.json()["passed"] == 2


def test_api_recheck_only_while_choosing(env, client):
    v = env.flow("AWAITING_APPROVAL")
    r = client.post(f"/orchestrator/runs/{v['request_id']}/live-check", headers=USER)
    assert r.status_code == 409


# ---------------------------------------- real 모드 (이슈 ④): 브라우저 없이 흉내
class FakeContext:
    def __init__(self):
        self.closed = False

    async def add_init_script(self, script):
        pass

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.contexts = []

    async def new_context(self, **kw):
        self.contexts.append(FakeContext())
        return self.contexts[-1]


class FakeKeeper:
    """장바구니와 함께 쓰는 브라우저(BrowserKeeper) 흉내."""

    def __init__(self):
        self.browser = FakeBrowser()
        self.calls = []

    def with_browser(self, fn, *, headless=False, browser_channel=None):
        import asyncio

        self.calls.append({"headless": headless, "channel": browser_channel})
        return asyncio.run(fn(self.browser))


@pytest.fixture
def real_live(env, monkeypatch):
    from app.modules.livecheck import checker
    from app.modules.livecheck.checker import CheckResult

    opened = []

    def fake_browser_checker(context, **kw):
        async def check(t):
            opened.append(t.restaurant_id)
            menus = [
                {"id": i.menu_id, "text": f"{i.name} {i.expected_price_krw or 0:,}원"}
                for i in t.items
            ]
            page = {"url": f"/#/{t.restaurant_id}/", "menus": menus}
            return CheckResult(t.restaurant_id, "OK", page=page, seconds=0.01)

        return check

    monkeypatch.setattr(checker, "browser_checker", fake_browser_checker)
    keeper = FakeKeeper()
    settings = LiveCheckSettings(mode="real", timeout_seconds=0, want=2, cache_minutes=15)
    svc = LiveCheckService(
        env.sf, settings=settings, executor=Inline(), now=env.clock, keeper=keeper
    )
    env.svc = functools.partial(type(env).svc, env, live_check=svc)
    return svc, keeper, opened


def test_real_mode_shares_cart_browser_and_closes_its_window(env, real_live):
    svc, keeper, opened = real_live
    v = env.flow("CANDIDATES_READY")
    lc = _view(env, v)
    assert [r["status"] for r in lc["results"]] == ["OK", "OK", "SKIPPED"]
    assert len(keeper.calls) == 1 and keeper.calls[0]["headless"] is False  # 창을 띄우는 모드
    assert len(keeper.browser.contexts) == 1 and keeper.browser.contexts[0].closed  # 확인 창만 닫음
    assert lc["seconds"] is not None
    assert [r["cached"] for r in lc["results"][:2]] == [False, False]
    assert all(isinstance(r["seconds"], float) for r in lc["results"][:2])


def test_real_mode_reuses_recent_checks_for_15_minutes(env, real_live):
    svc, keeper, opened = real_live
    env.flow("CANDIDATES_READY")
    first = list(opened)
    v = env.flow("CANDIDATES_READY")  # 15분 안에 같은 후보 → 다시 열지 않음
    assert opened == first
    assert [r["cached"] for r in _view(env, v)["results"][:2]] == [True, True]

    env.clock.advance(minutes=16)
    v2 = env.flow("CANDIDATES_READY")
    assert len(opened) == 2 * len(first)  # 15분이 지나면 다시 연다
    assert [r["cached"] for r in _view(env, v2)["results"][:2]] == [False, False]


def test_real_mode_recheck_ignores_recent_checks(env, real_live):
    svc, keeper, opened = real_live
    v = env.flow("CANDIDATES_READY")
    first = list(opened)
    env.clock.advance(minutes=1)  # 1분 뒤 (15분 캐시 안)
    again = env.svc().live_recheck("U001", v["request_id"])  # "다시 확인" (O-16)
    assert len(opened) == 2 * len(first)  # 15분 안이어도 요기요를 다시 연다
    assert [r["cached"] for r in again["results"][:2]] == [False, False]
    assert [r["cached"] for r in _view(env, v)["results"][:2]] == [False, False]
