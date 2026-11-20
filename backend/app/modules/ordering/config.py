"""B(주문) 모듈 전용 설정. 환경 변수에서 읽는다."""

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class OrderingSettings:
    # 서버·모듈 간 공용 서비스 토큰 (Authorization: Bearer {service_token}). 비어 있으면 모두 거부
    service_token: str = field(default_factory=lambda: _env("INTERNAL_SERVICE_TOKEN", ""))
    # 견적 유효시간(초). 팀 확정 전 임시값 10분
    quote_ttl_seconds: int = field(default_factory=lambda: int(_env("ORDERING_QUOTE_TTL_SECONDS", "600")))
    # B → C-05 혜택 계산, B → D-04 승인 조회 (단독 실행일 때 HTTP 주소. 비우면 '연결 안 됨'으로 503)
    c_benefit_url: str = field(default_factory=lambda: _env("C_BENEFIT_URL", ""))
    d_delegation_url: str = field(default_factory=lambda: _env("D_DELEGATION_URL", ""))
    http_timeout_seconds: float = field(default_factory=lambda: float(_env("ORDERING_HTTP_TIMEOUT", "3")))


@lru_cache
def get_settings() -> OrderingSettings:
    return OrderingSettings()
