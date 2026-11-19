"""요기요 장바구니 담기 작업: 시작(O-13)·조회(O-14).

- 담기는 5~20초 걸려서 요청 안에서 기다리지 않는다. 작업 행(cart_job)을 만들고 스레드에 맡긴다.
- 작업 스레드는 하나뿐 (요기요에 동시에 여러 브라우저를 띄우지 않게, 시연용 최소 호출).
- 같은 주문에 담는 중인 작업이 있으면 새로 만들지 않고 그 작업을 돌려준다 (버튼 연타 방지).
- 결과 캡처는 파일(YOGIYO_CART_DIR)로 두고, 조회 응답에 data URL로 실어 준다 (화면이 바로 표시).

설정 (.env)
- YOGIYO_CART_MODE   : fake(기본, Playwright 없이 견적대로 담긴 것처럼) | real(진짜 요기요) | off
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

from sqlalchemy import select
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


def _seconds(name: str) -> float:
    try:
        return max(0.0, float(_env(name, "0")))
    except ValueError:
        log.warning("%s 값이 숫자가 아니라 0으로 둔다", name)
        return 0.0


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


Filler = Callable[..., CartResult]


def default_filler(cfg: CartSettings) -> Filler | None:
    if cfg.mode == "off":
        return None
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

    # ------------------------------------------------------------------ 시작 (O-13)
    def start(self, *, request_id: str, user_id: str, quote: dict, message: str = "") -> dict:
        if self.filler is None:
            raise AppError(503, "CART_DISABLED", "요기요 장바구니 담기가 꺼져 있습니다.")
        running = self._latest(request_id, statuses=ACTIVE)
        if running is not None:
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
        self.executor.submit(self._run, job.job_id, message)
        return self.view(job)

    # ------------------------------------------------------------------ 조회 (O-14)
    def latest(self, request_id: str) -> dict | None:
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
            order = CartOrder(
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


def _data_url(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"
