"""A 전용 설정 (LLM). 팀 공통 설정(app.core.config)에는 없는 값만 여기서 환경변수로 읽는다.

LLM 제공자 (이슈 #62)
- openrouter (기본): OpenRouter API (OpenAI 방식). OPENROUTER_API_KEY가 있어야 한다.
- ollama: 로컬 Ollama (예전 방식, 키 없이 PC에서 돌릴 때)
LLM이 꺼져 있거나, 키가 없거나, 호출이 실패하면 규칙 기반 추출로 그대로 동작한다.
"""

import os
from dataclasses import dataclass
from functools import lru_cache

PROVIDERS = ("openrouter", "ollama")


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


@dataclass
class DiscoverySettings:
    llm_enabled: bool
    llm_provider: str
    ollama_base_url: str
    ollama_model: str
    openrouter_api_key: str
    openrouter_model: str
    openrouter_base_url: str
    openrouter_fallback_models: list[str]  # 주 모델이 오류를 내면 OpenRouter가 순서대로 시도할 모델
    llm_timeout_seconds: float
    llm_explain_enabled: bool

    @property
    def model(self) -> str:
        """지금 쓰는 모델 이름 (기록용)."""
        return self.openrouter_model if self.llm_provider == "openrouter" else self.ollama_model


@lru_cache
def get_settings() -> DiscoverySettings:
    provider = os.getenv("LLM_PROVIDER", "openrouter").strip().lower()
    return DiscoverySettings(
        llm_enabled=_bool("LLM_ENABLED", "true"),
        llm_provider=provider if provider in PROVIDERS else "openrouter",
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        openrouter_model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-mini").strip(),
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip(),
        openrouter_fallback_models=[
            m.strip() for m in os.getenv("OPENROUTER_FALLBACK_MODELS", "openai/gpt-4.1-mini").split(",") if m.strip()
        ],
        # 오류가 아니라 느릴 때는 대체 모델로 넘어가지 않으므로 짧게 (비교에서 최대 2초 안팎)
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "8")),
        llm_explain_enabled=_bool("LLM_EXPLAIN_ENABLED", "false"),
    )
