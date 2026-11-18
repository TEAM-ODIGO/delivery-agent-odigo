"""B 오류. 명세서의 B 오류 details 모양을 항상 채운다.

    {"error": {"code", "message", "details": {
        "current_order_version", "current_benefit_revision", "current_approval_target_id",
        "next_action", "retryable", ("fields")}}, "request_id"}

팀 공용 AppError를 상속하므로 O는 다른 모듈 오류와 똑같이 받아 처리한다 (status ≥ 500이면 재시도).
"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import AppError, error_body, request_id_of
from app.modules.ordering.constants import NextAction

# 422 응답에서 필드별로 보여 줄 문장. 없는 필드는 공통 문장을 쓴다
FIELD_MESSAGES = {
    "quantity": "수량은 양의 정수여야 합니다.",
    "items": "주문할 메뉴를 한 개 이상 보내 주세요.",
    "menu_id": "메뉴 ID 형식이 올바르지 않습니다.",
    "option_ids": "옵션 ID 목록 형식이 올바르지 않습니다.",
    "budget_basis": "budget_basis는 POST_FINANCIAL 또는 PRE_FINANCIAL이어야 합니다.",
    "status": "status는 PENDING·APPROVED·FAILED·UNKNOWN 중 하나여야 합니다.",
    "observed_at": "observed_at은 시간대가 있는 ISO 8601 시각이어야 합니다.",
    "stage": "stage는 MOCK_PAYMENT여야 합니다.",
}
DEFAULT_FIELD_MESSAGE = "값이 올바르지 않습니다."


class OrderingError(AppError):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        next_action: str = NextAction.NONE,
        retryable: bool = False,
        current_order_version: int | None = None,
        current_benefit_revision: int | None = None,
        current_approval_target_id: str | None = None,
        fields: list[dict] | None = None,
    ):
        details = {
            "current_order_version": current_order_version,
            "current_benefit_revision": current_benefit_revision,
            "current_approval_target_id": current_approval_target_id,
            "next_action": str(next_action),
            "retryable": retryable,
        }
        if fields is not None:
            details["fields"] = fields
        super().__init__(status, code, message, details)


def field_name(loc: tuple) -> str:
    """('body', 'items', 0, 'quantity') → 'items[0].quantity'"""
    text = ""
    for part in loc:
        if part in ("body", "query", "path", "header"):
            continue
        if isinstance(part, int):
            text += f"[{part}]"
        elif text:
            text += "." + str(part)
        else:
            text = str(part)
    return text or "body"


def validation_error_response(request: Request, exc: RequestValidationError) -> JSONResponse:
    """명세 B-01 예시와 같은 422 VALIDATION_ERROR (fields: [{field, message}])."""
    fields = []
    for error in exc.errors():
        name = field_name(tuple(error.get("loc", ())))
        last = name.split(".")[-1].split("[")[0]
        fields.append({"field": name, "message": FIELD_MESSAGES.get(last, DEFAULT_FIELD_MESSAGE)})
    message = fields[0]["message"] if fields else "요청 형식이 올바르지 않습니다."
    error = OrderingError(422, "VALIDATION_ERROR", message, fields=fields)
    body = error_body(error.code, error.message, error.details, request_id_of(request))
    return JSONResponse(status_code=422, content=body)
