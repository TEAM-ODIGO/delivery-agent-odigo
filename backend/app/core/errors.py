"""공통 에러 형식 (팀 합의 2026-11-15).

    {"error": {"code": "...", "message": "...", "details": {}}, "request_id": "REQ001"}

- request_id는 X-Request-ID 헤더에서 가져온다. 헤더가 없거나 형식이 이상하면 null.
- 업무 규칙상 '거절'(D-02 valid:false, D-06 allowed:false)은 에러가 아니라 200 응답이다.
"""

import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_\-:.]{1,64}$")


class AppError(Exception):
    """업무 규칙 위반을 알리는 예외. 라우터가 아니라 service에서 던진다."""

    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def request_id_of(request: Request) -> str | None:
    """헤더 값을 그대로 믿지 않고, 형식이 맞을 때만 응답에 되돌려 준다 (주입 방지)."""
    value = request.headers.get("x-request-id")
    return value if value and _REQUEST_ID.match(value) else None


def error_body(
    code: str, message: str, details: dict | None = None, request_id: str | None = None
) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "details": details or {}},
        "request_id": request_id,
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        body = error_body(exc.code, exc.message, exc.details, request_id_of(request))
        return JSONResponse(status_code=exc.status, content=body)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        # 입력값을 그대로 돌려주지 않는다(로그·응답 주입 방지). 위치와 이유만 알려 준다.
        fields = [
            {"loc": ".".join(str(p) for p in e["loc"]), "reason": e["type"]} for e in exc.errors()
        ]
        body = error_body(
            "VALIDATION_ERROR",
            "요청 형식이 올바르지 않습니다.",
            {"fields": fields},
            request_id_of(request),
        )
        return JSONResponse(status_code=400, content=body)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        # 내부 오류 내용은 숨긴다. 호출한 쪽은 '허용 아님'으로 처리해야 한다 (fail-closed).
        body = error_body(
            "INTERNAL_ERROR", "일시적인 오류입니다.", request_id=request_id_of(request)
        )
        return JSONResponse(status_code=503, content=body)
