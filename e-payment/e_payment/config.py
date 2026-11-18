"""설정은 모두 환경변수에서 읽는다 (.env.example 참고)."""

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: _env("E_DATABASE_URL", "sqlite:///./e.db"))
    service_token: str = field(default_factory=lambda: _env("INTERNAL_SERVICE_TOKEN"))
    # D-06(결제 직전 검증)을 부를 모놀리스 주소
    d_url: str = field(default_factory=lambda: _env("D_URL", "http://localhost:8000"))
    d_timeout: float = field(default_factory=lambda: float(_env("D_TIMEOUT", "3")))
    # 모의 기관(FEP)이 결제를 어떻게 처리할지: APPROVE / DECLINE / UNKNOWN / LOST
    #   UNKNOWN = 기관은 승인했지만 응답이 유실됨 (E-08 조회·대사로 APPROVED 확정)
    #   LOST    = 기관이 처리했는지 알 수 없음 (조회도 안 됨, UNKNOWN 유지)
    mock_mode: str = field(default_factory=lambda: _env("E_MOCK_MODE", "APPROVE").upper())
    # 환불 처리 결과: REFUNDED / FAILED
    mock_refund_mode: str = field(
        default_factory=lambda: _env("E_MOCK_REFUND_MODE", "REFUNDED").upper()
    )
    # 1이면 E-01 요청의 X-Mock-Outcome 헤더(APPROVE/DECLINE/UNKNOWN/LOST)로 결과를 고를 수 있다.
    # 시연·테스트 전용. 운영처럼 쓸 때는 꺼 둔다.
    allow_mock_override: bool = field(
        default_factory=lambda: _env("E_ALLOW_MOCK_OVERRIDE") in ("1", "true")
    )
    # E-02·E-03을 읽을 때 UNKNOWN 건이 있으면 E-08로 한 번 확인해 확정한다
    reconcile_on_read: bool = field(
        default_factory=lambda: _env("E_RECONCILE_ON_READ", "1") in ("1", "true")
    )
    # 미확정 거래를 주기적으로 대사하는 간격(초). 0이면 끔
    reconcile_seconds: float = field(
        default_factory=lambda: float(_env("E_RECONCILE_SECONDS", "0"))
    )


settings = Settings()
