"""Ollama 로컬 LLM 호출 (명세 A-09 'AI 추론 요청'의 MVP 1 버전).

공통 규칙 제안대로 REST가 아닌 A 내부 함수로 둔다.
LLM이 꺼져 있거나, 시간이 넘거나, JSON이 깨지면 None을 돌려주고 호출한 쪽은 규칙 기반 결과를 쓴다.
모든 호출은 a_llm_call 테이블에 기록한다.
"""

import json
import time

import httpx
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.modules.discovery.settings import get_settings
from app.modules.discovery.timeutil import now_kst
from app.modules.discovery.constants import CUISINES, TASTE_ORDER
from app.modules.discovery.models import LlmCall
from app.modules.discovery.schemas import LlmExtraction

MAX_ATTEMPTS = 2

EXTRACT_SYSTEM_PROMPT = """너는 배달 주문 문장에서 조건을 뽑는 도우미다. 반드시 JSON 하나만 출력한다.
규칙:
- budget_krw: 총예산(원, 정수). '2만원' → 20000. '인당 1만원'이고 2명이면 20000. 말하지 않았으면 null.
- people: 먹는 사람 수(정수). '둘이서' → 2, '혼자' → 1. 말하지 않았으면 null.
- cuisine: 아래 목록 중 하나 또는 null. 목록: {cuisines}
- excluded_ingredients: 빼 달라는 재료 단어 목록 (예: ["땅콩", "오이"]). 없으면 [].
- max_spice_level: 맵기 '상한'. 0=안 매움, 1=덜 매움, 2=보통, 3=아주 매움. '안 매운' → 0, '덜 매운' → 1.
  매운 걸 원할 때('매운 음식')는 상한이 아니므로 null로 두고 taste_preferences에 SPICY를 넣는다. 말하지 않았으면 null.
- local_currency_only: 지역화폐로만 결제하겠다고 하면 true, 아니면 false.
- max_delivery_minutes: 'N분 안에' → N. '빨리'만 있으면 40. 말하지 않았으면 null.
- menu_keywords: 문장에 나온 구체적인 메뉴 이름 (예: ["짜장면"]). 없으면 [].
- taste_preferences: 메뉴 이름 대신 맛으로 말한 것. 아래 코드만 쓴다. 없으면 [].
  SPICY=매운·매콤·얼큰, SWEET=달달·달콤·디저트, SOUP=국물·뜨끈·해장, CRISPY=바삭·튀긴,
  LIGHT=담백·가벼운·건강, CHEESY=고소·꾸덕·느끼. '느끼한 건 싫어'처럼 싫다고 한 맛은 넣지 않는다.
문장에 없는 값은 추측하지 말고 null로 둔다."""

EXTRACT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "budget_krw": {"type": ["integer", "null"]},
        "people": {"type": ["integer", "null"]},
        "cuisine": {"type": ["string", "null"]},
        "excluded_ingredients": {"type": "array", "items": {"type": "string"}},
        "max_spice_level": {"type": ["integer", "null"]},
        "local_currency_only": {"type": "boolean"},
        "max_delivery_minutes": {"type": ["integer", "null"]},
        "menu_keywords": {"type": "array", "items": {"type": "string"}},
        "taste_preferences": {"type": "array", "items": {"type": "string", "enum": TASTE_ORDER}},
    },
    "required": [
        "budget_krw", "people", "cuisine", "excluded_ingredients",
        "max_spice_level", "local_currency_only", "max_delivery_minutes", "menu_keywords", "taste_preferences",
    ],
}

EXPLAIN_SYSTEM_PROMPT = """너는 배달 추천 이유를 쓰는 도우미다. 후보마다 한국어 한 문장(40자 안팎)으로 추천 이유를 쓴다.
주어진 사실만 쓰고 숫자를 바꾸지 않는다. 반드시 {"reasons": ["...", "..."]} 형태의 JSON만 출력한다.
reasons의 개수와 순서는 입력 후보와 같아야 한다."""


def _record(db: Session, request_id: str, purpose: str, success: bool, attempt: int,
            latency_ms: int, error: str | None) -> None:
    db.add(
        LlmCall(
            request_id=request_id,
            purpose=purpose,
            model=get_settings().ollama_model,
            success=success,
            attempt=attempt,
            latency_ms=latency_ms,
            error=error,
            created_at=now_kst(),
        )
    )


def _chat(system_prompt: str, user_content: str, json_format) -> dict:
    settings = get_settings()
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        "format": json_format,
        "options": {"temperature": 0},
    }
    url = settings.ollama_base_url.rstrip("/") + "/api/chat"
    response = httpx.post(url, json=payload, timeout=settings.llm_timeout_seconds)
    response.raise_for_status()
    content = response.json()["message"]["content"]
    return json.loads(content)


def extract_conditions(db: Session, request_id: str, message: str) -> LlmExtraction | None:
    if not get_settings().llm_enabled:
        return None

    system_prompt = EXTRACT_SYSTEM_PROMPT.format(cuisines=", ".join(CUISINES.keys()))

    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            raw = _chat(system_prompt, message, EXTRACT_JSON_SCHEMA)
            result = LlmExtraction.model_validate(raw)
            _record(db, request_id, "EXTRACT", True, attempt, _elapsed_ms(started), None)
            return result
        except httpx.ConnectError as error:
            # 서버가 꺼져 있으면 다시 해도 소용없으니 바로 포기
            _record(db, request_id, "EXTRACT", False, attempt, _elapsed_ms(started), f"connect: {error}")
            return None
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValidationError) as error:
            _record(db, request_id, "EXTRACT", False, attempt, _elapsed_ms(started), str(error)[:500])

    return None


def explain_candidates(db: Session, request_id: str, facts: list[str]) -> list[str] | None:
    """후보별 사실 문장을 받아 자연스러운 추천 이유로 바꾼다. 실패하면 None (기본 문장 사용)."""
    settings = get_settings()
    if not settings.llm_enabled or not settings.llm_explain_enabled or not facts:
        return None

    user_content = json.dumps({"candidates": facts}, ensure_ascii=False)
    started = time.monotonic()
    try:
        raw = _chat(EXPLAIN_SYSTEM_PROMPT, user_content, "json")
        reasons = raw.get("reasons")
        if not isinstance(reasons, list) or len(reasons) != len(facts):
            raise ValueError("reasons 개수가 후보 수와 다릅니다.")
        cleaned = []
        for reason in reasons:
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("빈 추천 이유가 있습니다.")
            cleaned.append(reason.strip()[:200])
        _record(db, request_id, "EXPLAIN", True, 1, _elapsed_ms(started), None)
        return cleaned
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError, AttributeError) as error:
        _record(db, request_id, "EXPLAIN", False, 1, _elapsed_ms(started), str(error)[:500])
        return None


def check_llm() -> str:
    """health용: Ollama가 켜져 있고 모델이 있으면 UP."""
    settings = get_settings()
    if not settings.llm_enabled:
        return "DISABLED"
    try:
        response = httpx.get(settings.ollama_base_url.rstrip("/") + "/api/tags", timeout=2.0)
        response.raise_for_status()
        names = [model.get("name", "") for model in response.json().get("models", [])]
        if settings.ollama_model in names or f"{settings.ollama_model}:latest" in names:
            return "UP"
        return "MODEL_MISSING"
    except (httpx.HTTPError, ValueError):
        return "DOWN"


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
