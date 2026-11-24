"""요기요 장바구니 담기 작업: 시작(O-13)·조회(O-14).

- 담기는 5~20초 걸려서 요청 안에서 기다리지 않는다. 작업 행(cart_job)을 만들고 스레드에 맡긴다.
- 작업 스레드는 하나뿐 (요기요에 동시에 여러 브라우저를 띄우지 않게, 시연용 최소 호출).
- 같은 주문에 담는 중인 작업이 있으면 새로 만들지 않고 그 작업을 돌려준다 (버튼 연타 방지).
- 결과 캡처는 파일(YOGIYO_CART_DIR)로 두고, 조회 응답에 data URL로 실어 준다 (화면이 바로 표시).
- remote 모드: 서버는 작업을 QUEUED로 남기기만 하고, 한국 PC의 작업기
  (scripts/cart_worker.py)가 가져가서(claim) 담은 뒤 결과를 올린다 (cart/router.py).
  배포 서버(해외 클라우드 IP)는 요기요가 막기 때문이다.
  작업기가 정해진 시간 안에 가져가지 않거나 끝내지 않으면 가짜 담기로 마무리한다
  (결제 승인이 막히지 않게).

설정 (.env)
- YOGIYO_CART_MODE   : fake(기본, Playwright 없이 견적대로 담긴 것처럼) | real(진짜 요기요)
                       | remote(PC 작업기가 진짜 요기요에 담음) | off
- YOGIYO_CART_REMOTE_WAIT_SECONDS   : remote. 작업기가 이 시간(초) 안에 가져가지 않으면
                                      가짜로 마무리 (기본 30, 0이면 계속 기다림)
- YOGIYO_CART_REMOTE_TIMEOUT_SECONDS: remote. 시작부터 이 시간(초) 안에 결과가 없으면
                                      가짜로 마무리 (기본 180, 0이면 계속 기다림)
- YOGIYO_CART_HEADLESS: 0(기본, 브라우저 창을 띄움) | 1(창 없이 — 요기요가 Access Denied로 막음)
- YOGIYO_CART_KEEP_BROWSER: 1(기본) 창을 띄운 경우 새 주문(O-01) 전까지 요기요 창을 둔다
                          0이면 YOGIYO_CART_KEEP_OPEN_SECONDS(초)만큼 열어 둔 뒤 닫는다
- YOGIYO_CART_BROWSER: 비우면 Playwright Chromium, chrome이면 설치된 Chrome
- YOGIYO_CART_DIR    : 캡처 저장 폴더. 기본 cart_runs
"""

from __future__ import annotations

import base64
import logging
import os
import uuid
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import clock as core_clock
from app.core.errors import AppError
from app.modules.cart.models import CartJob
from app.modules.cart.yogiyo import BrowserKeeper, CartItem, CartOrder, CartResult

log = logging.getLogger("app.cart")

ACTIVE = ("QUEUED", "RUNNING")
FINISHED = ("CART_READY", "CART_MISMATCH", "FAILED")


def _env(name: str, default: str) -> str:
    """빈 값(`NAME=`)도 설정 안 한 것으로 본다."""
    value = (os.getenv(name) or "").strip()
    return value or default


def _seconds(name: str, default: str = "0") -> float:
    try:
        return max(0.0, float(_env(name, default)))
    except ValueError:
        log.warning("%s 값이 숫자가 아니라 %s(으)로 둔다", name, default)
        return float(default)


@dataclass(frozen=True)
class CartSettings:
    mode: str = field(default_factory=lambda: _env("YOGIYO_CART_MODE", "fake").lower())
    headless: bool = field(
        default_factory=lambda: _env("YOGIYO_CART_HEADLESS", "0") not in ("0", "false")
    )
    keep_open_seconds: float = field(
        default_factory=lambda: _seconds("YOGIYO_CART_KEEP_OPEN_SECONDS")
    )
    keep_browser: bool = field(
        default_factory=lambda: _env("YOGIYO_CART_KEEP_BROWSER", "1") not in ("0", "false")
    )
    browser_channel: str = field(default_factory=lambda: _env("YOGIYO_CART_BROWSER", ""))
    out_dir: str = field(default_factory=lambda: _env("YOGIYO_CART_DIR", "cart_runs"))
    remote_wait_seconds: float = field(
        default_factory=lambda: _seconds("YOGIYO_CART_REMOTE_WAIT_SECONDS", "30")
    )
    remote_timeout_seconds: float = field(
        default_factory=lambda: _seconds("YOGIYO_CART_REMOTE_TIMEOUT_SECONDS", "180")
    )


Filler = Callable[..., CartResult]


def default_filler(cfg: CartSettings) -> Filler | None:
    if cfg.mode == "off":
        return None
    # remote: 실제 담기는 PC 작업기가 한다. 여기 filler는 작업기가 없을 때 대신 쓰는 가짜 담기
    if cfg.mode == "real":
        if cfg.keep_browser and not cfg.headless:
            keeper = _keeper()

            def kept(order: CartOrder, *, name: str, message: str = "") -> CartResult:
                return keeper.fill(
                    order,
                    headless=False,
                    browser_channel=cfg.browser_channel or None,
                    out_dir=cfg.out_dir,
                    name=name,
                )

            return kept

        from app.modules.cart.yogiyo import fill_cart_sync

        def real(order: CartOrder, *, name: str, message: str = "") -> CartResult:
            return fill_cart_sync(
                order,
                headless=cfg.headless,
                out_dir=cfg.out_dir,
                browser_channel=cfg.browser_channel or None,
                keep_open_seconds=cfg.keep_open_seconds,
                name=name,
            )

        return real
    from app.modules.cart.fake import fake_fill

    return fake_fill


_executor: ThreadPoolExecutor | None = None
_kept: BrowserKeeper | None = None


def _keeper() -> BrowserKeeper:
    global _kept
    if _kept is None:
        _kept = BrowserKeeper()
    return _kept


def release_browser() -> None:
    """새 주문이 들어오면 열어 둔 요기요 창을 닫는다 (브라우저를 연 작업 스레드에서)."""
    if _kept is not None:
        default_executor().submit(_kept.close)


def default_executor() -> Executor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yogiyo-cart")
    return _executor


class CartService:
    def __init__(
        self,
        session: Session,
        session_factory: Callable[[], Session],
        *,
        settings: CartSettings | None = None,
        filler: Filler | None = None,
        executor: Executor | None = None,
        now: Callable = core_clock.now,
    ):
        self.session = session
        self.sf = session_factory
        self.cfg = settings or CartSettings()
        self.filler = filler if filler is not None else default_filler(self.cfg)
        self.executor = executor or default_executor()
        self.now = now

    @property
    def enabled(self) -> bool:
        """요기요 장바구니 담기를 쓸 수 있는지 (YOGIYO_CART_MODE=off면 False)."""
        return self.filler is not None

    # ------------------------------------------------------------------ 시작 (O-13)
    def start(self, *, request_id: str, user_id: str, quote: dict, message: str = "") -> dict:
        if self.filler is None:
            raise AppError(503, "CART_DISABLED", "요기요 장바구니 담기가 꺼져 있습니다.")
        running = self._latest(request_id, statuses=ACTIVE)
        if running is not None and not self._fallback_if_stale(running):
            return self.view(running)
        job = CartJob(
            job_id=f"CART-{uuid.uuid4().hex[:16]}",
            request_id=request_id,
            user_id=user_id,
            status="QUEUED",
            restaurant_id=str(quote["restaurant_id"]),
            expected={
                "items": [
                    {
                        "menu_id": str(i["menu_id"]),
                        "name": i.get("name"),
                        "quantity": i["quantity"],
                        "unit_price_krw": i.get("unit_price_krw"),
                    }
                    for i in quote["items"]
                ],
                "menu_subtotal_krw": quote.get("menu_subtotal_krw"),
                "delivery_fee_krw": quote.get("delivery_fee_krw"),
            },
            cart_items=[],
            mismatches=[],
            mode=self.cfg.mode,
            created_at=self.now(),
        )
        self.session.add(job)
        self.session.commit()
        if self.cfg.mode == "remote":
            return self.view(job)  # PC 작업기가 가져간다 (claim)
        self.executor.submit(self._run, job.job_id, message)
        return self.view(job)

    # ------------------------------------------------------------------ 조회 (O-14)
    def latest(self, request_id: str) -> dict | None:
        job = self._latest(request_id)
        if job is not None and self._fallback_if_stale(job):
            job = self._latest(request_id)
        return self.view(job) if job else None

    def _latest(self, request_id: str, statuses: tuple[str, ...] | None = None) -> CartJob | None:
        stmt = select(CartJob).where(CartJob.request_id == request_id)
        if statuses:
            stmt = stmt.where(CartJob.status.in_(statuses))
        stmt = stmt.order_by(CartJob.created_at.desc(), CartJob.job_id.desc()).limit(1)
        # 작업 스레드가 다른 세션에서 바꾼 값을 읽도록 (세션에 남은 예전 값을 쓰지 않음)
        return self.session.scalars(stmt.execution_options(populate_existing=True)).first()

    def check_approvable(self, request_id: str) -> None:
        """승인(결제) 전에 부른다. 담기를 안 했으면 막지 않는다 (담기는 선택 기능).

        - 담는 중이면 결과를 보고 승인하도록 막는다.
        - 마지막 담기가 실패했으면 막는다: 요기요에서 실제로 주문할 수 없는 상태일 수 있다
          (가게 영업 안 함·메뉴 없어짐 등). 다시 담아 성공하면 승인할 수 있다.
        - 견적과 금액이 다른 경우(CART_MISMATCH)는 화면 경고만 하고 막지 않는다.
        """
        job = self._latest(request_id)
        if job is None:
            return
        if job.status in ACTIVE:
            raise AppError(
                409, "CART_IN_PROGRESS", "요기요 장바구니를 담는 중이에요. 끝난 뒤 승인해 주세요."
            )
        if job.status == "FAILED":
            raise AppError(
                409,
                "CART_FAILED",
                "요기요 장바구니 담기에 실패해서 결제할 수 없어요. "
                "다시 담거나 주문을 취소해 주세요.",
                {"job_id": job.job_id, "error": job.error},
            )

    def view(self, job: CartJob) -> dict:
        return {
            "job_id": job.job_id,
            "status": job.status,
            "mode": job.mode,
            "restaurant_id": job.restaurant_id,
            "expected": job.expected,
            "cart_items": job.cart_items or [],
            "menu_subtotal_krw": job.menu_subtotal_krw,
            "delivery_fee_krw": job.delivery_fee_krw,
            "mismatches": job.mismatches or [],
            "error": job.error,
            "yogiyo_url": job.yogiyo_url,
            "screenshot": _data_url(job.screenshot_path) if job.status in FINISHED else None,
            "created_at": job.created_at.isoformat(),
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }

    # ------------------------------------------------------------------ 작업 스레드
    def _run(self, job_id: str, message: str) -> None:
        with self.sf() as s:
            job = s.get(CartJob, job_id)
            if job is None:
                return
            job.status = "RUNNING"
            s.commit()
            order = _order(job)
            try:
                r = self.filler(order, name=job_id, message=message)
            except Exception as exc:  # noqa: BLE001 — 어떤 실패든 작업 결과로 남긴다
                log.exception("요기요 장바구니 담기 실패")
                r = CartResult(
                    status="FAILED",
                    restaurant_id=job.restaurant_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
            job.status = r.status if r.status in FINISHED else "FAILED"
            job.cart_items = r.cart_items
            job.menu_subtotal_krw = r.menu_subtotal_krw
            job.delivery_fee_krw = r.delivery_fee_krw
            job.mismatches = r.mismatches
            job.error = (r.error or "")[:2000] or None
            job.screenshot_path = r.screenshot
            job.yogiyo_url = r.url
            job.finished_at = self.now()
            s.commit()

    # ------------------------------------------------------------------ remote: PC 작업기
    def claim(self, worker: str | None = None) -> dict | None:
        """대기 중인 remote 작업 하나를 RUNNING으로 바꾸고 담을 내용을 돌려준다. 없으면 None.

        작업기가 여러 대여도 같은 작업을 두 번 가져가지 않게, QUEUED일 때만 바꾼다.
        """
        for _ in range(5):
            stmt = (
                select(CartJob)
                .where(CartJob.mode == "remote", CartJob.status == "QUEUED")
                .order_by(CartJob.created_at, CartJob.job_id)
                .limit(1)
            )
            job = self.session.scalars(stmt.execution_options(populate_existing=True)).first()
            if job is None:
                return None
            if self._fallback_if_stale(job):
                continue  # 너무 오래 기다린 작업은 가짜로 마무리했다 → 다음 작업
            done = self.session.execute(
                update(CartJob)
                .where(CartJob.job_id == job.job_id, CartJob.status == "QUEUED")
                .values(status="RUNNING")
            )
            self.session.commit()
            if done.rowcount != 1:
                continue  # 다른 작업기가 먼저 가져감
            log.info("요기요 담기 작업 %s → PC 작업기 %s", job.job_id, worker or "-")
            return {
                "job_id": job.job_id,
                "restaurant_id": job.restaurant_id,
                "items": job.expected["items"],
                "expected_menu_subtotal_krw": job.expected.get("menu_subtotal_krw"),
                "expected_delivery_fee_krw": job.expected.get("delivery_fee_krw"),
            }
        return None

    def submit_result(self, job_id: str, r: CartResult, screenshot: bytes | None = None) -> dict:
        """PC 작업기가 담은 결과를 저장한다. 이미 끝난 작업(시간 초과로 가짜 마무리 등)이면 409."""
        job = self.session.get(CartJob, job_id, populate_existing=True)
        if job is None:
            raise AppError(404, "CART_JOB_NOT_FOUND", "담기 작업을 찾지 못했습니다.")
        if job.status in FINISHED:
            raise AppError(
                409,
                "CART_JOB_CLOSED",
                "이미 끝난 담기 작업이에요 (작업기 응답이 늦어 시연 모드로 마무리했을 수 있어요).",
            )
        if job.mode != "remote":
            raise AppError(404, "CART_JOB_NOT_FOUND", "작업기가 처리할 담기 작업이 아닙니다.")
        if screenshot:
            out = Path(self.cfg.out_dir)
            out.mkdir(parents=True, exist_ok=True)
            shot = out / f"{job_id}-{r.status}.jpg"
            shot.write_bytes(screenshot)
            r.screenshot = str(shot)
        else:
            r.screenshot = None
        if not self._finish(job_id, "RUNNING", r):
            raise AppError(
                409,
                "CART_JOB_CLOSED",
                "이미 끝난 담기 작업이에요 (작업기 응답이 늦어 시연 모드로 마무리했을 수 있어요).",
            )
        return self.view(self.session.get(CartJob, job_id, populate_existing=True))

    def _fallback_if_stale(self, job: CartJob) -> bool:
        """remote 작업을 작업기가 제때 가져가지(끝내지) 않았으면 가짜 담기로 마무리한다.

        작업기가 꺼져 있어도 승인 화면이 '확인 중'에서 멈추지 않게 하려는 것이다.
        마무리했으면 True.
        """
        if job.mode != "remote" or job.status not in ACTIVE or self.filler is None:
            return False
        if job.status == "QUEUED":
            limit, why = self.cfg.remote_wait_seconds, "PC 작업기가 작업을 가져가지 않아"
        else:
            limit, why = self.cfg.remote_timeout_seconds, "PC 작업기가 결과를 보내지 않아"
        waited = (self.now() - job.created_at).total_seconds()
        if limit <= 0 or waited < limit:
            return False
        r = self.filler(_order(job), name=job.job_id, message="")
        r.error = f"{why} 시연 모드(fake)로 대신 담았어요."
        if self._finish(job.job_id, job.status, r, mode="fake"):
            log.warning("요기요 담기 작업 %s: %s 가짜로 마무리", job.job_id, why)
        return True

    def _finish(
        self, job_id: str, from_status: str, r: CartResult, mode: str | None = None
    ) -> bool:
        """작업 결과를 저장한다. 상태가 from_status 그대로일 때만 바꾼다.

        작업기 결과와 시간 초과 마무리가 겹쳐도 한 번만 저장된다.
        """
        values = {
            "status": r.status if r.status in FINISHED else "FAILED",
            "cart_items": r.cart_items,
            "menu_subtotal_krw": r.menu_subtotal_krw,
            "delivery_fee_krw": r.delivery_fee_krw,
            "mismatches": r.mismatches,
            "error": (r.error or "")[:2000] or None,
            "screenshot_path": r.screenshot,
            "yogiyo_url": r.url,
            "finished_at": self.now(),
        }
        if mode:
            values["mode"] = mode
        done = self.session.execute(
            update(CartJob)
            .where(CartJob.job_id == job_id, CartJob.status == from_status)
            .values(**values)
        )
        self.session.commit()
        return done.rowcount == 1


def _order(job: CartJob) -> CartOrder:
    """작업 행의 견적(expected)으로 요기요에 담을 주문을 만든다."""
    return CartOrder(
        restaurant_id=job.restaurant_id,
        items=[
            CartItem(
                menu_id=i["menu_id"],
                quantity=i["quantity"],
                name=i.get("name"),
                unit_price_krw=i.get("unit_price_krw"),
            )
            for i in job.expected["items"]
        ],
        expected_menu_subtotal_krw=job.expected.get("menu_subtotal_krw"),
        expected_delivery_fee_krw=job.expected.get("delivery_fee_krw"),
    )


def _data_url(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"
