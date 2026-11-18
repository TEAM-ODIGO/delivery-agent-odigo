"""가짜 E(모의결제) 서버 — 진짜 E(Spring)가 오기 전, '별도 서버 + HTTP' 구조를 그대로 확인한다.

    uv run --env-file .env uvicorn scripts.fake_e_server:app --port 8002
    (모놀리스 .env에 E_PAYMENT_URL=http://localhost:8002 를 넣고 모놀리스를 다시 켠다)

- E-01 POST /payments, E-03 GET /payments/executions/{execution_id} (명세 모양 그대로)
- 결제 직전에 모놀리스의 D-06(POST /delegation/authorizations/check)을 HTTP로 부른다.
  주소 D_URL(기본 http://localhost:8000), 토큰 INTERNAL_SERVICE_TOKEN.
- 결과 고르기: 환경변수 FAKE_E_MODE = APPROVE(기본) / DECLINE / UNKNOWN
  (같은 프로세스 가짜 E의 '#결제거절' 같은 태그는 서버가 달라 전달되지 않으므로 여기서는 이 값으로)
"""

import os
from typing import Annotated

import httpx
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

from app.core.errors import AppError, install_error_handlers
from app.fakes.payment import FakePayment
from app.fakes.scenario import ScenarioBook

MODES = {"DECLINE": "PAY_DECLINE", "UNKNOWN": "PAY_UNKNOWN"}


def http_check_authorization(d_url: str, token: str):
    client = httpx.Client(base_url=d_url, timeout=3.0)

    def check(body: dict, request_id: str) -> dict:
        r = client.post(
            "/delegation/authorizations/check",
            json=body,
            headers={"Authorization": f"Bearer {token}", "X-Request-ID": request_id},
        )
        r.raise_for_status()  # D 오류·응답 없음 → 예외 → 가짜 E는 결제하지 않음(BLOCKED)
        return r.json()

    return check


class _ModeBook(ScenarioBook):
    """모든 요청에 FAKE_E_MODE 플래그를 적용하는 장부."""

    def __init__(self, flag: str | None):
        super().__init__()
        self.flag = flag

    def has(self, request_id, flag):
        return flag == self.flag

    def consume(self, request_id, flag):
        return False


def create_app(check_authorization=None, mode: str | None = None) -> FastAPI:
    token = os.getenv("INTERNAL_SERVICE_TOKEN", "")
    check = check_authorization or http_check_authorization(
        os.getenv("D_URL", "http://localhost:8000"), token
    )
    mode = (mode or os.getenv("FAKE_E_MODE", "APPROVE")).upper()
    e = FakePayment(_ModeBook(MODES.get(mode)), check)
    app = FastAPI(title="가짜 E (모의결제)")
    install_error_handlers(app)

    def auth(authorization: str | None) -> None:
        if not token or authorization != f"Bearer {token}":
            raise AppError(401, "UNAUTHORIZED_SERVICE", "허용되지 않은 호출입니다.")

    @app.post("/payments", summary="E-01 결제 요청")
    def pay(
        body: dict,
        authorization: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ):
        auth(authorization)
        if idempotency_key != body.get("execution_id"):
            raise AppError(400, "VALIDATION_ERROR", "Idempotency-Key는 execution_id여야 합니다.")
        return e.request_payment(body)

    @app.get("/payments/executions/{execution_id}", summary="E-03 실행 ID로 결제 조회")
    def by_execution(execution_id: str, authorization: Annotated[str | None, Header()] = None):
        auth(authorization)
        return e.get_by_execution(execution_id)

    @app.get("/health")
    def health():
        return JSONResponse({"service": "payment(fake)", "status": "UP", "mode": mode})

    return app


app = create_app()
