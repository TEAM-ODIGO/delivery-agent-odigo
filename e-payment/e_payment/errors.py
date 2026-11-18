"""공통 에러 형식 {"error": {"code","message","details"}, "request_id"} (팀 공통 규칙)."""

import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_\-:.]{1,64}$")


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def request_id_of(request: Request) -> str | None:
    v = request.headers.get("x-request-id")
    return v if v and _REQUEST_ID.match(v) else None


def error_body(code: str, message: str, details: dict | None, request_id: str | None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}, "request_id": request_id}


def _reason(e: dict) -> str:
    t = e.get("type", "")
    ctx = e.get("ctx") or {}
    if t in ("greater_than", "greater_than_equal"):
        return "must_be_positive" if ctx.get("gt") == 0 or ctx.get("ge") == 1 else "too_small"
    return {"missing": "missing", "extra_forbidden": "not_allowed"}.get(t, t or "invalid")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app(request: Request, exc: AppError):
        return JSONResponse(error_body(exc.code, exc.message, exc.details, request_id_of(request)), exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields, first = [], None
        for e in exc.errors():
            loc = ".".join(str(p) for p in e["loc"])
            fields.append({"loc": loc, "reason": _reason(e)})
            first = first or f"{loc.split('.')[-1]}: {e.get('msg', '잘못된 값입니다.')}"
        body = error_body("VALIDATION_ERROR", first or "요청 형식이 올바르지 않습니다.", {"fields": fields}, request_id_of(request))
        return JSONResponse(body, 400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return JSONResponse(error_body(code, str(exc.detail), {}, request_id_of(request)), exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        return JSONResponse(error_body("INTERNAL_ERROR", "일시적인 오류입니다.", {}, request_id_of(request)), 500)
