"""음식 종류·맛·느낌으로 말한 요청 (이슈 #117).

- '면 종류'·'밥 종류로'·'시원한 음식'을 알아듣는다 (NOODLE·RICE·COLD)
- '매콤한 거랑 달달한 거 중에서'는 둘 다 갖춘 메뉴가 아니라 두 방향을 번갈아 보여 준다
- 담백·건강·다이어트를 말하지 않았으면 다이어트 식단 메뉴는 담지 않는다
- 샐러드를 원하면 샐러드 가게의 샐러드·포케만
- 메뉴를 정하지 않은 식사 요청에는 빵집·카페를 빼고, 음식 종류가 겹치지 않게 고른다
"""

from datetime import datetime

import pytest

from app.modules.discovery import choices
from app.modules.discovery.extractor import extract_by_rules
from app.modules.discovery.search import search
from app.modules.discovery.timeutil import KST
from tests.discovery.conftest import menu, restaurant

NOON = datetime(2026, 11, 20, 12, 30, tzinfo=KST)
FEEDBACK = "둘이서 먹을건데 약간 자극적이고 매콤하고 짭짤한 거랑 달달한거 중에서 뭐가 좋을지 모르겠어. 지금 발표하면서 기가 쫙 빨렸어."


@pytest.fixture(autouse=True)
def stores(db_sessions):
    with db_sessions() as s:
        s.add_all([
            restaurant("N1", "국수집-을지로점", ["한식"], 4.6, 25, True, min_order=8000),
            restaurant("N2", "냉면집-명동점", ["한식"], 4.5, 25, True, min_order=8000),
            restaurant("N3", "덮밥집-시청점", ["일식/돈까스"], 4.6, 25, True, min_order=8000),
            restaurant("N4", "떡볶이집-충무로점", ["분식"], 4.7, 25, True, min_order=8000),
            restaurant("N5", "디저트집-종로점", ["카페/디저트"], 4.9, 25, True, min_order=8000),
            restaurant("N6", "맛있는다이어트식단-마포점", ["샐러드", "한식"], 4.9, 25, True, min_order=8000),
            restaurant("N7", "샐러드집-광화문점", ["샐러드"], 4.6, 25, True, min_order=8000),
            restaurant("N8", "빵집-서소문점", ["카페/디저트", "샌드위치"], 4.95, 20, True, min_order=8000),
        ])
        s.flush()
        s.add_all([
            menu("N11", "N1", "잔치국수", 9000),
            menu("N12", "N1", "얼큰 칼국수", 10000, spice=2),
            menu("N21", "N2", "물냉면", 11000),
            menu("N22", "N2", "비빔냉면", 11000, spice=2),
            menu("N31", "N3", "규동 덮밥", 11000),
            menu("N32", "N3", "치킨 카레", 11000),
            menu("N41", "N4", "매운 떡볶이", 12000, spice=3),
            menu("N42", "N4", "매콤달콤 떡볶이", 12000, spice=2),
            menu("N51", "N5", "초코 케이크", 9000),
            menu("N52", "N5", "허니 크로플", 9000),
            menu("N61", "N6", "(약220kcal) 매콤달콤 비빔막국수", 9000, spice=2),
            menu("N62", "N6", "저칼로리 다이어트 짜장면", 9000),
            menu("N63", "N6", "닭가슴살 샐러드", 9000),
            menu("N71", "N7", "연어 샐러드", 11000),
            menu("N72", "N7", "스테이크 라이스볼", 12000),
            menu("N81", "N8", "우유 듬뿍 연유브레드", 9000),
        ])
        s.commit()


def run(db_sessions, message: str, top_n: int = 6) -> list[dict]:
    constraints, _ = extract_by_rules(message)
    constraints.setdefault("people", None)
    constraints["people"] = constraints["people"] or 1
    constraints.update(choices.search_prefs(constraints, message))
    with db_sessions() as s:
        bodies, _ = search(s, constraints, top_n, now=NOON)
    return bodies


def names(bodies: list[dict]) -> list[str]:
    return [b["restaurant"]["name"] for b in bodies]


def all_items(bodies: list[dict]) -> list[str]:
    return [i["name"] for b in bodies for i in b["items"]]


# ---------------------------------------------------------------- 문장 읽기
@pytest.mark.parametrize(
    ("message", "tastes"),
    [
        ("혼자 면 종류 먹고 싶어", ["NOODLE"]),
        ("면 요리로 2만원", ["NOODLE"]),
        ("밥 종류로 아무거나", ["RICE"]),
        ("시원한 음식 먹고 싶어", ["COLD"]),
        ("차가운 거 먹고 싶어", ["COLD"]),
        ("시원한 국수 먹고 싶어", ["COLD"]),
        ("시원한 국물로 해장", ["SOUP"]),
        ("라면 먹고 싶어", []),
        ("볶음밥 먹고 싶어", []),
    ],
)
def test_food_shape_words(message, tastes):
    constraints, _ = extract_by_rules(message)
    assert constraints["taste_preferences"] == tastes


def test_cold_drink_is_temperature_not_cold_food():
    constraints, _ = extract_by_rules("시원한 거 아이스 아메리카노 2잔")
    assert "COLD" not in constraints["taste_preferences"]


@pytest.mark.parametrize(
    ("message", "groups"),
    [
        (FEEDBACK, [["SPICY"], ["SWEET"]]),
        ("매운 거나 단 거 중에 뭐가 좋을까", [["SPICY"], ["SWEET"]]),
        ("매콤달콤한 거 먹고 싶어", []),
        ("매콤하고 달달한 양념치킨", []),
    ],
)
def test_taste_groups(message, groups):
    constraints, _ = extract_by_rules(message)
    assert choices.taste_groups(message, constraints["taste_preferences"]) == groups


# ---------------------------------------------------------------- 고르기
def test_noodle_request_finds_noodle_menus(db_sessions):
    bodies = run(db_sessions, "혼자 면 종류 먹고 싶어 2만원")
    assert set(names(bodies)[:2]) == {"국수집-을지로점", "냉면집-명동점"}
    assert "덮밥집-시청점" not in names(bodies)


def test_rice_request_finds_rice_menus(db_sessions):
    bodies = run(db_sessions, "밥 종류로 아무거나 2만원")
    assert names(bodies)[0] == "덮밥집-시청점"
    assert "국수집-을지로점" not in names(bodies)


def test_cold_request_finds_cold_menus_not_soup(db_sessions):
    bodies = run(db_sessions, "시원한 음식 먹고 싶어 2만원")
    assert names(bodies)[0] == "냉면집-명동점"
    assert "얼큰 칼국수" not in all_items(bodies)


def test_choice_between_tastes_shows_both_directions(db_sessions):
    bodies = run(db_sessions, FEEDBACK.replace("둘이서", "둘이서 3만원"))
    shown = names(bodies)
    assert "떡볶이집-충무로점" in shown and "디저트집-종로점" in shown
    assert bodies[0]["search_notice"] == "매운 쪽과 달달한 쪽을 번갈아 골랐어요."
    # 다이어트 식단은 지쳐서 자극적인 걸 원하는 요청에 나오지 않는다
    assert not any("kcal" in item or "다이어트" in item for item in all_items(bodies))


def test_salad_request_gets_salads_not_diet_food(db_sessions):
    bodies = run(db_sessions, "샐러드 먹고 싶어 2만원")
    items = all_items(bodies)
    assert "연어 샐러드" in items
    assert "저칼로리 다이어트 짜장면" not in items and "스테이크 라이스볼" not in items


def test_diet_menu_is_kept_when_asked(db_sessions):
    bodies = run(db_sessions, "다이어트 중이라 가벼운 거 2만원")
    assert any("다이어트" in item or "kcal" in item or "샐러드" in item for item in all_items(bodies))


def test_open_meal_request_skips_bakery_and_varies_kinds(db_sessions):
    bodies = run(db_sessions, "점심 메뉴 추천해줘 2만원", top_n=4)
    shown = names(bodies)
    assert "빵집-서소문점" not in shown and "디저트집-종로점" not in shown
    kinds = [b["restaurant"]["cuisine"] for b in bodies]
    assert len(kinds) == len(set(kinds))


# ---------------------------------------------------------------- LLM 결과 합치기
def test_llm_soup_is_dropped_for_cold_food():
    from app.modules.discovery.schemas import LlmExtraction
    from app.modules.discovery.service import _merge

    message = "시원한 음식 먹고 싶어"
    rules, notes = extract_by_rules(message)
    merged, _, _ = _merge(rules, notes, LlmExtraction(taste_preferences=["SOUP", "COLD"]), message)
    assert merged["taste_preferences"] == ["COLD"]


def test_llm_noodle_is_kept_when_grounded():
    from app.modules.discovery.schemas import LlmExtraction
    from app.modules.discovery.service import _merge

    message = "면 종류로 먹고 싶어"
    rules, notes = extract_by_rules(message)
    merged, _, _ = _merge(rules, notes, LlmExtraction(taste_preferences=["NOODLE", "RICE"]), message)
    assert merged["taste_preferences"] == ["NOODLE"]
