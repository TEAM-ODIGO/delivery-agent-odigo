"""커피·차·음료 주문 (이슈 #107).

'커피2잔'이 카페/디저트 가게의 아무 메뉴(크레페·샌드위치)로 추천되던 문제:
'커피'가 음식 종류 단어로만 쓰이고, 음료(menu_type='drink')는 담지 않았기 때문이다.
이 파일에서만 카페 2곳을 더 넣는다.
DC 커피집: 아메리카노(HOT/ICE)·카페라떼·딸기라떼·디카페인·레몬에이드·밀크티·레몬티·크레페, 최소주문 6,000원
DB 빵집:   아메리카노·케이크·샌드위치, 음료는 '메인 없는 주문 불가', 최소주문 10,000원
"""

from datetime import datetime

import pytest

from app.modules.discovery import drinks
from app.modules.discovery.extractor import extract_by_rules, find_menu_keywords
from app.modules.discovery.search import search
from app.modules.discovery.timeutil import KST
from tests.discovery.conftest import menu, restaurant

NOON = datetime(2026, 11, 20, 12, 30, tzinfo=KST)


def drink(mid, rid, name, price, category="음료"):
    item = menu(mid, rid, name, price, menu_type="drink")
    item.menu_category = category
    return item


@pytest.fixture(autouse=True)
def cafes(db_sessions):
    with db_sessions() as s:
        dc = restaurant("DC", "커피집-을지로점", ["카페/디저트"], 4.9, 25, True, min_order=6000, fee=1000)
        db = restaurant("DB", "빵집-명동점", ["카페/디저트"], 4.95, 25, True, min_order=10000, fee=1000)
        s.add_all([dc, db])
        s.flush()
        s.add_all([
            drink("D1", "DC", "아메리카노(HOT)", 3000),
            drink("D2", "DC", "아메리카노(ICED)", 3500),
            drink("D3", "DC", "카페라떼(HOT)", 4000),
            drink("D4", "DC", "딸기라떼", 4500),
            drink("D5", "DC", "디카페인 아메리카노(ICED)", 4000),
            drink("D6", "DC", "레몬에이드", 4500),
            drink("D7", "DC", "밀크티", 4500),
            drink("D8", "DC", "레몬티(HOT)", 4000),
            menu("D9", "DC", "크레페", 6000),
            drink("B1", "DB", "아메리카노", 3000, category="음료 (메인없는 주문은 불가합니다)"),
            menu("B2", "DB", "생크림 케이크", 9000),
            menu("B3", "DB", "샌드위치", 7000),
        ])
        s.commit()


def run(db_sessions, message: str) -> list[dict]:
    constraints, _ = extract_by_rules(message)
    constraints["drink_prefs"] = drinks.search_prefs(constraints, message)
    with db_sessions() as s:
        bodies, _ = search(s, constraints, 3, now=NOON)
    return bodies


def items_of(bodies: list[dict], rid: str) -> dict[str, int]:
    body = next(b for b in bodies if b["restaurant"]["restaurant_id"] == rid)
    return {i["name"]: i["quantity"] for i in body["items"]}


# ---------------------------------------------------------------- 문장 읽기
@pytest.mark.parametrize(
    ("message", "keywords", "people"),
    [
        ("커피2잔", ["커피"], 2),
        ("아메리카노 3잔", ["아메리카노"], 3),
        ("아아 두 잔", ["아메리카노"], 2),
        ("따뜻한 카페 라떼 하나", ["카페라떼"], 1),
        ("녹차 라떼 2개", ["녹차라떼"], 2),
        ("밀크티 2개", ["밀크티"], 2),
        ("시원한 음료 3개", ["음료"], 3),
        ("레몬에이드랑 아메리카노", ["아메리카노", "레몬에이드"], None),
        ("따뜻한 차 한 잔", ["차"], 1),
    ],
)
def test_drink_words_become_menu_keywords(message, keywords, people):
    constraints, _ = extract_by_rules(message)
    assert constraints["menu_keywords"] == keywords
    assert constraints["cuisine"] == "카페/디저트"
    assert constraints["people"] == people
    assert "SOUP" not in constraints["taste_preferences"]  # '따뜻한'·'시원한'은 국물이 아니라 온도


def test_not_drinks():
    assert find_menu_keywords("아이스크림 2만원") == ["아이스크림"]
    assert find_menu_keywords("자동차 타고 가면서 먹을 김밥") == ["김밥"]
    assert extract_by_rules("혼자 국물 있는 거")[0]["taste_preferences"] == ["SOUP"]
    constraints, _ = extract_by_rules("둘이서 치킨 3만원")
    assert constraints["cuisine"] == "치킨" and constraints["people"] == 2


def test_prefs():
    assert drinks.find_prefs("아이스 아메리카노 디카페인") == ["ICE", "DECAF"]
    assert drinks.find_prefs("뜨아 2잔") == ["HOT"]
    assert drinks.find_prefs("아이스티 하나") == []  # 아이스티의 '아이스'는 온도가 아니다


def test_menu_matching():
    assert drinks.menu_matches("카페라떼(HOT)", "drink", "커피")
    assert not drinks.menu_matches("딸기라떼", "drink", "커피")  # 커피 없는 라떼
    assert not drinks.menu_matches("딸기라떼", "drink", "라떼")
    assert drinks.menu_matches("청귤차", "drink", "차")  # 사전에 없는 차도 음료면
    assert not drinks.menu_matches("크림 스파게티", "main", "차")
    assert not drinks.menu_matches("녹차 아이스크림", "main", "녹차")
    assert drinks.menu_matches("레몬에이드", "drink", "음료")
    assert not drinks.menu_matches("코카콜라 1.25L", "drink", "음료")
    assert drinks.split_cups(3, 2) == [2, 1] and drinks.split_cups(1, 2) == [1, 1]


# ---------------------------------------------------------------- 추천
def test_coffee_two_cups_gets_two_coffees(db_sessions):
    bodies = run(db_sessions, "커피2잔")
    got = items_of(bodies, "DC")
    coffee = [name for name in got if "아메리카노" in name or "카페라떼" in name]
    assert coffee and sum(got[n] for n in coffee) == 2
    assert "크레페" not in got and "딸기라떼" not in got


def test_drink_that_cannot_be_ordered_alone_is_not_used(db_sessions):
    bodies = run(db_sessions, "아메리카노 3잔")
    assert all(b["restaurant"]["restaurant_id"] != "DB" or "아메리카노" not in items_of(bodies, "DB") for b in bodies)


def test_temperature_and_decaf_preferences(db_sessions):
    assert items_of(run(db_sessions, "아이스 아메리카노 2잔"), "DC") == {"아메리카노(ICED)": 2}
    assert "아메리카노(HOT)" in items_of(run(db_sessions, "뜨거운 아메리카노 3잔"), "DC")
    assert "디카페인 아메리카노(ICED)" in items_of(run(db_sessions, "디카페인 아메리카노 2잔"), "DC")


def test_two_kinds_split_cups(db_sessions):
    got = items_of(run(db_sessions, "레몬에이드랑 아메리카노 3잔"), "DC")
    assert got.get("레몬에이드") == 1 and sum(q for n, q in got.items() if "아메리카노" in n) == 2


def test_tea_and_generic_drink(db_sessions):
    assert "레몬티(HOT)" in items_of(run(db_sessions, "따뜻한 차 두 잔"), "DC")
    got = items_of(run(db_sessions, "음료 2개"), "DC")
    assert "크레페" not in got and sum(got.values()) >= 2


def test_min_order_is_filled_with_other_menu_not_extra_cup(db_sessions):
    with db_sessions() as s:
        from app.modules.discovery.catalog_models import Restaurant

        store = s.get(Restaurant, "DC")
        store.min_order_krw = 9000
        store.delivery_fee_tiers = [{"order_amount": 9000, "delivery_fee": 1000}]
        s.commit()
    got = items_of(run(db_sessions, "아이스 아메리카노 2잔"), "DC")
    assert got["아메리카노(ICED)"] == 2
    assert sum(q for n, q in got.items() if "아메리카노" in n) == 2  # 세 번째 커피 대신 다른 메뉴로 채움
    assert len(got) == 2


def test_drink_specialist_comes_before_bakery(db_sessions):
    """평점이 조금 높은 빵집(DB, 음료 비율 낮음)보다 커피집(DC)이 먼저 (커피를 시켰을 때만)."""
    with db_sessions() as s:
        s.add(drink("B4", "DB", "카페라떼", 4000))  # 빵집도 커피를 단독으로 팔 수 있게
        s.commit()
    bodies = run(db_sessions, "카페라떼 2잔")
    ids = [b["restaurant"]["restaurant_id"] for b in bodies]
    assert ids.index("DC") < ids.index("DB")
