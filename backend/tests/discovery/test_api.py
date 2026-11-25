"""A-01·02·04·05·06·health API 시나리오 테스트 (conftest의 가짜 가게 4곳 기준).

R1 빠른치킨: 35분, 후라이드 18,000 + 배달 2,000, 지역화폐 O  ← 예산 2만원·40분이면 정답
R2 느린치킨: 58분 (배달 시간 초과)
R3 현금치킨: 30분, 지역화폐 X, 땅콩 메뉴 있음
R4 테스트피자: 피자 가게 (치킨 조건이면 제외)
"""

from app.modules.discovery import llm
from tests.discovery.conftest import ADDRESS, HEADERS

SPEC_SENTENCE = "둘이서 2만원 이하로 안 매운 치킨 빨리 시켜줘"


def h(request_id: str) -> dict:
    return {**HEADERS, "X-Request-ID": request_id}


# ---------------------------------------------------------------- A-01

def test_create_needs_clarification(create):
    response = create("REQ001", "안 매운 치킨 빨리 시켜줘")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "NEED_CLARIFICATION"
    assert body["constraints_confirmed"] is False
    assert body["constraints_version"] == 1
    assert [q["slot"] for q in body["clarification_questions"]] == ["people", "budget_krw"]
    assert body["clarification_questions"][0]["input_type"] == "SELECT"
    assert body["constraints"]["delivery_address"] == ADDRESS
    assert body["normalization_notes"] == [
        "'빨리' → 배달예상 40분 이내 (기본 규칙)",
        "'안 매운' → max_spice_level 0",
    ]


def test_create_confirmed_when_all_required(create):
    body = create("REQ001", SPEC_SENTENCE).json()

    assert body["status"] == "CONFIRMED"
    assert body["constraints_confirmed"] is True
    assert body["clarification_questions"] == []


def test_create_is_idempotent(create):
    first = create("REQ001", SPEC_SENTENCE)
    second = create("REQ001", SPEC_SENTENCE)
    other = create("REQ001", "피자 시켜줘")

    assert second.status_code == 201
    assert second.json() == first.json()
    assert other.status_code == 409
    assert other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_create_validation_errors(client, create):
    too_long = create("REQ001", "가" * 301)
    assert too_long.status_code == 400
    assert too_long.json()["error"]["code"] == "VALIDATION_ERROR"
    assert too_long.json()["request_id"] == "REQ001"

    missing = client.post("/discovery/requests", headers=h("REQ002"),
                          json={"request_id": "REQ002", "user_id": "U001", "delivery_address": ADDRESS})
    assert missing.status_code == 400
    assert {"loc": "body.message", "reason": "missing"} in missing.json()["error"]["details"]["fields"]

    bad_id = client.post("/discovery/requests", headers=HEADERS,
                         json={"request_id": "REQ 001", "user_id": "U001", "message": "치킨",
                               "delivery_address": ADDRESS})
    assert bad_id.status_code == 400


def test_header_must_match_request_id(client):
    response = client.post("/discovery/requests", headers=h("OTHER"),
                           json={"request_id": "REQ001", "user_id": "U001", "message": "치킨",
                                 "delivery_address": ADDRESS})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_service_token_required(client):
    response = client.get("/discovery/requests/REQ001")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED_SERVICE"

    wrong = client.get("/discovery/requests/REQ001", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401


# ---------------------------------------------------------------- A-02

def test_get_not_found(client):
    response = client.get("/discovery/requests/NOPE", headers=h("NOPE"))
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "REQUEST_NOT_FOUND", "message": "주문 요청을 찾을 수 없습니다.", "details": {}},
        "request_id": "NOPE",
    }


# ---------------------------------------------------------------- A-04 → A-05 → A-06 전체 흐름

def test_full_flow_clarify_search_select(client, create):
    create("REQ001", "안 매운 치킨 빨리 시켜줘")

    early = client.post("/discovery/requests/REQ001/search", headers=h("REQ001"), json={"top_n": 3})
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "INVALID_STATUS"

    # MVP 1 재질문: 선택지·입력칸 값으로 채운다
    step1 = client.patch("/discovery/requests/REQ001/constraints", headers=h("REQ001"),
                         json={"constraints": {"people": 2}}).json()
    assert step1["status"] == "NEED_CLARIFICATION"
    assert [q["slot"] for q in step1["clarification_questions"]] == ["budget_krw"]

    step2 = client.patch("/discovery/requests/REQ001/constraints", headers=h("REQ001"),
                         json={"constraints": {"budget_krw": 20000}}).json()
    assert step2["status"] == "CONFIRMED"
    assert step2["constraints_version"] == 3

    found = client.post("/discovery/requests/REQ001/search", headers=h("REQ001"), json={"top_n": 3})
    assert found.status_code == 200
    result = found.json()
    assert result["status"] == "RECOMMENDED"

    first = result["candidates"][0]
    assert first["candidate_id"] == "CAND001"
    assert first["rank"] == 1
    assert first["restaurant"]["restaurant_id"] == "R1"
    assert first["items"] == [{
        "menu_id": "M11", "name": "후라이드치킨", "quantity": 1, "option_ids": [], "unit_price_krw": 18000,
    }]
    assert first["estimated_total_krw"] == 20000
    assert first["meets_all_constraints"] is True
    assert first["reasons"][0] == "예산 안(20,000원), 35분 안에 도착 예정"
    assert "지역화폐 가능(추정)" in first["reasons"][1]

    # 피자 가게(R4)·매운 메뉴(M12)·주류(M13)는 나오지 않는다
    ids = [c["restaurant"]["restaurant_id"] for c in result["candidates"]]
    assert "R4" not in ids
    # R2는 58분이라 조건(40분) 위반. 맞는 가게가 모자랄 때 채우는 근접 후보는 30% 이내만이라 나오지 않는다 (이슈 #37)
    assert "R2" not in ids

    # 근접 후보로 풀 수 있는 조건을 알려 준다
    slots = [s["slot"] for s in result["relaxation_suggestions"]]
    assert "max_delivery_minutes" in slots

    detail = client.get("/discovery/requests/REQ001", headers=h("REQ001")).json()
    assert detail["status"] == "RECOMMENDED"
    assert [c["candidate_id"] for c in detail["candidates"]] == [c["candidate_id"] for c in result["candidates"]]
    assert detail["created_at"].endswith("+09:00")

    selected = client.post("/discovery/requests/REQ001/select",
                           headers={**h("REQ001"), "Idempotency-Key": "REQ001:select:CAND001"},
                           json={"candidate_id": "CAND001"})
    assert selected.status_code == 200
    assert selected.json()["status"] == "SELECTED"
    assert selected.json()["selected_candidate"]["restaurant"]["restaurant_id"] == "R1"
    assert selected.json()["constraints_version"] == 3

    again = client.post("/discovery/requests/REQ001/select", headers=h("REQ001"), json={"candidate_id": "CAND001"})
    assert again.status_code == 200
    assert again.json() == selected.json()

    locked = client.patch("/discovery/requests/REQ001/constraints", headers=h("REQ001"),
                          json={"constraints": {"budget_krw": 30000}})
    assert locked.status_code == 409
    assert locked.json()["error"]["code"] == "INVALID_STATUS"


def test_select_unknown_candidate(client, create):
    create("REQ001", SPEC_SENTENCE)
    client.post("/discovery/requests/REQ001/search", headers=h("REQ001"), json={})

    response = client.post("/discovery/requests/REQ001/select", headers=h("REQ001"), json={"candidate_id": "CAND999"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CANDIDATE_NOT_FOUND"


def test_select_with_wrong_idempotency_key(client, create):
    create("REQ001", SPEC_SENTENCE)
    client.post("/discovery/requests/REQ001/search", headers=h("REQ001"), json={})

    response = client.post("/discovery/requests/REQ001/select",
                           headers={**h("REQ001"), "Idempotency-Key": "REQ001:select:CAND002"},
                           json={"candidate_id": "CAND001"})
    assert response.status_code == 400


def test_changed_constraints_retire_old_candidates(client, create):
    create("REQ001", SPEC_SENTENCE)
    client.post("/discovery/requests/REQ001/search", headers=h("REQ001"), json={})

    changed = client.patch("/discovery/requests/REQ001/constraints", headers=h("REQ001"),
                           json={"constraints": {"max_delivery_minutes": 60}}).json()
    assert changed["status"] == "CONFIRMED"  # RECOMMENDED였어도 조건이 바뀌면 다시 탐색
    assert "'빨리' → 배달예상 40분 이내 (기본 규칙)" not in changed["normalization_notes"]

    detail = client.get("/discovery/requests/REQ001", headers=h("REQ001")).json()
    assert detail["candidates"] == []

    old = client.post("/discovery/requests/REQ001/select", headers=h("REQ001"), json={"candidate_id": "CAND001"})
    assert old.status_code == 409

    again = client.post("/discovery/requests/REQ001/search", headers=h("REQ001"), json={}).json()
    assert again["candidates"][0]["candidate_id"] != "CAND001"  # 새 회차는 새 ID


def test_patch_without_change_keeps_version(client, create):
    create("REQ001", SPEC_SENTENCE)
    same = client.patch("/discovery/requests/REQ001/constraints", headers=h("REQ001"),
                        json={"constraints": {"people": 2}}).json()
    assert same["constraints_version"] == 1


def test_patch_validation(client, create):
    create("REQ001", SPEC_SENTENCE)

    negative = client.patch("/discovery/requests/REQ001/constraints", headers=h("REQ001"),
                            json={"constraints": {"budget_krw": 0}})
    assert negative.status_code == 400
    assert negative.json()["error"]["details"]["fields"][0]["loc"] == "body.constraints.budget_krw"

    unknown_field = client.patch("/discovery/requests/REQ001/constraints", headers=h("REQ001"),
                                 json={"constraints": {"color": "red"}})
    assert unknown_field.status_code == 400

    unknown_cuisine = client.patch("/discovery/requests/REQ001/constraints", headers=h("REQ001"),
                                   json={"constraints": {"cuisine": "우주음식"}})
    assert unknown_cuisine.status_code == 400

    empty = client.patch("/discovery/requests/REQ001/constraints", headers=h("REQ001"), json={"constraints": {}})
    assert empty.status_code == 400


def test_local_currency_only_excludes_unconfirmed(client, create):
    create("REQ001", "혼자 3만원 치킨 지역화폐로")
    result = client.post("/discovery/requests/REQ001/search", headers=h("REQ001"), json={}).json()

    for candidate in result["candidates"]:
        assert candidate["restaurant"]["local_currency_merchant"] is True
    assert "R3" not in [c["restaurant"]["restaurant_id"] for c in result["candidates"]]


def test_excluded_ingredient_removes_menu(client, create):
    create("REQ001", "땅콩 알레르기 있어요 혼자 3만원 치킨")
    result = client.post("/discovery/requests/REQ001/search", headers=h("REQ001"), json={"top_n": 5}).json()

    for candidate in result["candidates"]:
        for item in candidate["items"]:
            assert "땅콩" not in item["name"]


def test_near_miss_when_nothing_fits(client, create):
    create("REQ001", "둘이서 5천원 치킨")
    result = client.post("/discovery/requests/REQ001/search", headers=h("REQ001"), json={}).json()

    assert result["status"] == "RECOMMENDED"
    assert result["candidates"]
    assert all(c["meets_all_constraints"] is False for c in result["candidates"])
    assert result["candidates"][0]["violations"][0]["code"] == "BUDGET_EXCEEDED"
    assert result["relaxation_suggestions"][0]["slot"] == "budget_krw"


def test_no_candidates(client, create):
    create("REQ001", "혼자 3만원 초밥")  # 회/초밥 가게가 없음
    result = client.post("/discovery/requests/REQ001/search", headers=h("REQ001"), json={}).json()

    assert result["status"] == "NO_CANDIDATES"
    assert result["candidates"] == []
    detail = client.get("/discovery/requests/REQ001", headers=h("REQ001")).json()
    assert detail["status"] == "CONFIRMED"


# ---------------------------------------------------------------- LLM

def test_llm_result_is_used_and_rules_fill_gaps(client, create, monkeypatch):
    monkeypatch.setattr(llm.get_settings(), "llm_enabled", True)

    def fake_chat(system_prompt, user_content, json_format):
        # LLM이 인원은 놓치고 예산은 뽑았다고 가정
        return {
            "budget_krw": 20000, "people": None, "cuisine": "치킨", "excluded_ingredients": [],
            "max_spice_level": 0, "local_currency_only": False, "max_delivery_minutes": 40, "menu_keywords": [],
        }

    monkeypatch.setattr(llm, "_chat", fake_chat)
    body = create("REQ001", "둘이서 이만 원 정도로 안 매운 치킨 빨리").json()

    assert body["constraints"]["budget_krw"] == 20000
    assert body["constraints"]["people"] == 2  # 규칙으로 채움
    assert body["status"] == "CONFIRMED"


def test_llm_failure_falls_back_to_rules(client, create, monkeypatch):
    monkeypatch.setattr(llm.get_settings(), "llm_enabled", True)

    def broken_chat(system_prompt, user_content, json_format):
        raise llm.httpx.ConnectError("connection refused")

    monkeypatch.setattr(llm, "_chat", broken_chat)
    body = create("REQ001", SPEC_SENTENCE).json()

    assert body["status"] == "CONFIRMED"
    assert body["constraints"]["people"] == 2


def test_llm_invalid_values_are_rejected(client, create, monkeypatch):
    monkeypatch.setattr(llm.get_settings(), "llm_enabled", True)

    def silly_chat(system_prompt, user_content, json_format):
        return {"budget_krw": -5, "people": 999}

    monkeypatch.setattr(llm, "_chat", silly_chat)
    body = create("REQ001", SPEC_SENTENCE).json()

    assert body["constraints"]["budget_krw"] == 20000
    assert body["constraints"]["people"] == 2


# ---------------------------------------------------------------- health

def test_health(client):
    response = client.get("/discovery/health")
    assert response.status_code == 200
    assert response.json() == {"service": "discovery", "status": "UP", "llm": "DISABLED"}


def test_search_excludes_shown_restaurants(client, create):
    """재추천 (이슈 #68): 이미 보여 준 가게를 빼고 찾는다. 더 없으면 지금 후보를 그대로 둔다."""
    create("REQ001", "둘이서 3만원 이하로 치킨 시켜줘")
    first = client.post("/discovery/requests/REQ001/search", headers=h("REQ001"), json={"top_n": 1}).json()
    shown = [first["candidates"][0]["restaurant"]["restaurant_id"]]

    while True:
        found = client.post("/discovery/requests/REQ001/search", headers=h("REQ001"),
                            json={"top_n": 1, "exclude_restaurant_ids": shown})
        assert found.status_code == 200
        body = found.json()
        if not body["candidates"]:
            break
        rid = body["candidates"][0]["restaurant"]["restaurant_id"]
        assert rid not in shown
        shown.append(rid)
        assert len(shown) < 10
    assert len(shown) >= 2
    assert body["status"] == "NO_CANDIDATES"

    # 더 없을 때는 마지막 후보가 그대로 살아 있어 고를 수 있다
    detail = client.get("/discovery/requests/REQ001", headers=h("REQ001")).json()
    assert [c["restaurant"]["restaurant_id"] for c in detail["candidates"]] == [shown[-1]]
