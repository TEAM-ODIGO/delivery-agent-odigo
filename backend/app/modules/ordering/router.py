"""B(주문) HTTP 입구. 경로 접두어 /ordering, 호출자는 O(서비스 토큰).

요청 형식 오류는 팀 공통(400)과 달리 B 명세대로 422 VALIDATION_ERROR + fields[{field, message}]로 돌려준다.
(B 라우트에만 적용되도록 라우트 클래스에서 처리)
"""

from collections.abc import Callable

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.modules.ordering.errors import validation_error_response
from app.modules.ordering.schemas import (
    BindBenefitBody,
    CreateApprovalTargetBody,
    CreateOrderPlanBody,
    ExecutionCheckBody,
    PaymentEventBody,
)
from app.modules.ordering.security import require_service_token
from app.modules.ordering.service import OrderingService


class OrderingRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def route_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                return validation_error_response(request, exc)

        return route_handler


router = APIRouter(prefix="/ordering", tags=["B · 주문"], route_class=OrderingRoute)
protected = [Depends(require_service_token)]


def get_service(db: Session = Depends(get_session)) -> OrderingService:
    return OrderingService(db)


def respond(result: tuple[int, dict]) -> JSONResponse:
    status, body = result
    return JSONResponse(status_code=status, content=body)


@router.post("/order-plans", dependencies=protected, status_code=201, summary="B-01 주문안 생성·검증")
def create_order_plan(
    body: CreateOrderPlanBody,
    service: OrderingService = Depends(get_service),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None, description="O가 인증한 사용자 (C-05 계산용)"),
):
    return respond(service.create_order_plan(body, idempotency_key, x_request_id, x_user_id))


@router.get("/order-plans/{order_plan_id}", dependencies=protected, summary="B-02 주문안·견적 조회")
def get_order_plan(
    order_plan_id: str,
    quote_id: str | None = Query(default=None),
    service: OrderingService = Depends(get_service),
    x_request_id: str | None = Header(default=None),
):
    return respond(service.get_order_plan(order_plan_id, quote_id, x_request_id))


@router.post("/order-plans/{order_plan_id}/benefit-bindings", dependencies=protected,
             summary="B-05 C 혜택 결과 연결")
def bind_benefit(
    order_plan_id: str,
    body: BindBenefitBody,
    service: OrderingService = Depends(get_service),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None, description="O가 인증한 사용자 (C-05 계산용)"),
):
    return respond(service.bind_benefit(order_plan_id, body, idempotency_key, x_request_id, x_user_id))


@router.post("/order-plans/{order_plan_id}/approval-targets", dependencies=protected, status_code=201,
             summary="B-06 승인 대상 구성")
def create_approval_target(
    order_plan_id: str,
    body: CreateApprovalTargetBody,
    service: OrderingService = Depends(get_service),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
):
    return respond(service.create_approval_target(order_plan_id, body, idempotency_key, x_request_id))


@router.post("/order-plans/{order_plan_id}/execution-checks", dependencies=protected,
             summary="B-07 결제 전 진행 조건 확인")
def check_execution(
    order_plan_id: str,
    body: ExecutionCheckBody,
    service: OrderingService = Depends(get_service),
    x_request_id: str | None = Header(default=None),
):
    return respond(service.check_execution(order_plan_id, body, x_request_id))


@router.post("/order-plans/{order_plan_id}/payment-events", dependencies=protected,
             summary="B-08 모의 결제 결과·미확정 반영")
def record_payment_event(
    order_plan_id: str,
    body: PaymentEventBody,
    service: OrderingService = Depends(get_service),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
):
    return respond(service.record_payment_event(order_plan_id, body, idempotency_key, x_request_id))


@router.get("/health", summary="B-09 상태 확인 (인증 없음)")
def health(db: Session = Depends(get_session)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # DB 연결 실패 종류가 많아 넓게 받는다
        return JSONResponse(status_code=503, content={"service": "ordering", "status": "DOWN"})
    return {"service": "ordering", "status": "UP"}
