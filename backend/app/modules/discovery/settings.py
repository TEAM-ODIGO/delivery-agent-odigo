"""A 전용 설정 (LLM). 팀 공통 설정(app.core.config)에는 없는 값만 여기서 환경변수로 읽는다.

LLM이 꺼져 있거나 Ollama가 없으면 규칙 기반 추출로 그대로 동작한다.
"""

import os
from dataclasses import dataclass
from functools import lru_cache


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


@dataclass
class DiscoverySettings:
    llm_enabled: bool
    ollama_base_url: str
    ollama_model: str
    llm_timeout_seconds: float
    llm_explain_enabled: bool


@lru_cache
def get_settings() -> DiscoverySettings:
    return DiscoverySettings(
        llm_enabled=_bool("LLM_ENABLED", "true"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "20")),
        llm_explain_enabled=_bool("LLM_EXPLAIN_ENABLED", "false"),
    )
