"""공통 오류. 팀 공통 AppError와 오류 본문 형식을 그대로 쓴다 (app/core/errors.py).
핸들러는 app 전체에 이미 설치돼 있어서 C가 따로 설치하지 않는다."""
from app.core.errors import AppError, error_body  # noqa: F401
