"""요기요 실시간 식당 확인 작업 (이슈 ②): 후보 상위 N곳을 보여 줄 때 백그라운드로 확인한다.

- O가 후보를 정하면(CANDIDATES_READY) `start`로 작업을 맡긴다. 후보 화면은 기다리지 않는다.
- 확인 결과는 식당별 한 행(restaurant_live_status)에 덮어쓴다. 주문마다 쌓지 않는다.
- 후보별 판정은 저장하지 않고, 읽을 때 식당 화면 원본으로 다시 계산한다(`view`·`verdict`).
- 작업은 장바구니 담기와 같은 작업 스레드(작업자 1개)에서 돈다. 요기요 창이 겹쳐 뜨지 않는다.

설정 (.env)
- LIVE_CHECK_MODE             : fake(기본, 요기요 접속 없이 시연) | real | off(확인 안 함)
- LIVE_CHECK_TOP_N=6          : A-05에서 받는 후보 수
- LIVE_CHECK_WANT=3           : 통과(OK·CHANGED)가 이만큼이면 나머지는 확인하지 않음
- LIVE_CHECK_CONCURRENCY=3    : 동시에 여는 탭 수
- LIVE_CHECK_TIMEOUT_SECONDS=8: 식당 하나에서 메뉴를 기다리는 시간
- LIVE_CHECK_CACHE_MINUTES=15 : real 모드에서 이 시간 안에 확인한 식당은 다시 열지 않음
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core import clock as core_clock
from app.modules.livecheck.catalog import restaurant_info
from app.modules.livecheck.checker import (
    PASSED,
    CheckItem,
    CheckResult,
    CheckTarget,
    judge,
    observed_for,
    run_checks,
)
from app.modules.livecheck.fake import fake_page
from app.modules.livecheck.models import RestaurantLiveStatus

log = logging.getLogger("app.livecheck")

VALID_PAGE = ("OPEN", "NOT_SERVING", "REDIRECTED")  # 캐시로 다시 쓸 수 있는 화면 상태


def _env(name: str, default: str) -> str:
    value = (os.getenv(name) or "").strip()
    return value or default


def _num(name: str, default: float) -> float:
    try:
        return max(0.0, float(_env(name, str(default))))
    except ValueError:
        log.warning("%s 값이 숫자가 아니라 %s로 둔다", name, default)
        return default


@dataclass(frozen=True)
class LiveCheckSettings:
    mode: str = field(default_factory=lambda: _env("LIVE_CHECK_MODE", "fake").lower())
    top_n: int = field(default_factory=lambda: max(1, int(_num("LIVE_CHECK_TOP_N", 6))))
    want: int = field(default_factory=lambda: max(1, int(_num("LIVE_CHECK_WANT", 3))))
    concurrency: int = field(default_factory=lambda: max(1, int(_num("LIVE_CHECK_CONCURRENCY", 3))))
    timeout_seconds: float = field(default_factory=lambda: _num("LIVE_CHECK_TIMEOUT_SECONDS", 8))
    cache_minutes: float = field(default_factory=lambda: _num("LIVE_CHECK_CACHE_MINUTES", 15))
    headless: bool = field(
        default_factory=lambda: _env("YOGIYO_CART_HEADLESS", "0") not in ("0", "false")
    )
    browser_channel: str = field(default_factory=lambda: _env("YOGIYO_CART_BROWSER", ""))

    @property
    def enabled(self) -> bool:
        return self.mode in ("fake", "real")


def page_status(page: dict | None) -> str:
    if not page:
        return "ERROR"
    if page.get("denied"):
        return "BLOCKED"
    if page.get("not_serving"):
        return "NOT_SERVING"
    if page.get("redirected"):
        return "REDIRECTED"
    return "OPEN" if page.get("menus") else "ERROR"


def target_of(candidate: dict) -> CheckTarget:
    """A-05 후보 → 확인 대상. 배달비·최소 주문 금액은 수집 데이터의 기본값으로 비교한다."""
    r = candidate["restaurant"]
    info = restaurant_info(r["restaurant_id"])
    return CheckTarget(
        restaurant_id=str(r["restaurant_id"]),
        name=r.get("name"),
        items=[
            CheckItem(str(i["menu_id"]), i.get("unit_price_krw"), i.get("name"))
            for i in candidate.get("items", [])
        ],
        expected_delivery_fee_krw=info.get("base_delivery_fee_krw"),
        expected_min_order_krw=info.get("min_order_krw"),
    )


# ---------------------------------------- 진행 중인 작업 (프로세스 안에만 둔다)
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _job(request_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(request_id)
        return dict(job) if job else None


def _set_job(request_id: str, **values) -> None:
    with _jobs_lock:
        _jobs.setdefault(request_id, {}).update(values)


Observer = Callable[..., object]


class LiveCheckService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        settings: LiveCheckSettings | None = None,
        executor: Executor | None = None,
        now: Callable[[], datetime] = core_clock.now,
    ):
        self.sf = session_factory
        self.cfg = settings or LiveCheckSettings()
        self._executor = executor
        self.now = now

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    @property
    def executor(self) -> Executor:
        if self._executor is None:  # 장바구니와 같은 작업 스레드 (요기요 창이 겹치지 않게)
            from app.modules.cart.service import default_executor

            self._executor = default_executor()
        return self._executor

    # ------------------------------------------------------------ 시작
    def start(
        self, request_id: str, candidates: list[dict], message: str, started_at: datetime
    ) -> None:
        """후보 확인을 작업 스레드에 맡긴다 (주문 저장이 끝난 뒤 부른다)."""
        if not self.enabled or not candidates:
            return
        _set_job(request_id, state="RUNNING", started_at=started_at, skipped=[], error=None)
        self.executor.submit(self._run, request_id, list(candidates), message, started_at)

    def _run(self, request_id: str, candidates: list[dict], message: str, started_at) -> None:
        try:
            results = asyncio.run(self._check_all(candidates, message, started_at))
            skipped = [
                c["index"]
                for c, r in zip(candidates, results, strict=True)
                if r.status == "SKIPPED"
            ]
            reason = next((r.reason for r in results if r.status == "SKIPPED"), None)
            _set_job(request_id, state="DONE", skipped=skipped, stop_reason=reason)
        except Exception as exc:  # noqa: BLE001 — 브라우저를 못 띄워도 주문은 계속된다
            log.exception("요기요 실시간 확인 실패")
            _set_job(request_id, state="DONE", error=f"{type(exc).__name__}: {exc}")

    async def _check_all(self, candidates: list[dict], message: str, started_at) -> list:
        targets = [target_of(c) for c in candidates]
        rank = {id(t): i for i, t in enumerate(targets)}
        locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        async def with_cache(observe, t: CheckTarget) -> CheckResult:
            async with locks[t.restaurant_id]:  # 같은 식당이 두 후보에 있으면 한 번만 연다
                t0 = time.monotonic()
                row = self._fresh_row(t.restaurant_id, started_at)
                if row is not None:
                    page, cached, err = row.page, True, row.error
                else:
                    page, err = await observe(t)
                    cached = False
                    self._save(t.restaurant_id, page, err)
                status, changes, reason = self._judge(t, page, err)
                return CheckResult(
                    t.restaurant_id,
                    status,
                    changes,
                    reason,
                    seconds=time.monotonic() - t0,
                    page=page,
                    cached=cached,
                )

        opts = dict(concurrency=self.cfg.concurrency, want=self.cfg.want)
        if self.cfg.mode == "fake":

            async def fake(t: CheckTarget):
                return fake_page(t, rank=rank[id(t)], message=message), None

            return await run_checks(targets, lambda t: with_cache(fake, t), **opts)

        from playwright.async_api import async_playwright

        from app.modules.cart.yogiyo import DEMO_LOCATION, _init_script, _launch
        from app.modules.livecheck.checker import browser_checker

        loc = json.loads(DEMO_LOCATION.read_text(encoding="utf-8"))
        async with async_playwright() as p:
            browser = await _launch(
                p, headless=self.cfg.headless, channel=self.cfg.browser_channel or None
            )
            try:
                context = await browser.new_context(
                    locale="ko-KR", viewport={"width": 1280, "height": 900}
                )
                await context.add_init_script(_init_script(loc))
                check = browser_checker(context, timeout_ms=int(self.cfg.timeout_seconds * 1000))

                async def real(t: CheckTarget):
                    r = await check(t)
                    return r.page, (r.reason if r.page is None else None)

                return await run_checks(targets, lambda t: with_cache(real, t), **opts)
            finally:
                await browser.close()

    # ------------------------------------------------------------ 저장 (식당당 한 행)
    def _fresh_row(self, restaurant_id: str, started_at: datetime) -> RestaurantLiveStatus | None:
        """이번 작업에서 다시 쓸 수 있는 최근 확인. fake 결과는 다른 주문에 쓰지 않는다."""
        with self.sf() as s:
            row = s.get(RestaurantLiveStatus, restaurant_id)
            if row is None or row.page_status not in VALID_PAGE or row.checked_at is None:
                return None
            if row.mode != self.cfg.mode:
                return None
            since = started_at
            if self.cfg.mode == "real":
                since = started_at - timedelta(minutes=self.cfg.cache_minutes)
            if row.checked_at < since:
                return None
            s.expunge(row)
            return row

    def _save(self, restaurant_id: str, page: dict | None, error: str | None) -> None:
        with self.sf() as s:
            row = s.get(RestaurantLiveStatus, restaurant_id)
            if row is None:
                row = RestaurantLiveStatus(restaurant_id=restaurant_id)
                s.add(row)
            row.page_status = page_status(page)
            row.page, row.error, row.mode = page, error, self.cfg.mode
            row.checking_since, row.checked_at = None, self.now()
            s.commit()

    @staticmethod
    def _judge(t: CheckTarget, page: dict | None, error: str | None):
        if page is None:
            return "ERROR", [], error or "확인하지 못했어요"
        return judge(t, observed_for(page, t))

    # ------------------------------------------------------------ 조회 (O-15)
    def results(self, request_id: str, candidates: list[dict], started_at: datetime | None) -> list:
        job = _job(request_id) or {}
        out = []
        with self.sf() as s:
            for c in candidates:
                t = target_of(c)
                row = s.get(RestaurantLiveStatus, t.restaurant_id)
                out.append(self._candidate_result(c, t, row, job, started_at))
        return out

    def _row_counts(self, row, started_at) -> bool:
        if row is None or row.checked_at is None or started_at is None or row.mode != self.cfg.mode:
            return False
        since = started_at
        if self.cfg.mode == "real" and row.page_status in VALID_PAGE:
            since = started_at - timedelta(minutes=self.cfg.cache_minutes)
        return row.checked_at >= since

    def _candidate_result(self, c: dict, t: CheckTarget, row, job: dict, started_at) -> dict:
        base = {"candidate_index": c["index"], "restaurant_id": t.restaurant_id}
        if self._row_counts(row, started_at):
            status, changes, reason = self._judge(t, row.page, row.error)
            if row.page_status in ("BLOCKED", "ERROR") and status not in ("BLOCKED", "ERROR"):
                status = row.page_status
            return {
                **base,
                "status": status,
                "changes": changes,
                "reason": reason,
                "checked_at": row.checked_at.isoformat(),
                "observed": {
                    "delivery_fee_krw": (row.page or {}).get("delivery_fee_krw"),
                    "min_order_krw": (row.page or {}).get("min_order_krw"),
                },
            }
        state = job.get("state")
        if state == "RUNNING":
            status, reason = "CHECKING", None
        elif c["index"] in (job.get("skipped") or []):
            status, reason = "SKIPPED", job.get("stop_reason")
        elif job.get("error"):
            status, reason = "ERROR", f"확인하지 못했어요 ({job['error']})"
        elif started_at is None:
            status, reason = "NOT_CHECKED", None
        else:
            status, reason = "SKIPPED", job.get("stop_reason")
        return {**base, "status": status, "changes": [], "reason": reason, "checked_at": None}

    def view(self, request_id: str, candidates: list[dict], started_at: datetime | None) -> dict:
        if not self.enabled:
            return {"mode": self.cfg.mode, "state": "OFF", "total": 0, "done": 0, "results": []}
        results = self.results(request_id, candidates, started_at)
        # 화면에 보일 후보: 고를 수 있는 후보가 want곳 나올 때까지 순위대로.
        # 주문 불가 후보는 숨기지 않고 제자리에 사유와 함께 보이되 고를 수 없다(selectable=False).
        selectable_shown = 0
        for r in results:
            r["selectable"] = r["status"] != "UNAVAILABLE"
            r["visible"] = selectable_shown < self.cfg.want
            selectable_shown += r["visible"] and r["selectable"]
        job = _job(request_id) or {}
        running = job.get("state") == "RUNNING" or any(r["status"] == "CHECKING" for r in results)
        finals = [r for r in results if r["status"] not in ("CHECKING",)]
        return {
            "mode": self.cfg.mode,
            "state": "RUNNING" if running else ("DONE" if started_at else "NONE"),
            "total": len(results),
            "done": len(finals),
            "passed": sum(r["status"] in PASSED for r in results),
            "want": self.cfg.want,
            "all_unavailable": bool(results)
            and not running
            and all(r["status"] == "UNAVAILABLE" for r in results),
            "started_at": started_at.isoformat() if started_at else None,
            "results": results,
        }

    # ------------------------------------------------------------ 후보 선택 때 판정 (O-02)
    def verdict(
        self, request_id: str, candidate: dict, started_at, *, wait_seconds: float | None = None
    ) -> dict:
        """선택한 후보의 판정. 확인 중이면 끝날 때까지 잠깐 기다린다."""
        wait = self.cfg.timeout_seconds + 2 if wait_seconds is None else wait_seconds
        deadline = time.monotonic() + wait
        while True:
            r = self.results(request_id, [candidate], started_at)[0]
            if r["status"] != "CHECKING" or time.monotonic() >= deadline:
                return r
            time.sleep(0.25)
