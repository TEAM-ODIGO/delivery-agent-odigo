"""인증: 서버·모듈 간은 service_token, 관리자 직접 호출은 admin_access_token. health는 인증 없음.
토큰은 팀 설정(app/core/config.py, .env의 INTERNAL_SERVICE_TOKEN · ADMIN_ACCESS_TOKEN)을 읽는다.
값이 비어 있으면 아무도 통과하지 못한다 (fail-closed).
HTTPBearer를 쓰므로 Swagger(/docs)에 Authorize 버튼이 생긴다."""
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

from .errors import AppError

_bearer = HTTPBearer(auto_error=False)


def _is(token: str | None, expected: str) -> bool:
    return bool(token) and bool(expected) and token == expected


def require_service(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    token = credentials.credentials if credentials else None
    if not (_is(token, settings.service_token) or _is(token, settings.admin_access_token)):
        raise AppError(401, "UNAUTHORIZED_SERVICE", "서비스 토큰이 올바르지 않습니다.")


def require_admin(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    token = credentials.credentials if credentials else None
    if _is(token, settings.admin_access_token):
        return
    if _is(token, settings.service_token):
        raise AppError(403, "FORBIDDEN", "관리자 토큰이 필요한 API입니다.")
    raise AppError(401, "UNAUTHORIZED_SERVICE", "토큰이 올바르지 않습니다.")
