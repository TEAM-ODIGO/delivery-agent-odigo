"""A HTTP 입구의 서비스 토큰 확인 (팀 공통: Authorization: Bearer {service_token}).

토큰은 팀 공통 설정(app.core.config.settings.service_token)을 쓴다. 비어 있으면 전부 거부(fail-closed).
"""

import hmac

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.errors import AppError

bearer_scheme = HTTPBearer(auto_error=False, description="서비스 토큰 (INTERNAL_SERVICE_TOKEN)")


def require_service_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(401, "UNAUTHORIZED_SERVICE", "서비스 토큰이 필요합니다.")
    expected = settings.service_token
    if not expected or not hmac.compare_digest(credentials.credentials, expected):
        raise AppError(401, "UNAUTHORIZED_SERVICE", "서비스 토큰이 올바르지 않습니다.")
