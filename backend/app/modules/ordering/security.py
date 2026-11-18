import hmac

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.modules.ordering.config import get_settings
from app.modules.ordering.errors import OrderingError

# HTTPBearer를 쓰면 Swagger(/docs) 오른쪽 위에 Authorize 버튼이 생긴다.
# auto_error=False: 토큰이 없을 때 FastAPI 기본 403 대신 팀 공통 401 오류를 직접 던지려고
bearer_scheme = HTTPBearer(auto_error=False, description="서비스 토큰 (INTERNAL_SERVICE_TOKEN)")


def require_service_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> None:
    """서버·모듈 간 호출용 서비스 토큰 확인. 설정이 비어 있으면 모두 거부한다(fail-closed)."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise OrderingError(401, "UNAUTHORIZED_SERVICE", "서비스 토큰이 필요합니다.")

    expected = get_settings().service_token
    if not expected or not hmac.compare_digest(credentials.credentials, expected):
        raise OrderingError(401, "UNAUTHORIZED_SERVICE", "서비스 토큰이 올바르지 않습니다.")
