"""서버 설정. 값은 코드가 아니라 환경변수(.env)에서 읽는다."""

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    # 개발 기본값은 SQLite 파일. 팀 공용 개발·시연은 PostgreSQL 주소로 바꾼다.
    #   예) postgresql+psycopg://user:pw@localhost:5432/delivery
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./dev.db"))

    # 서비스 간 인증 토큰 (팀 공통: Authorization: Bearer {service_token}).
    # E가 D-06을 HTTP로 부를 때 쓴다. 비어 있으면 D-06 HTTP 호출은 전부 거부된다 (fail-closed).
    # 예전 이름 DELEGATION_SERVICE_KEY도 읽어 준다.
    service_token: str = field(
        default_factory=lambda: (
            _env("INTERNAL_SERVICE_TOKEN", "") or _env("DELEGATION_SERVICE_KEY", "")
        )
    )

    # C(혜택) 서버 주소. 비어 있으면 임시 고정 규칙(StaticBenefitPort)을 쓴다.
    # C를 따로 띄워 함께 돌려 볼 때: C_BENEFIT_URL=http://localhost:8001
    c_benefit_url: str = field(default_factory=lambda: _env("C_BENEFIT_URL", ""))

    # 1이면 진짜 C 대신 가짜 C(app/fakes/benefit.py)를 쓴다. C 없이 O·D만 시험할 때.
    use_fake_c: bool = field(default_factory=lambda: _env("USE_FAKE_C", "") in ("1", "true"))

    # 1이면 진짜 A·B 대신 가짜 A·B(app/fakes)를 쓴다. 진짜 A·B 없이 O·C·D만 시험할 때.
    use_fake_a: bool = field(default_factory=lambda: _env("USE_FAKE_A", "") in ("1", "true"))
    use_fake_b: bool = field(default_factory=lambda: _env("USE_FAKE_B", "") in ("1", "true"))
    # B가 만드는 견적의 유효시간(초). 가짜 B와 같은 30분
    ordering_quote_ttl_seconds: int = field(
        default_factory=lambda: int(_env("ORDERING_QUOTE_TTL_SECONDS", "1800"))
    )

    # 승인 유효시간 상한(분). 실제 유효시간 = min(견적 만료, 지금 + 이 값)
    approval_ttl_minutes: int = field(
        default_factory=lambda: int(_env("APPROVAL_TTL_MINUTES", "10"))
    )

    # --- O (오케스트레이터) ---
    # E(모의결제) 서버 주소. 비어 있으면 같은 프로세스의 가짜 E(app/fakes)를 쓴다.
    #   가짜 E 서버를 따로 띄울 때: E_PAYMENT_URL=http://localhost:8002
    e_payment_url: str = field(default_factory=lambda: _env("E_PAYMENT_URL", ""))
    # MVP 1 임시 로그인: "토큰:user_id" 를 쉼표로. 로그인 기능이 생기면 교체
    dev_user_tokens: str = field(
        default_factory=lambda: _env("DEV_USER_TOKENS", "dev-token-U001:U001,dev-token-U002:U002")
    )
    # O-04 관리자 토큰. 비어 있으면 O-04는 아무도 못 쓴다 (fail-closed)
    admin_access_token: str = field(default_factory=lambda: _env("ADMIN_ACCESS_TOKEN", ""))
    # 하위 호출 재시도 간격(초). 재시도 횟수는 2회 고정 (설계 문서 03 6절)
    orchestrator_retry_delay: float = field(
        default_factory=lambda: float(_env("ORCHESTRATOR_RETRY_DELAY", "0.2"))
    )

    # 결제 결과 확인·B-08 재전송 주기(초). 0이면 끔 (서버 시작 시 1회 + O-03 조회 때만)
    reconcile_seconds: float = field(
        default_factory=lambda: float(_env("ORCHESTRATOR_RECONCILE_SECONDS", "60"))
    )

    def user_tokens(self) -> dict[str, str]:
        pairs = (p.split(":", 1) for p in self.dev_user_tokens.split(",") if ":" in p)
        return {token.strip(): user.strip() for token, user in pairs if token.strip()}


settings = Settings()
