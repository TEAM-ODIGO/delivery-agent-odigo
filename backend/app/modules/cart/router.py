"""PC 작업기용 API (YOGIYO_CART_MODE=remote). 화면은 부르지 않는다.

배포 서버(해외 클라우드 IP)는 요기요가 Access Denied로 막는다. 그래서 서버는 담기 작업을 쌓아 두고,
한국 PC의 작업기(scripts/cart_worker.py)가 가져가서 담은 뒤 결과를 올린다.
인증: 서비스 토큰 (Authorization: Bearer {INTERNAL_SERVICE_TOKEN}).
경로는 nginx가 백엔드로 넘기는 /orchestrator 아래에 둔다.
"""

from __future__ import annotations

import base64
import binascii
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, get_session
from app.core.errors import AppError
from app.modules.cart.service import CartService
from app.modules.cart.yogiyo import CartResult
from app.modules.discovery.security import require_service_token

MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024

router = APIRouter(
    prefix="/orchestrator/cart-worker",
    tags=["cart-worker"],
    dependencies=[Depends(require_service_token)],
)


def get_cart(session: Annotated[Session, Depends(get_session)]) -> CartService:
    return CartService(session, SessionLocal)


Cart = Annotated[CartService, Depends(get_cart)]


class ClaimRequest(BaseModel):
    worker: str | None = Field(default=None, max_length=64, description="작업기 이름 (로그용)")


class ResultRequest(BaseModel):
    status: Literal["CART_READY", "CART_MISMATCH", "FAILED"]
    cart_items: list[dict[str, Any]] = []
    menu_subtotal_krw: int | None = None
    delivery_fee_krw: int | None = None
    mismatches: list[str] = []
    error: str | None = None
    url: str | None = None
    screenshot_base64: str | None = Field(default=None, description="JPEG 캡처 (base64)")


@router.post("/jobs/claim", summary="(로컬 확장) PC 작업기: 대기 중인 담기 작업 하나 가져가기")
def claim_job(body: ClaimRequest, cart: Cart):
    job = cart.claim(body.worker)
    if job is None:
        return Response(status_code=204)  # 가져갈 작업 없음
    return job


@router.post("/jobs/{job_id}/result", summary="(로컬 확장) PC 작업기: 담기 결과 올리기")
def submit_result(job_id: str, body: ResultRequest, cart: Cart):
    screenshot = None
    if body.screenshot_base64:
        try:
            screenshot = base64.b64decode(body.screenshot_base64, validate=True)
        except (binascii.Error, ValueError):
            raise AppError(
                400, "VALIDATION_ERROR", "캡처(base64) 형식이 올바르지 않습니다."
            ) from None
        if len(screenshot) > MAX_SCREENSHOT_BYTES:
            raise AppError(400, "VALIDATION_ERROR", "캡처가 너무 큽니다 (최대 5MB).")
    r = CartResult(
        status=body.status,
        restaurant_id="",
        cart_items=body.cart_items,
        menu_subtotal_krw=body.menu_subtotal_krw,
        delivery_fee_krw=body.delivery_fee_krw,
        mismatches=body.mismatches,
        error=body.error,
        url=body.url,
    )
    return cart.submit_result(job_id, r, screenshot)


# ---------------------------------------------------------------- 요기요 실시간 확인 (LIVE_CHECK_MODE=remote, #80)
class LiveResultItem(BaseModel):
    index: int
    restaurant_id: str
    status: str = Field(max_length=20)
    reason: str | None = None
    page: dict[str, Any] | None = Field(default=None, description="식당 화면 원본 (서버가 저장·재판정)")
    seconds: float | None = None
    cached: bool = False


class LiveResultRequest(BaseModel):
    results: list[LiveResultItem] = Field(default_factory=list, max_length=50)
    error: str | None = Field(default=None, max_length=500, description="작업기 전체 실패 사유")


def _live():
    from app.modules.orchestrator.wiring import live_check_service

    return live_check_service()


@router.post(
    "/live-checks/claim",
    summary="(로컬 확장, #80) PC 작업기: 대기 중인 요기요 실시간 확인 작업 하나 가져가기",
)
def claim_live_check(body: ClaimRequest):
    job = _live().claim(body.worker)
    if job is None:
        return Response(status_code=204)  # 가져갈 작업 없음 (remote 모드가 아니어도 204)
    return job


@router.post(
    "/live-checks/{job_id}/result",
    summary="(로컬 확장, #80) PC 작업기: 요기요 실시간 확인 결과 올리기",
)
def submit_live_check(job_id: str, body: LiveResultRequest):
    out = _live().submit(job_id, [r.model_dump() for r in body.results], body.error)
    if not out["accepted"]:
        raise AppError(409, "JOB_NOT_ACTIVE", "이미 끝났거나 시간이 지난 확인 작업입니다.", {"job_id": job_id})
    return out
