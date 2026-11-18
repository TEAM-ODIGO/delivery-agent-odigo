"""맛 취향(taste_preferences) 테스트: "매운 음식", "달달한 거"처럼 메뉴 이름 없이 맛만 말한 경우.

이 파일에서만 가게 4곳을 더 넣는다 (테스트마다 새 DB라서 따로 지울 필요 없음).
R5 타코야끼집: 별점 5.0으로 가장 높지만 매운 메뉴는 7개 중 1개(14%)  ← 매운 걸 원할 때 위로 오면 안 됨
R6 떡볶이집:   별점 4.5, 매운 메뉴 3개 중 2개(맵기 데이터 2·3단계)
R7 국밥집:     국물 데이터가 있는 국밥 (얼큰국밥은 맵기 3)
R8 디저트카페: 크로플·케이크 (꿀조합 세트는 '꿀'이 들어가도 단 음식이 아님)
"""

import pytest

from app.modules.discovery import llm
from app.modules.discovery.extractor import extract_by_rules, extract_tastes
from tests.discovery.conftest import HEADERS, menu, restaurant

EXTRA_STORES = ["R5", "R6", "R7", "R8"]


def soup_menu(menu_id, restaurant_id, name, price, spice=None):
    item = menu(menu_id, restaurant_id, name, price, spice=spice)
    item.has_soup = True
    return item


@pytest.fixture(autouse=True)
def taste_stores(db_sessions):
    with db_sessions() as session:
        session.add_all([
            restaurant("R5", "타코야끼집-명동점", ["분식"], 5.0, 30, True, min_order=12000),
            restaurant("R6", "떡볶이집-을지로점", ["분식"], 4.5, 30, True, min_order=12000),
            restaurant("R7", "국밥집-시청점", ["한식"], 4.6, 30, True, min_order=9000),
            restaurant("R8", "디저트카페-충무로점", ["카페/디저트"], 4.7, 30, True, min_order=10000),
        ])
        session.flush()
        session.add_all([
            menu("M51", "R5", "타코야끼 오리지날", 8000, popular=True),
            menu("M52", "R5", "타코야끼 크림", 8000),
            menu("M53", "R5", "타코야끼 치즈", 8000),
            menu("M54", "R5", "타코야끼 간장", 8000),
            menu("M55", "R5", "타코야끼 명란", 8500),
            menu("M56", "R5", "타코야끼 가쓰오", 8000),
            menu("M57", "R5", "타코야끼 매운갈릭", 8500, spice=2),
            menu("M61", "R6", "국물떡볶이", 14000, spice=2),
            menu("M62", "R6", "엽기 닭발", 15000, spice=3),
            menu("M63", "R6", "로제떡볶이", 14500, spice=1),
            soup_menu("M71", "R7", "순대국밥", 10000, spice=0),
            soup_menu("M72", "R7", "얼큰국밥", 10500, spice=3),
            menu("M81", "R8", "허니버터 크로플", 7000),
            menu("M82", "R8", "초코 케이크", 7500),
            menu("M83", "R8", "꿀조합 세트", 12000),
        ])
        session.commit()


def h(request_id: str) -> dict:
    return {**HEADERS, "X-Request-ID": request_id}


def search(client, create, request_id: str, message: str, top_n: int = 3) -> tuple[dict, dict]:
    created = create(request_id, message).json()
    found = client.post(f"/discovery/requests/{request_id}/search", headers=h(request_id), json={"top_n": top_n})
    assert found.status_code == 200
    return created, found.json()


def menu_ids(candidate: dict) -> list[str]:
    ids = []
    for item in candidate["items"]:
        ids.append(item["menu_id"])
    return ids


# ---------------------------------------------------------------- 조건 추출 (규칙)

def test_taste_words_are_extracted():
    cases = {
        "둘이서 먹을만한 2만원 이하로 매운 음식 빨리 시켜줘": ["SPICY"],
        "혼자 만오천원으로 달달한 거 먹고 싶어": ["SWEET"],
        "얼큰한 국물 땡겨": ["SPICY", "SOUP"],
        "바삭한 치킨": ["CRISPY"],
        "꾸덕한 파스타": ["CHEESY"],
        "속 편한 거": ["LIGHT"],
        "시원한 국물": ["SOUP"],
    }
    for message, expected in cases.items():
        assert extract_tastes(message)[0] == expected, message


def test_negated_tastes_are_not_extracted():
    assert extract_tastes("느끼한 건 싫고 담백한 거")[0] == ["LIGHT"]
    assert extract_tastes("단 거 말고 짭짤한 거")[0] == []
    assert extract_tastes("간단한 거 먹자")[0] == []  # '간단'의 '단'은 단맛이 아님

    constraints, _ = extract_by_rules("매운 거 말고 달달한 거")
    assert constraints["taste_preferences"] == ["SWEET"]
    assert constraints["max_spice_level"] == 0

    constraints, _ = extract_by_rules("안 매운 치킨")
    assert constraints["taste_preferences"] == []
    assert constraints["max_spice_level"] == 0

    constraints, _ = extract_by_rules("살짝 매운 떡볶이")  # 덜 매운 것 → 상한 1, 매운맛 선호 아님
    assert constraints["taste_preferences"] == []
    assert constraints["max_spice_level"] == 1


# ---------------------------------------------------------------- 탐색

def test_spicy_prefers_stores_that_mainly_sell_spicy_food(client, create):
    created, result = search(client, create, "REQ101", "둘이서 3만원 매운 거")
    assert created["constraints"]["taste_preferences"] == ["SPICY"]

    candidates = result["candidates"]
    first = candidates[0]
    # 별점은 타코야끼집이 가장 높지만, 매운 메뉴가 대부분인 가게가 먼저다
    assert first["restaurant"]["restaurant_id"] != "R5"
    for candidate in candidates:
        if candidate["restaurant"]["restaurant_id"] == "R5":
            assert menu_ids(candidate) == ["M57"]  # 그래도 담는다면 매운 메뉴만
        if candidate["restaurant"]["restaurant_id"] == "R6":
            assert "M63" not in menu_ids(candidate)  # 로제떡볶이는 맵기 1이라 매운 메뉴가 아님

    spicy_store = next(c for c in candidates if c["restaurant"]["restaurant_id"] in ("R6", "R7"))
    assert "매운(맵기" in spicy_store["reasons"][1]
    assert "가게 메뉴 중 매운 메뉴" in spicy_store["reasons"][1]


def test_spicy_soup_uses_soup_and_spice_data(client, create):
    _, result = search(client, create, "REQ102", "혼자 2만원 얼큰한 국물")
    first = result["candidates"][0]
    assert first["restaurant"]["restaurant_id"] == "R7"
    assert menu_ids(first) == ["M72"]  # 순대국밥은 국물은 있지만 맵기 0
    assert "매운(맵기 3단계)·국물(국물 있음)" in first["reasons"][1]


def test_sweet_uses_menu_names_and_skips_slang(client, create):
    _, result = search(client, create, "REQ103", "둘이서 3만원 달달한 거")
    first = result["candidates"][0]
    assert first["restaurant"]["restaurant_id"] == "R8"
    assert "M83" not in menu_ids(first)  # '꿀조합'은 꿀이 아님
    assert set(menu_ids(first)) <= {"M81", "M82"}


def test_taste_not_found_still_recommends_with_suggestion(client, create):
    _, result = search(client, create, "REQ104", "혼자 3만원 담백한 거")
    assert result["candidates"]  # 담백한 메뉴가 없어도 비슷한 가게는 보여 준다
    assert "담백한 메뉴가 없어" in result["candidates"][0]["reasons"][-1]
    slots = [s["slot"] for s in result["relaxation_suggestions"]]
    assert "taste_preferences" in slots


def test_without_taste_ranking_is_unchanged(client, create):
    """맛을 말하지 않으면 예전처럼 별점 등으로만 정한다 (타코야끼집도 정상 후보)."""
    _, result = search(client, create, "REQ105", "둘이서 3만원 분식", top_n=2)
    ids = [c["restaurant"]["restaurant_id"] for c in result["candidates"]]
    assert ids[0] == "R5"


# ---------------------------------------------------------------- A-04 · LLM

def test_patch_taste_preferences(client, create):
    create("REQ106", "둘이서 3만원 아무거나")
    changed = client.patch("/discovery/requests/REQ106/constraints", headers=h("REQ106"),
                           json={"constraints": {"taste_preferences": ["SOUP", "SPICY", "SPICY"]}})
    assert changed.status_code == 200
    assert changed.json()["constraints"]["taste_preferences"] == ["SPICY", "SOUP"]  # 중복 제거·순서 맞춤

    wrong = client.patch("/discovery/requests/REQ106/constraints", headers=h("REQ106"),
                         json={"constraints": {"taste_preferences": ["SALTY"]}})
    assert wrong.status_code == 400

    cleared = client.patch("/discovery/requests/REQ106/constraints", headers=h("REQ106"),
                           json={"constraints": {"taste_preferences": None}})
    assert cleared.json()["constraints"]["taste_preferences"] == []


def test_llm_tastes_are_checked(client, create, monkeypatch):
    monkeypatch.setattr(llm.get_settings(), "llm_enabled", True)

    def fake_chat(system_prompt, user_content, json_format):
        # LLM이 모르는 코드(WRONG)를 주고, '안 매운'인데 SPICY를 넣었다고 가정
        return {
            "budget_krw": 20000, "people": 2, "cuisine": None, "excluded_ingredients": [],
            "max_spice_level": 0, "local_currency_only": False, "max_delivery_minutes": None,
            "menu_keywords": [], "taste_preferences": ["spicy", "WRONG", "SOUP"],
        }

    monkeypatch.setattr(llm, "_chat", fake_chat)
    body = create("REQ107", "둘이서 2만원 안 매운 국물").json()
    assert body["constraints"]["taste_preferences"] == ["SOUP"]


def test_llm_mistakes_do_not_override_rules(client, create, monkeypatch):
    """실제로 있었던 일: LLM이 '만오천원'을 5000으로, '국물'을 음식 종류 '분식'·메뉴 '국물'로 읽었다."""
    monkeypatch.setattr(llm.get_settings(), "llm_enabled", True)

    def wrong_chat(system_prompt, user_content, json_format):
        return {
            "budget_krw": 5000, "people": 1, "cuisine": "분식", "excluded_ingredients": ["대파"],
            "max_spice_level": None, "local_currency_only": True, "max_delivery_minutes": 30,
            "menu_keywords": ["국물"], "taste_preferences": ["SOUP", "SPICY"],
        }

    monkeypatch.setattr(llm, "_chat", wrong_chat)
    body = create("REQ108", "혼자 만오천원 국물 있는 거").json()
    constraints = body["constraints"]
    assert constraints["budget_krw"] == 15000  # 규칙이 찾은 값이 이긴다
    assert constraints["cuisine"] is None  # 문장에 분식·떡볶이 같은 말이 없음
    assert constraints["menu_keywords"] == []  # '국물'은 메뉴 이름이 아님
    assert constraints["excluded_ingredients"] == []  # 문장에 없는 재료
    assert constraints["payment_requirements"]["local_currency_only"] is False
    assert constraints["max_delivery_minutes"] is None  # 시간 얘기가 없음
    assert constraints["taste_preferences"] == ["SOUP"]  # 매운 얘기가 없음
    assert "'혼자' → people 1" in body["normalization_notes"]


def test_llm_still_fills_what_rules_missed(client, create, monkeypatch):
    """규칙이 못 읽은 표현은 문장에 근거가 있으면 LLM 값으로 채운다."""
    monkeypatch.setattr(llm.get_settings(), "llm_enabled", True)

    def helpful_chat(system_prompt, user_content, json_format):
        return {
            "budget_krw": 20000, "people": 3, "cuisine": "중식", "excluded_ingredients": ["오이"],
            "max_spice_level": None, "local_currency_only": False, "max_delivery_minutes": None,
            "menu_keywords": ["깐풍기"], "taste_preferences": [],
        }

    monkeypatch.setattr(llm, "_chat", helpful_chat)
    # '이만 원 남짓'·'우리 셋'은 규칙 정규식이 못 읽는다고 가정한 문장
    body = create("REQ109", "우리 세 식구 중국집 깐풍기 오이는 넣지 말고 예산은 이만 원 남짓").json()
    constraints = body["constraints"]
    assert constraints["people"] == 3
    assert constraints["budget_krw"] == 20000
    assert constraints["cuisine"] == "중식"
    assert "깐풍기" in constraints["menu_keywords"]
    assert constraints["excluded_ingredients"] == ["오이"]


def test_name_rules_fix_wrong_collected_data():
    """수집 데이터에서 실제로 본 잘못된 값들 (2026-11-17 을지로입구역 데이터)."""
    from app.modules.discovery.search import matches_taste

    def m(name, spice=None, soup=None):
        item = menu("X", "R1", name, 10000, spice=spice)
        item.has_soup = soup
        return item

    # 맵기: 순대국밥이 3단계로 들어와 있지만 매운 메뉴가 아니다 / '얼큰'이 붙으면 매운 메뉴
    assert not matches_taste(m("(더감칠맛) 토종순대국밥정식", spice=3), "SPICY")
    assert matches_taste(m("얼큰순대국밥", spice=None), "SPICY")
    assert not matches_taste(m("로제떡볶이", spice=2), "SPICY")
    assert matches_taste(m("마라로제떡볶이", spice=1), "SPICY")
    assert matches_taste(m("짬뽕", spice=2), "SPICY")
    assert not matches_taste(m("백짬뽕", spice=2), "SPICY")

    # 국물: 마제소바·김밥이 '국물 있음'으로 들어와 있음 / 찬 국물은 '국물'로 보지 않음 / 데이터가 없으면 이름
    assert not matches_taste(m("마제소바+공기밥", soup=True), "SOUP")
    assert not matches_taste(m("천국김밥", soup=True), "SOUP")
    assert not matches_taste(m("물냉면", soup=True), "SOUP")
    assert matches_taste(m("떡만두국", soup=True), "SOUP")
    assert not matches_taste(m("볶음짬뽕", soup=None), "SOUP")
    assert matches_taste(m("김치찌개", soup=None), "SOUP")

    # 단맛: 음료가 메인으로 들어와 있는 경우
    assert not matches_taste(m("진짜생과일] 갈아만든 꿀배"), "SWEET")
    assert matches_taste(m("허니콤보"), "SWEET")
