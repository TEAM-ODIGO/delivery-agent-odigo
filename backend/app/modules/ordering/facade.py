"""O가 부르는 B의 입구: 명세 body(dict) → 명세 응답(dict).

B 본체(service.OrderingService)는 요청 DTO와 DB 세션을 받고 (HTTP 상태, 응답 dict)를 돌려준다.
O의 OrderingPort(dict)에 맞추려고 여기서 바꾼다. 하는 일은 네 가지뿐이다.
 1. dict → B 요청 DTO 변환 (형식 오류는 팀 공통 400 VALIDATION_ERROR)
 2. 호출마다 새 세션을 열어 B를 부르고 응답 dict만 돌려준다 (HTTP 상태는 성공이면 버린다)
 3. B-01 body의 user_id를 호출 문맥(X-User-ID와 같은 역할)으로만 넘긴다 (B-01 명세 body에는 없다)
 4. B 오류(OrderingError)는 팀 공통 AppError의 하위 클래스라 그대로 올려 보낸다
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.clock import now as clock_now
from app.core.errors import AppError
from app.modules.ordering import ports
from app.modules.ordering import schemas as s
from app.modules.ordering.service import OrderingService

Body = dict[str, Any]


def _parse(model, body: Body):
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        fields = [
            {"loc": ".".join(str(p) for p in e["loc"]), "reason": e["type"]} for e in exc.errors()
        ]
        raise AppError(
            400, "VALIDATION_ERROR", "요청 형식이 올바르지 않습니다.", {"fields": fields}
        ) from exc


class OrderingFacade:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        benefit: ports.BenefitCalculator,
        approvals: ports.ApprovalReader,
        clock: Callable[[], datetime] = clock_now,
    ):
        self._sessions = session_factory
        self._benefit = benefit
        self._approvals = approvals
        self._clock = clock

    def _run(self, call: Callable[[OrderingService], tuple[int, dict]]) -> Body:
        with self._sessions() as db:
            _status, body = call(
                OrderingService(db, benefit=self._benefit, approvals=self._approvals, clock=self._clock)
            )
        return body

    # --- B-01 -----------------------------------------------------------------------------
    def create_order_plan(self, body: Body, *, idempotency_key: str) -> Body:
        body = dict(body)
        user_id = body.pop("user_id", None)  # B-01 명세 body에는 없다 → 호출 문맥으로만 전달
        if user_id == "-":
            user_id = None
        request = _parse(s.CreateOrderPlanBody, body)
        return self._run(
            lambda svc: svc.create_order_plan(request, idempotency_key, None, user_id)
        )

    # --- B-05 (user_id는 B-01 때 저장한 주문안 값을 쓴다) -----------------------------------
    def bind_benefit(self, order_plan_id: str, body: Body, *, idempotency_key: str) -> Body:
        request = _parse(s.BindBenefitBody, body)
        return self._run(
            lambda svc: svc.bind_benefit(order_plan_id, request, idempotency_key, None, None)
        )

    # --- B-06 -----------------------------------------------------------------------------
    def create_approval_target(
        self, order_plan_id: str, body: Body, *, idempotency_key: str
    ) -> Body:
        request = _parse(s.CreateApprovalTargetBody, body)
        return self._run(
            lambda svc: svc.create_approval_target(order_plan_id, request, idempotency_key, None)
        )

    # --- B-10 (로컬 확장, #86) ---------------------------------------------------------------
    def requote(self, order_plan_id: str, body: Body, *, idempotency_key: str) -> Body:
        request = _parse(s.RequoteBody, body)
        return self._run(lambda svc: svc.requote(order_plan_id, request, idempotency_key, None))

    # --- B-07 -----------------------------------------------------------------------------
    def check_execution(self, order_plan_id: str, body: Body) -> Body:
        request = _parse(s.ExecutionCheckBody, body)
        return self._run(lambda svc: svc.check_execution(order_plan_id, request, None))

    # --- B-08 -----------------------------------------------------------------------------
    def record_payment_event(
        self, order_plan_id: str, body: Body, *, idempotency_key: str
    ) -> Body:
        request = _parse(s.PaymentEventBody, body)
        return self._run(
            lambda svc: svc.record_payment_event(order_plan_id, request, idempotency_key, None)
        )
