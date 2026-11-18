"""E 모의결제 서버 (FastAPI).

    uv run uvicorn e_payment.main:app --port 8002

모놀리스 .env: E_PAYMENT_URL=http://localhost:8002   (O가 E-01·E-03을 부른다)
C가 E-11을 읽게 하려면 C 쪽에 E_BASE_URL=http://localhost:8002
"""

import asyncio
import contextlib
import hmac
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from e_payment import fep, schemas, service
from e_payment.config import Settings, settings as default_settings
from e_payment.db import Base, SessionLocal, engine, get_session
from e_payment.dclient import DClient, HttpDClient
from e_payment.errors import AppError, install_error_handlers, request_id_of
from e_payment.seed import seed_if_empty


def create_app(
    settings: Settings | None = None,
    d_client: DClient | None = None,
    session_factory=None,
) -> FastAPI:
    cfg = settings or default_settings
    sessions = session_factory or SessionLocal
    d = d_client or HttpDClient(cfg.d_url, cfg.service_token, cfg.d_timeout)

    def _reconcile_job() -> None:
        with sessions() as s:
            service.reconcile_all(s)

    async def _loop(seconds: float) -> None:
        while True:
            await asyncio.sleep(seconds)
            with contextlib.suppress(Exception):
                await run_in_threadpool(_reconcile_job)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if session_factory is None:
            Base.metadata.create_all(engine)
        with sessions() as s:
            seed_if_empty(s)
        task = asyncio.create_task(_loop(cfg.reconcile_seconds)) if cfg.reconcile_seconds > 0 else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="E 모의결제", lifespan=lifespan)
    install_error_handlers(app)

    def db_dep():
        with sessions() as s:
            yield s

    app.dependency_overrides[get_session] = db_dep  # 테스트·다른 DB 주입용

    def auth(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = cfg.service_token
        supplied = authorization or ""
        if not expected or not hmac.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
            raise AppError(401, "UNAUTHORIZED_SERVICE", "서비스 인증이 필요합니다.")

    Auth = Depends(auth)
    DB = Annotated[Session, Depends(get_session)]

    def _mode(override: str | None) -> str:
        if override and cfg.allow_mock_override:
            m = override.upper()
            if m not in fep.MODES:
                raise AppError(400, "VALIDATION_ERROR", "X-Mock-Outcome 값이 올바르지 않습니다.")
            return m
        return cfg.mock_mode if cfg.mock_mode in fep.MODES else "APPROVE"

    def _rid(request: Request, fallback: str | None = None) -> str:
        return request_id_of(request) or fallback or "NONE"

    # ---------------- E-01 ----------------
    @app.post("/payments", dependencies=[Auth], summary="E-01 결제 요청")
    def e01(
        body: schemas.PaymentRequest,
        request: Request,
        db: DB,
        idempotency_key: Annotated[str | None, Header()] = None,
        x_mock_outcome: Annotated[str | None, Header()] = None,
    ):
        if idempotency_key != body.execution_id:
            raise AppError(400, "VALIDATION_ERROR", "Idempotency-Key는 execution_id와 같아야 합니다.",
                           {"fields": [{"loc": "header.Idempotency-Key", "reason": "mismatch"}]})
        return service.request_payment(db, body.model_dump(), _rid(request, body.request_id), d, _mode(x_mock_outcome))

    # ---------------- E-03 (E-02보다 먼저 등록: /payments/executions/... 가 {payment_id}로 읽히지 않게) ----------------
    @app.get("/payments/executions/{execution_id}", dependencies=[Auth], summary="E-03 실행 ID로 결제 조회")
    def e03(execution_id: str, db: DB):
        return service.get_by_execution(db, execution_id, cfg.reconcile_on_read)

    # ---------------- E-02 ----------------
    @app.get("/payments/{payment_id}", dependencies=[Auth], summary="E-02 결제 상태 조회")
    def e02(payment_id: str, db: DB):
        return service.get_payment(db, payment_id, cfg.reconcile_on_read)

    # ---------------- E-04 ----------------
    @app.get("/payments", dependencies=[Auth], summary="E-04 결제 내역 조회 (1순위)")
    def e04(db: DB, user_id: Annotated[str, Query(min_length=1)], cursor: str | None = None, limit: int = 20):
        return service.list_payments(db, user_id, cursor, limit)

    # ---------------- E-05·E-06 ----------------
    @app.post("/payments/{payment_id}/refunds", status_code=202, dependencies=[Auth], summary="E-05 환불 요청 (1순위)")
    def e05(payment_id: str, body: schemas.RefundRequest, db: DB, idempotency_key: Annotated[str | None, Header()] = None):
        if not idempotency_key:
            raise AppError(400, "VALIDATION_ERROR", "Idempotency-Key 헤더가 필요합니다.",
                           {"fields": [{"loc": "header.Idempotency-Key", "reason": "missing"}]})
        return service.create_refund(db, payment_id, body.model_dump(), idempotency_key, cfg.mock_refund_mode)

    @app.get("/payments/{payment_id}/refunds/{refund_id}", dependencies=[Auth], summary="E-06 환불 상태 조회 (1순위)")
    def e06(payment_id: str, refund_id: str, db: DB):
        return service.get_refund(db, payment_id, refund_id)

    # ---------------- E-07·E-08·E-09 (E 내부, 모의 기관) ----------------
    @app.post("/internal/fep/authorizations", dependencies=[Auth], summary="E-07 모의 기관 승인 요청")
    def e07(body: schemas.FepAuthorization, db: DB, idempotency_key: Annotated[str | None, Header()] = None,
            x_mock_outcome: Annotated[str | None, Header()] = None):
        if idempotency_key != body.transaction_id:
            raise AppError(400, "VALIDATION_ERROR", "Idempotency-Key는 transaction_id와 같아야 합니다.")
        try:
            res = fep.authorize(db, body.transaction_id, body.amount_krw, _mode(x_mock_outcome))
        except fep.FepUnavailable as exc:
            db.commit()  # UNKNOWN은 기관이 처리한 기록을 남긴다
            raise AppError(503, "FEP_UNAVAILABLE", "승인 처리 결과를 확인할 수 없습니다. 상태 조회가 필요합니다.") from exc
        db.commit()
        return res

    @app.get("/internal/fep/transactions/{transaction_id}", dependencies=[Auth], summary="E-08 기관 거래 상태 조회")
    def e08(transaction_id: str, db: DB):
        try:
            return fep.lookup(db, transaction_id)
        except fep.FepUnavailable as exc:
            raise AppError(503, "STATUS_LOOKUP_UNAVAILABLE", "기관 상태 조회가 불가능합니다. 미확정 상태를 유지합니다.") from exc

    @app.post("/internal/fep/refunds", dependencies=[Auth], summary="E-09 모의 기관 환불 요청 (1순위)")
    def e09(body: schemas.FepRefund, db: DB, idempotency_key: Annotated[str | None, Header()] = None):
        if idempotency_key != body.refund_transaction_id:
            raise AppError(400, "VALIDATION_ERROR", "Idempotency-Key는 refund_transaction_id와 같아야 합니다.")
        res = fep.refund(db, body.refund_transaction_id, body.original_transaction_id, body.amount_krw, cfg.mock_refund_mode)
        db.commit()
        return res

    # ---------------- E-10 ----------------
    @app.post("/internal/reconciliations", status_code=202, dependencies=[Auth], summary="E-10 미확정 거래 대사 (1순위)")
    def e10(body: schemas.ReconciliationRequest, db: DB, idempotency_key: Annotated[str | None, Header()] = None):
        if not idempotency_key:
            raise AppError(400, "VALIDATION_ERROR", "Idempotency-Key 헤더가 필요합니다.",
                           {"fields": [{"loc": "header.Idempotency-Key", "reason": "missing"}]})
        return service.create_reconciliation(db, body.model_dump(), idempotency_key)

    # ---------------- E-11 ----------------
    @app.get("/internal/payment-methods", dependencies=[Auth], summary="E-11 결제수단 원장 조회 (C → E)")
    def e11(db: DB, user_id: Annotated[str, Query(min_length=1)], type: str | None = None):
        return service.list_methods(db, user_id, type)

    @app.get("/health", summary="E 상태 확인 (인증 없음)")
    def health(db: DB):
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"service": "payment", "status": "DOWN"}, 503)
        return {"service": "payment", "status": "UP", "mock_mode": cfg.mock_mode}

    return app


app = create_app()
