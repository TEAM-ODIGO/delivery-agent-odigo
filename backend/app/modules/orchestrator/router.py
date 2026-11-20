"""O 모듈의 HTTP 입구 (화면은 O만 부른다). 판단은 service가 한다.

인증 (MVP 1 임시)
- 사용자: Authorization: Bearer {access_token}. 토큰 → user_id는 .env의 DEV_USER_TOKENS로 정한다.
  (로그인 기능이 생기면 current_user_id만 바꾸면 된다. user_id는 body로 받지 않는다 = 위조 방지)
- 관리자(O-04): Authorization: Bearer {ADMIN_ACCESS_TOKEN}. 비어 있으면 아무도 못 본다.
"""

import hmac
import re
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal, get_session
from app.core.errors import AppError
from app.modules.cart.service import CartService, release_browser
from app.modules.orchestrator.schemas import Reply, RunCreate
from app.modules.orchestrator.service import OrchestratorService
from app.modules.orchestrator.wiring import cart_service, orchestrator_service

router = APIRouter(prefix="/orchestrator", tags=["O · orchestrator"])

_KEY = re.compile(r"^[A-Za-z0-9_\-:.]{1,100}$")


def get_d_session():
    """D는 O와 다른 세션을 쓴다 (D가 커밋해도 O의 진행 중 변경·잠금에 영향 없게)."""
    with SessionLocal() as s:
        yield s


def get_service(
    session: Annotated[Session, Depends(get_session)],
    d_session: Annotated[Session, Depends(get_d_session)],
) -> OrchestratorService:
    return orchestrator_service(session, d_session)


def _bearer(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    return token if scheme.lower() == "bearer" else ""


def current_user_id(authorization: Annotated[str | None, Header()] = None) -> str:
    user = settings.user_tokens().get(_bearer(authorization))
    if not user:
        raise AppError(401, "UNAUTHORIZED", "로그인이 필요합니다.")
    return user


def get_admin_token() -> str:
    return settings.admin_access_token


def require_admin(
    expected: Annotated[str, Depends(get_admin_token)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    if not expected or not hmac.compare_digest(expected, _bearer(authorization)):
        raise AppError(403, "FORBIDDEN", "관리자만 볼 수 있습니다.")


def idempotency_key(idempotency_key: Annotated[str | None, Header()] = None) -> str | None:
    if idempotency_key is not None and not _KEY.match(idempotency_key):
        raise AppError(400, "VALIDATION_ERROR", "Idempotency-Key 형식이 올바르지 않습니다.")
    return idempotency_key


Service = Annotated[OrchestratorService, Depends(get_service)]
UserId = Annotated[str, Depends(current_user_id)]
IdemKey = Annotated[str | None, Depends(idempotency_key)]


@router.post("/runs", status_code=201, summary="O-01 주문 요청 시작")
def start_run(body: RunCreate, svc: Service, user_id: UserId, key: IdemKey):
    release_browser()  # 이전 주문 때 열어 둔 요기요 창을 닫는다 (창을 띄워 시연할 때만)
    return svc.start_run(user_id, body, key)


def get_cart(session: Annotated[Session, Depends(get_session)]) -> CartService:
    return cart_service(session)


Cart = Annotated[CartService, Depends(get_cart)]


@router.post("/runs/{request_id}/reply", summary="O-02 사용자 응답 전달")
def reply(
    request_id: str,
    svc: Service,
    user_id: UserId,
    key: IdemKey,
    body: Annotated[Reply, Body()],
    cart: Cart,
):
    if body.type == "APPROVE":
        svc.check_owner(user_id, request_id)
        cart.check_approvable(request_id)  # 요기요 장바구니 담기에 실패했으면 결제하지 않는다
    return svc.reply(user_id, request_id, body, key)


@router.get("/runs", summary="O-09 내 주문 목록")
def list_my_runs(svc: Service, user_id: UserId, limit: int = 20):
    return svc.list_runs(user_id, limit)


@router.get("/payment-methods", summary="O-10 맡길 결제수단 목록")
def list_payment_methods(svc: Service, user_id: UserId):
    return svc.list_payment_methods(user_id)


@router.get(
    "/admin/runs",
    summary="O-11 전체 주문 목록 (관리자)",
    dependencies=[Depends(require_admin)],
)
def admin_runs(svc: Service, limit: int = 100, state: str | None = None):
    return svc.list_runs(None, limit, state)


@router.get(
    "/admin/runs/{request_id}/audit-logs",
    summary="O-12 주문별 D 감사 로그 (관리자)",
    dependencies=[Depends(require_admin)],
)
def admin_audit_logs(request_id: str, svc: Service):
    return svc.list_audit_logs(request_id)


@router.post(
    "/runs/{request_id}/yogiyo-cart",
    status_code=202,
    summary="O-13 요기요 장바구니 담기 시작 (결제 직전에서 멈춤)",
)
def start_yogiyo_cart(request_id: str, svc: Service, user_id: UserId, cart: Cart):
    return cart.start(**svc.cart_target(user_id, request_id))


@router.get("/runs/{request_id}/yogiyo-cart", summary="O-14 요기요 장바구니 담기 결과")
def get_yogiyo_cart(request_id: str, svc: Service, user_id: UserId, cart: Cart):
    svc.check_owner(user_id, request_id)
    return cart.latest(request_id) or {"status": "NONE"}


@router.get("/runs/{request_id}/live-check", summary="O-15 후보 요기요 실시간 확인 결과")
def get_live_check(request_id: str, svc: Service, user_id: UserId):
    return svc.live_check_view(user_id, request_id)


@router.post("/runs/{request_id}/live-check", summary="O-16 후보 요기요 실시간 다시 확인")
def recheck_live(request_id: str, svc: Service, user_id: UserId):
    return svc.live_recheck(user_id, request_id)


@router.get("/runs/{request_id}", summary="O-03 진행 상태 조회")
def get_run(request_id: str, svc: Service, user_id: UserId):
    return svc.get_run(user_id, request_id)


@router.get(
    "/runs/{request_id}/events",
    summary="O-04 주문 단계 이력 조회 (관리자, 1순위)",
    dependencies=[Depends(require_admin)],
)
def list_events(request_id: str, svc: Service):
    return svc.list_events(request_id)


@router.get("/health", summary="O-05 상태 확인")
def health(session: Annotated[Session, Depends(get_session)]):
    try:
        session.execute(text("SELECT 1"))
        return {"service": "orchestrator", "status": "UP"}
    except Exception:
        return JSONResponse(status_code=503, content={"service": "orchestrator", "status": "DOWN"})
