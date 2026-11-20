"""D 모듈의 HTTP 입구. 판단은 하지 않고 service로 넘기기만 한다.

- health를 뺀 모든 API는 서비스 토큰(Authorization: Bearer {service_token})을 확인한다.
  D-09도 서비스 토큰이다: 부르는 쪽은 O(O-04)이고, 관리자 권한은 O-04가 관리자 토큰으로 확인한다.
  D-06은 E가 HTTP로 부르고, D-01~D-05는 모놀리스 안에서 O가 service를 직접 부르지만
  테스트·대시보드·디버깅용 HTTP 입구도 같은 토큰으로 막아 둔다.
  (막지 않으면 누구나 HTTP로 승인 확정(D-03)을 흉내 낼 수 있다)
"""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_session
from app.core.errors import AppError
from app.modules.delegation.benefits import BenefitPort, StaticBenefitPort, http_benefit_port
from app.modules.delegation.constants import EventType
from app.modules.delegation.schemas import (
    ApprovalOut,
    AuditLogPage,
    AuthorizationCheckRequest,
    AuthorizationCheckResult,
    ConfirmOut,
    ConfirmRequest,
    OrderValidationRequest,
    OrderValidationResult,
    PaymentResultOut,
    PaymentResultRequest,
    PolicyCreate,
    PolicyOut,
    RevokeOut,
    RevokeRequest,
)
from app.modules.delegation.service import DelegationService

router = APIRouter(prefix="/delegation", tags=["D · delegation"])

# C 연결 지점. 세 가지 중 하나:
#   1) C_BENEFIT_URL 없음 → 임시 고정 규칙 (D 혼자 개발·테스트)
#   2) C_BENEFIT_URL 있음 → C 서버를 HTTP로 호출 (C와 각자 서버로 함께 돌려 볼 때)
#   3) 모놀리스로 합친 뒤 → CBenefitPort(calculate=<C-05 함수>, list_methods=<C-02 함수>)
_benefit_port: BenefitPort = (
    http_benefit_port(settings.c_benefit_url, settings.service_token)
    if settings.c_benefit_url
    else StaticBenefitPort()
)


# --- 의존성 (테스트에서 app.dependency_overrides로 바꿔 끼울 수 있다) -----------------
def get_benefit_port() -> BenefitPort:
    return _benefit_port


def get_service_token() -> str:
    return settings.service_token


def get_service(
    session: Annotated[Session, Depends(get_session)],
    benefits: Annotated[BenefitPort, Depends(get_benefit_port)],
) -> DelegationService:
    return DelegationService(session, benefits, approval_ttl_minutes=settings.approval_ttl_minutes)


def require_service_token(
    expected: Annotated[str, Depends(get_service_token)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """팀 공통 서비스 간 인증: Authorization: Bearer {service_token}"""
    scheme, _, token = (authorization or "").partition(" ")
    # 토큰이 설정돼 있지 않으면 아무도 통과 못 한다 (fail-closed).
    # compare_digest: 글자를 하나씩 비교하다 멈추지 않아 응답 시간으로 토큰을 추측할 수 없다.
    if not expected or scheme.lower() != "bearer" or not hmac.compare_digest(expected, token):
        raise AppError(401, "UNAUTHORIZED_SERVICE", "허용되지 않은 호출입니다.")


Service = Annotated[DelegationService, Depends(get_service)]

# 서비스 토큰이 필요한 API 묶음
protected = APIRouter(dependencies=[Depends(require_service_token)])


# --- API -------------------------------------------------------------------------------
@protected.post(
    "/policies", status_code=201, response_model=PolicyOut, summary="D-01 조건·위임 등록"
)
def register_policy(body: PolicyCreate, svc: Service):
    return svc.register_policy(body)


@protected.post(
    "/validations/orders", response_model=OrderValidationResult, summary="D-02 주문안 검증"
)
def validate_order(body: OrderValidationRequest, svc: Service):
    return svc.validate_order(body)


@protected.post(
    "/approvals/{approval_id}/confirm", response_model=ConfirmOut, summary="D-03 승인 확정"
)
def confirm(approval_id: str, body: ConfirmRequest, svc: Service):
    return svc.confirm(approval_id, body)


@protected.get("/approvals/{approval_id}", response_model=ApprovalOut, summary="D-04 승인 조회")
def get_approval(approval_id: str, svc: Service):
    return svc.get_approval(approval_id)


@protected.post(
    "/approvals/{approval_id}/revoke", response_model=RevokeOut, summary="D-05 승인 철회"
)
def revoke(approval_id: str, body: RevokeRequest, svc: Service):
    return svc.revoke(approval_id, body)


@protected.post(
    "/authorizations/check",
    response_model=AuthorizationCheckResult,
    summary="D-06 결제 직전 검증 (E 전용)",
)
def check_authorization(
    body: AuthorizationCheckRequest,
    svc: Service,
    x_request_id: Annotated[str | None, Header(max_length=64)] = None,
):
    return svc.check_authorization(body, request_id_hint=x_request_id)


IdQuery = Annotated[str | None, Query(max_length=64, pattern=r"^[A-Za-z0-9_\-:.]+$")]


@protected.post(
    "/payments/results", response_model=PaymentResultOut, summary="D-08 결제 결과 기록 (MVP 2)"
)
def record_payment_result(body: PaymentResultRequest, svc: Service):
    return svc.record_payment_result(body)


@protected.get("/audit-logs", response_model=AuditLogPage, summary="D-09 감사 로그 조회 (MVP 2)")
def list_audit_logs(
    svc: Service,
    request_id: IdQuery = None,
    approval_id: IdQuery = None,
    execution_id: IdQuery = None,
    event_type: EventType | None = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return svc.list_audit_logs(
        request_id=request_id,
        approval_id=approval_id,
        execution_id=execution_id,
        event_type=event_type,
        page=page,
        page_size=page_size,
    )


@router.get("/health", summary="D-07 상태 확인")
def health(session: Annotated[Session, Depends(get_session)]):
    try:
        session.execute(text("SELECT 1"))
        return {"service": "delegation", "status": "UP"}
    except Exception:
        return JSONResponse(status_code=503, content={"service": "delegation", "status": "DOWN"})


router.include_router(protected)
