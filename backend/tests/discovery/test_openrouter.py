"""OpenRouter LLM (이슈 #62): 요청 모양, 오류별 처리, 실패하면 규칙으로. 실제 네트워크는 쓰지 않는다."""

import json

import httpx
import pytest

from app.modules.discovery import llm
from app.modules.discovery.models import LlmCall

GOOD = {
    "budget_krw": 30000, "people": 2, "cuisine": "치킨", "excluded_ingredients": [], "max_spice_level": None,
    "local_currency_only": False, "max_delivery_minutes": None, "menu_keywords": [], "taste_preferences": [],
}


@pytest.fixture
def openrouter(monkeypatch):
    s = llm.get_settings()
    monkeypatch.setattr(s, "llm_enabled", True)
    monkeypatch.setattr(s, "llm_provider", "openrouter")
    monkeypatch.setattr(s, "openrouter_api_key", "sk-or-test-key")
    monkeypatch.setattr(s, "openrouter_model", "openai/gpt-4.1-mini")
    monkeypatch.setattr(s, "openrouter_base_url", "https://openrouter.example/api/v1")
    monkeypatch.setattr(s, "openrouter_fallback_models", [])
    sent: list[dict] = []
    replies: list = []

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.append({"url": url, "json": dict(json or {}), "headers": headers})
        reply = replies.pop(0) if replies else (200, {"choices": [{"message": {"content": _dumps(GOOD)}}]})
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    return sent, replies


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _calls(db_sessions) -> list[LlmCall]:
    with db_sessions() as s:
        return list(s.query(LlmCall).order_by(LlmCall.id))


def test_request_shape_and_result(openrouter, db_sessions):
    sent, _ = openrouter
    with db_sessions() as s:
        result = llm.extract_conditions(s, "REQ-OR-1", "둘이서 3만원 치킨")
        s.commit()
    assert result.budget_krw == 30000 and result.cuisine == "치킨"
    req = sent[0]
    assert req["url"] == "https://openrouter.example/api/v1/chat/completions"
    assert req["headers"]["Authorization"] == "Bearer sk-or-test-key"
    body = req["json"]
    assert body["model"] == "openai/gpt-4.1-mini" and body["temperature"] == 0
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    assert body["provider"] == {"require_parameters": True}
    assert body["messages"][1] == {"role": "user", "content": "둘이서 3만원 치킨"}  # 주문 문장만 보낸다
    calls = _calls(db_sessions)
    assert calls[-1].success and calls[-1].model == "openrouter:openai/gpt-4.1-mini"
    assert "sk-or" not in (calls[-1].error or "")


def test_code_fenced_json_is_accepted(openrouter, db_sessions):
    _, replies = openrouter
    replies.append((200, {"choices": [{"message": {"content": "```json\n" + _dumps(GOOD) + "\n```"}}]}))
    with db_sessions() as s:
        assert llm.extract_conditions(s, "REQ-OR-2", "둘이서 3만원 치킨").people == 2


@pytest.mark.parametrize("status", [401, 402, 403])
def test_key_or_credit_errors_stop_at_once(openrouter, db_sessions, status):
    sent, replies = openrouter
    replies.append((status, {"error": {"code": status, "message": "nope"}}))
    with db_sessions() as s:
        assert llm.extract_conditions(s, f"REQ-OR-{status}", "둘이서 3만원 치킨") is None
        s.commit()
    assert len(sent) == 1  # 다시 시도하지 않는다
    assert f"OpenRouter {status}" in _calls(db_sessions)[-1].error


def test_rate_limit_is_retried_once(openrouter, db_sessions):
    sent, replies = openrouter
    replies.append((429, {"error": {"code": 429, "message": "Rate limit exceeded"}}))
    with db_sessions() as s:
        result = llm.extract_conditions(s, "REQ-OR-429", "둘이서 3만원 치킨")
        s.commit()
    assert result is not None and len(sent) == 2
    assert [c.success for c in _calls(db_sessions)] == [False, True]


def test_broken_json_and_timeout_fall_back(openrouter, db_sessions):
    sent, replies = openrouter
    replies.append((200, {"choices": [{"message": {"content": "죄송해요, JSON이 아니에요"}}]}))
    replies.append(httpx.ReadTimeout("timeout"))
    with db_sessions() as s:
        assert llm.extract_conditions(s, "REQ-OR-BAD", "둘이서 3만원 치킨") is None
    assert len(sent) == 2


def test_missing_key_does_not_call_network(openrouter, db_sessions, monkeypatch):
    sent, _ = openrouter
    monkeypatch.setattr(llm.get_settings(), "openrouter_api_key", "")
    with db_sessions() as s:
        assert llm.extract_conditions(s, "REQ-OR-NOKEY", "둘이서 3만원 치킨") is None
    assert sent == []
    assert llm.check_llm() == "NO_API_KEY"


def test_explain_uses_json_object_mode(openrouter, db_sessions, monkeypatch):
    sent, replies = openrouter
    monkeypatch.setattr(llm.get_settings(), "llm_explain_enabled", True)
    replies.append((200, {"choices": [{"message": {"content": _dumps({"reasons": ["가깝고 평점이 높아요"]})}}]}))
    with db_sessions() as s:
        assert llm.explain_candidates(s, "REQ-OR-EX", ["평점 4.8, 30분"]) == ["가깝고 평점이 높아요"]
    assert sent[0]["json"]["response_format"] == {"type": "json_object"}


def test_api_falls_back_to_rules_when_openrouter_fails(openrouter, client, create):
    _, replies = openrouter
    replies.extend([(402, {"error": {"code": 402, "message": "Insufficient credits"}})])
    body = create("REQ-OR-API", "둘이서 2만원으로 치킨").json()
    assert body["status"] == "CONFIRMED" and body["constraints"]["people"] == 2  # 규칙으로 채움


def test_health_checks_key_without_model_call(openrouter, monkeypatch):
    seen = []

    def fake_get(url, headers=None, timeout=None):
        seen.append(url)
        return httpx.Response(401, json={"error": {"code": 401}}, request=httpx.Request("GET", url))

    monkeypatch.setattr(llm.httpx, "get", fake_get)
    assert llm.check_llm() == "INVALID_KEY" and seen == ["https://openrouter.example/api/v1/key"]


def test_reasoning_model_without_temperature_is_retried(openrouter, db_sessions):
    """temperature를 받지 않는 모델(생각하는 모델)은 '처리할 곳 없음'(404)으로 거절된다 → 빼고 다시 보낸다."""
    sent, replies = openrouter
    replies.append((404, {"error": {"code": 404, "message": "No endpoints found that can handle the requested parameters."}}))
    with db_sessions() as s:
        assert llm.extract_conditions(s, "REQ-OR-T", "둘이서 3만원 치킨") is not None
    assert "temperature" in sent[0]["json"] and "temperature" not in sent[1]["json"]


def test_bad_request_reason_is_recorded(openrouter, db_sessions):
    sent, replies = openrouter
    bad = (400, {"error": {"code": 400, "message": "openai/nope is not a valid model ID"}})
    replies.extend([bad, bad, bad, bad])
    with db_sessions() as s:
        assert llm.extract_conditions(s, "REQ-OR-400", "둘이서 3만원 치킨") is None
        s.commit()
    assert "not a valid model ID" in _calls(db_sessions)[-1].error


def test_fallback_models_are_sent_and_used_model_is_recorded(openrouter, db_sessions, monkeypatch):
    """주 모델(KT)이 오류를 내면 OpenRouter가 대체 모델로 답한다 → 요청에 대체 목록, 기록에 실제 모델."""
    sent, replies = openrouter
    s = llm.get_settings()
    monkeypatch.setattr(s, "openrouter_model", "kt/midm-k-3.0-pro")
    monkeypatch.setattr(s, "openrouter_fallback_models", ["kt/midm-k-3.0-pro", "openai/gpt-4.1-mini"])
    replies.append((200, {"model": "openai/gpt-4.1-mini", "choices": [{"message": {"content": _dumps(GOOD)}}]}))
    with db_sessions() as db:
        assert llm.extract_conditions(db, "REQ-OR-FB", "둘이서 3만원 치킨") is not None
        db.commit()
    body = sent[0]["json"]
    assert body["model"] == "kt/midm-k-3.0-pro"
    assert body["models"] == ["openai/gpt-4.1-mini"]  # 주 모델은 목록에서 뺀다
    assert _calls(db_sessions)[-1].model == "openrouter:openai/gpt-4.1-mini (대체)"


def test_primary_answer_recorded_without_fallback_mark(openrouter, db_sessions, monkeypatch):
    sent, replies = openrouter
    monkeypatch.setattr(llm.get_settings(), "openrouter_model", "kt/midm-k-3.0-pro")
    replies.append((200, {"model": "kt/midm-k-3.0-pro", "choices": [{"message": {"content": _dumps(GOOD)}}]}))
    with db_sessions() as db:
        llm.extract_conditions(db, "REQ-OR-PR", "둘이서 3만원 치킨")
        db.commit()
    assert "models" not in sent[0]["json"]  # 대체 목록이 비면 보내지 않는다
    assert _calls(db_sessions)[-1].model == "openrouter:kt/midm-k-3.0-pro"


def test_default_settings_have_fallback_and_short_timeout(monkeypatch):
    from app.modules.discovery import settings as st

    for name in ("OPENROUTER_FALLBACK_MODELS", "LLM_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    st.get_settings.cache_clear()
    try:
        fresh = st.get_settings()
        assert fresh.openrouter_fallback_models == ["openai/gpt-4.1-mini"] and fresh.llm_timeout_seconds == 8
    finally:
        st.get_settings.cache_clear()
