"""요기요 실시간 식당 확인 작업 (이슈 ②): 후보 상위 N곳을 보여 줄 때 백그라운드로 확인한다.

- O가 후보를 정하면(CANDIDATES_READY) `start`로 작업을 맡긴다. 후보 화면은 기다리지 않는다.
- 확인 결과는 식당별 한 행(restaurant_live_status)에 덮어쓴다. 주문마다 쌓지 않는다.
- 후보별 판정은 저장하지 않고, 읽을 때 식당 화면 원본으로 다시 계산한다(`view`·`verdict`).
- 작업은 장바구니 담기와 같은 작업 스레드(작업자 1개)에서 돈다. 요기요 창이 겹쳐 뜨지 않는다.

설정 (.env)
- LIVE_CHECK_MODE             : fake(기본, 요기요 접속 없이 시연) | real | remote(한국 PC 작업기, #80) | off(확인 안 함)
- LIVE_CHECK_TOP_N=6          : A-05에서 받는 후보 수
- LIVE_CHECK_WANT=3           : 통과(OK·CHANGED)가 이만큼이면 나머지는 확인하지 않음
- LIVE_CHECK_CONCURRENCY=3    : 동시에 여는 탭 수
- LIVE_CHECK_TIMEOUT_SECONDS=8: 식당 하나에서 메뉴를 기다리는 시간
- LIVE_CHECK_CACHE_MINUTES=15 : real·remote 모드에서 이 시간 안에 확인한 식당은 다시 열지 않음
- LIVE_CHECK_REMOTE_WAIT_SECONDS=20    : remote 모드에서 작업기가 이 안에 가져가지 않으면 '확인 못 함'
- LIVE_CHECK_REMOTE_TIMEOUT_SECONDS=90 : 가져간 뒤 이 안에 결과가 안 오면 '확인 못 함'
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
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
from app.modules.livecheck.remote import target_to_json
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
    remote_wait_seconds: float = field(
        default_factory=lambda: _num("LIVE_CHECK_REMOTE_WAIT_SECONDS", 20)
    )
    remote_timeout_seconds: float = field(
        default_factory=lambda: _num("LIVE_CHECK_REMOTE_TIMEOUT_SECONDS", 90)
    )

    @property
    def enabled(self) -> bool:
        return self.mode in ("fake", "real", "remote")


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


# remote 모드 (#80): PC 작업기가 가져갈 확인 작업. job_id → 작업 (프로세스 안에만 둔다)
_remote: dict[str, dict] = {}
_remote_lock = threading.Lock()
REMOTE_NOT_CLAIMED = "요기요 확인 PC(작업기)가 작업을 가져가지 않았어요"
REMOTE_NO_RESULT = "요기요 확인 PC(작업기)가 제시간에 결과를 보내지 않았어요"


Observer = Callable[..., object]


class LiveCheckService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        settings: LiveCheckSettings | None = None,
        executor: Executor | None = None,
        now: Callable[[], datetime] = core_clock.now,
        keeper=None,
    ):
        self.sf = session_factory
        self.cfg = settings or LiveCheckSettings()
        self._executor = executor
        self.now = now
        self._keeper = keeper

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    @property
    def keeper(self):
        """real 모드 브라우저: 장바구니 담기와 같은 브라우저를 쓴다 (요기요 창이 하나만 뜬다)."""
        if self._keeper is None:
            from app.modules.cart.service import _keeper

            self._keeper = _keeper()
        return self._keeper

    @property
    def executor(self) -> Executor:
        if self._executor is None:  # 장바구니와 같은 작업 스레드 (요기요 창이 겹치지 않게)
            from app.modules.cart.service import default_executor

            self._executor = default_executor()
        return self._executor

    # ------------------------------------------------------------ 시작
    def start(
        self,
        request_id: str,
        candidates: list[dict],
        message: str,
        started_at: datetime,
        *,
        fresh_only: bool = False,
    ) -> None:
        """후보 확인을 작업 스레드에 맡긴다 (주문 저장이 끝난 뒤 부른다).

        fresh_only: 최근 확인(캐시)을 쓰지 않고 요기요를 다시 연다 ("다시 확인", O-16).
        """
        if not self.enabled or not candidates:
            return
        if self.cfg.mode == "remote":
            self._enqueue_remote(request_id, list(candidates), started_at, fresh_only)
            return
        _set_job(
            request_id,
            state="RUNNING",
            started_at=started_at,
            started_mono=time.monotonic(),
            seconds=None,
            timing={},
            skipped=[],
            error=None,
        )
        self.executor.submit(
            self._run, request_id, list(candidates), message, started_at, fresh_only
        )

    def _run(
        self,
        request_id: str,
        candidates: list[dict],
        message: str,
        started_at,
        fresh_only: bool = False,
    ) -> None:
        try:
            if self.cfg.mode == "real":
                results = self.keeper.with_browser(
                    lambda browser: self._check_all(
                        candidates, message, started_at, browser, fresh_only
                    ),
                    headless=self.cfg.headless,
                    browser_channel=self.cfg.browser_channel or None,
                )
            else:
                results = asyncio.run(
                    self._check_all(candidates, message, started_at, None, fresh_only)
                )
            timing = {
                c["index"]: {"seconds": round(r.seconds, 2), "cached": r.cached}
                for c, r in zip(candidates, results, strict=True)
                if r.status != "SKIPPED"
            }
            _set_job(request_id, timing=timing, seconds=self._elapsed(request_id))
            skipped = [
                c["index"]
                for c, r in zip(candidates, results, strict=True)
                if r.status == "SKIPPED"
            ]
            reason = next((r.reason for r in results if r.status == "SKIPPED"), None)
            _set_job(request_id, state="DONE", skipped=skipped, stop_reason=reason)
        except Exception as exc:  # noqa: BLE001 — 브라우저를 못 띄워도 주문은 계속된다
            log.exception("요기요 실시간 확인 실패")
            _set_job(
                request_id,
                state="DONE",
                seconds=self._elapsed(request_id),
                error=f"{type(exc).__name__}: {exc}",
            )

    # ------------------------------------------------------------ remote 모드 (#80)
    def _enqueue_remote(self, request_id, candidates, started_at, fresh_only) -> None:
        """확인 작업을 쌓아 두고 PC 작업기를 기다린다. 같은 주문의 이전 작업은 버린다."""
        targets = []
        for c in candidates:
            t = target_of(c)
            row = self._fresh_row(t.restaurant_id, started_at, fresh_only)
            cached = None
            if row is not None:
                status, _, reason = self._judge(t, row.page, row.error)
                cached = {"status": status, "reason": reason}
            targets.append({"index": c["index"], **target_to_json(t), "cached": cached})
        job_id = uuid.uuid4().hex
        with _remote_lock:
            for jid in [j for j, job in _remote.items() if job["request_id"] == request_id]:
                del _remote[jid]
            _remote[job_id] = {
                "job_id": job_id,
                "request_id": request_id,
                "status": "QUEUED",
                "created_at": self.now(),
                "claimed_at": None,
                "worker": None,
                "targets": targets,
                "fresh_only": fresh_only,
            }
        _set_job(
            request_id,
            state="RUNNING",
            started_at=started_at,
            started_mono=time.monotonic(),
            seconds=None,
            timing={},
            skipped=[],
            error=None,
            remote_job=job_id,
        )

    def _expire_remote(self) -> None:
        """작업기가 안 가져가거나 결과를 안 보내면 '확인 못 함'으로 끝낸다 (후보 선택은 막지 않음)."""
        now = self.now()
        expired = []
        with _remote_lock:
            for jid, job in list(_remote.items()):
                if job["status"] == "QUEUED":
                    late = (now - job["created_at"]).total_seconds() > self.cfg.remote_wait_seconds
                    msg = REMOTE_NOT_CLAIMED
                else:
                    late = (now - job["claimed_at"]).total_seconds() > self.cfg.remote_timeout_seconds
                    msg = REMOTE_NO_RESULT
                if late:
                    del _remote[jid]
                    expired.append((job, msg))
        for job, msg in expired:
            rid = job["request_id"]
            if (_job(rid) or {}).get("remote_job") == job["job_id"]:
                log.warning("요기요 확인 작업 %s: %s", job["job_id"], msg)
                _set_job(rid, state="DONE", error=msg, seconds=self._elapsed(rid))

    def claim(self, worker: str | None = None) -> dict | None:
        """PC 작업기가 가져갈 확인 작업 하나 (먼저 쌓인 것부터). 없으면 None."""
        self._expire_remote()
        with _remote_lock:
            queued = [j for j in _remote.values() if j["status"] == "QUEUED"]
            if not queued:
                return None
            job = min(queued, key=lambda j: j["created_at"])
            job.update(status="CLAIMED", claimed_at=self.now(), worker=worker)
            return {
                "job_id": job["job_id"],
                "request_id": job["request_id"],
                "targets": job["targets"],
                "concurrency": self.cfg.concurrency,
                "want": self.cfg.want,
                "timeout_seconds": self.cfg.timeout_seconds,
            }

    def submit(self, job_id: str, results: list[dict], error: str | None = None) -> dict:
        """작업기 결과: 식당 화면 원본을 저장하고(식당당 한 행) 이 주문의 확인을 끝낸다."""
        with _remote_lock:
            job = _remote.get(job_id)
            if job is None or job["status"] != "CLAIMED":
                return {"accepted": False}
            del _remote[job_id]
        for r in results:
            if r.get("status") in ("SKIPPED", None) or r.get("cached"):
                continue
            page = r.get("page")
            self._save(str(r["restaurant_id"]), page, None if page is not None else r.get("reason"))
        rid = job["request_id"]
        if (_job(rid) or {}).get("remote_job") != job_id:  # 그 사이 다시 확인을 시작함: 원본만 저장
            return {"accepted": True, "current": False}
        timing = {
            int(r["index"]): {"seconds": r.get("seconds"), "cached": bool(r.get("cached"))}
            for r in results
            if r.get("status") != "SKIPPED"
        }
        skipped = [int(r["index"]) for r in results if r.get("status") == "SKIPPED"]
        reason = next((r.get("reason") for r in results if r.get("status") == "SKIPPED"), None)
        _set_job(
            rid,
            state="DONE",
            timing=timing,
            skipped=skipped,
            stop_reason=reason,
            seconds=self._elapsed(rid),
            error=error,
        )
        return {"accepted": True, "current": True}

    @staticmethod
    def _elapsed(request_id: str) -> float | None:
        job = _job(request_id) or {}
        start = job.get("started_mono")
        return round(time.monotonic() - start, 2) if start is not None else None

    async def _check_all(
        self, candidates: list[dict], message: str, started_at, browser=None, fresh_only=False
    ) -> list:
        targets = [target_of(c) for c in candidates]
        rank = {id(t): i for i, t in enumerate(targets)}
        locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        async def with_cache(observe, t: CheckTarget) -> CheckResult:
            async with locks[t.restaurant_id]:  # 같은 식당이 두 후보에 있으면 한 번만 연다
                t0 = time.monotonic()
                row = self._fresh_row(t.restaurant_id, started_at, fresh_only)
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

        from app.modules.cart.yogiyo import DEMO_LOCATION, _init_script
        from app.modules.livecheck import checker

        # 장바구니와 같은 브라우저에 확인용 창(context)을 하나 열고, 끝나면 그 창만 닫는다
        loc = json.loads(DEMO_LOCATION.read_text(encoding="utf-8"))
        context = await browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 900})
        try:
            await context.add_init_script(_init_script(loc))
            check = checker.browser_checker(
                context, timeout_ms=int(self.cfg.timeout_seconds * 1000)
            )

            async def real(t: CheckTarget):
                r = await check(t)
                return r.page, (r.reason if r.page is None else None)

            return await run_checks(targets, lambda t: with_cache(real, t), **opts)
        finally:
            try:
                await context.close()
            except Exception:  # noqa: BLE001 — 사용자가 창을 먼저 닫은 경우
                pass

    # ------------------------------------------------------------ 저장 (식당당 한 행)
    def _since(self, started_at: datetime, fresh_only: bool) -> datetime:
        """이 시각 이후의 확인만 이번 결과로 친다. real 모드는 15분 캐시 (다시 확인이면 제외)."""
        if self.cfg.mode in ("real", "remote") and not fresh_only:
            return started_at - timedelta(minutes=self.cfg.cache_minutes)
        return started_at

    def _fresh_row(
        self, restaurant_id: str, started_at: datetime, fresh_only: bool = False
    ) -> RestaurantLiveStatus | None:
        """이번 작업에서 다시 쓸 수 있는 최근 확인. fake 결과는 다른 주문에 쓰지 않는다."""
        with self.sf() as s:
            row = s.get(RestaurantLiveStatus, restaurant_id)
            if row is None or row.page_status not in VALID_PAGE or row.checked_at is None:
                return None
            if row.mode != self.cfg.mode:
                return None
            if row.checked_at < self._since(started_at, fresh_only):
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
    def results(
        self,
        request_id: str,
        candidates: list[dict],
        started_at: datetime | None,
        fresh_only: bool = False,
    ) -> list:
        if self.cfg.mode == "remote":
            self._expire_remote()
        job = _job(request_id) or {}
        out = []
        with self.sf() as s:
            for c in candidates:
                t = target_of(c)
                row = s.get(RestaurantLiveStatus, t.restaurant_id)
                out.append(self._candidate_result(c, t, row, job, started_at, fresh_only))
        return out

    def _row_counts(self, row, started_at, fresh_only: bool = False) -> bool:
        if row is None or row.checked_at is None or started_at is None or row.mode != self.cfg.mode:
            return False
        if row.page_status not in VALID_PAGE:  # 차단·오류는 캐시로 치지 않는다
            return row.checked_at >= started_at
        return row.checked_at >= self._since(started_at, fresh_only)

    def _candidate_result(
        self, c: dict, t: CheckTarget, row, job: dict, started_at, fresh_only: bool = False
    ) -> dict:
        base = {"candidate_index": c["index"], "restaurant_id": t.restaurant_id}
        if self._row_counts(row, started_at, fresh_only):
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

    def view(
        self,
        request_id: str,
        candidates: list[dict],
        started_at: datetime | None,
        fresh_only: bool = False,
    ) -> dict:
        if not self.enabled:
            return {"mode": self.cfg.mode, "state": "OFF", "total": 0, "done": 0, "results": []}
        results = self.results(request_id, candidates, started_at, fresh_only)
        # 화면에 보일 후보: 고를 수 있는 후보가 want곳 나올 때까지 순위대로.
        # 주문 불가 후보는 숨기지 않고 제자리에 사유와 함께 보이되 고를 수 없다(selectable=False).
        selectable_shown = 0
        for r in results:
            r["selectable"] = r["status"] != "UNAVAILABLE"
            r["visible"] = selectable_shown < self.cfg.want
            selectable_shown += r["visible"] and r["selectable"]
        job = _job(request_id) or {}
        timing = job.get("timing") or {}
        for r in results:  # 확인에 걸린 시간, 최근 확인(캐시)을 다시 썼는지
            t = timing.get(r["candidate_index"]) or {}
            r["seconds"], r["cached"] = t.get("seconds"), t.get("cached")
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
            # 확인 시작부터 끝까지 걸린 시간(초). 진행 중이면 지금까지
            "seconds": job.get("seconds") if not running else self._elapsed(request_id),
            "results": results,
        }

    # ------------------------------------------------------------ 후보 선택 때 판정 (O-02)
    def verdict(
        self,
        request_id: str,
        candidate: dict,
        started_at,
        *,
        fresh_only: bool = False,
        wait_seconds: float | None = None,
    ) -> dict:
        """선택한 후보의 판정. 확인 중이면 끝날 때까지 잠깐 기다린다."""
        wait = self.cfg.timeout_seconds + 2 if wait_seconds is None else wait_seconds
        deadline = time.monotonic() + wait
        while True:
            r = self.results(request_id, [candidate], started_at, fresh_only)[0]
            if r["status"] != "CHECKING" or time.monotonic() >= deadline:
                return r
            time.sleep(0.25)
