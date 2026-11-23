"""브랜드로 말한 요청 (이슈 #58): "맘스터치 시켜줘", "베라 먹고 싶어" → 그 브랜드 가게에서 찾는다.

이 파일에서만 가게를 더 넣는다: 배스킨라빈스 2곳(아이스크림), 맘스터치(버거), 요아정(요거트).
"""

from datetime import datetime

import pytest

from app.modules.discovery.brands import brand_words, find_brands
from app.modules.discovery.extractor import extract_by_rules
from app.modules.discovery.search import search
from app.modules.discovery.timeutil import KST
from tests.discovery.conftest import menu, restaurant

NOON = datetime(2026, 11, 20, 12, 30, tzinfo=KST)
NAMES = ["배스킨라빈스-광화문점", "배스킨라빈스-시청역점", "맘스터치-광화문점", "교촌치킨-종로점", "피자피자-을지로점",
         "요아정-을지로점", "BBQ-명동점", "BHC-을지로점"]


def test_find_brands_by_name_alias_and_short_name():
    assert find_brands("맘스터치 싸이버거 시켜줘", NAMES) == ["맘스터치"]
    assert find_brands("베라 먹고 싶어", NAMES) == ["배스킨라빈스"]
    assert find_brands("교촌 둘이서 3만원", NAMES) == ["교촌치킨"]  # 음식 이름을 뗀 앞부분
    assert find_brands("bbq 황금올리브", NAMES) == ["BBQ"] and find_brands("비에이치씨", NAMES) == ["BHC"]
    assert find_brands("요아정 먹고싶다", NAMES) == ["요아정"]
    assert find_brands("피자 둘이서 2만원", NAMES) == []  # 음식 이름은 브랜드로 보지 않는다
    assert brand_words("피자피자") == ["피자피자"] and "교촌" in brand_words("교촌치킨")


@pytest.fixture
def stores(db_sessions):
    with db_sessions() as s:
        s.add_all([
            restaurant("BR1", "배스킨라빈스-광화문점", ["카페/디저트"], 4.6, 30, True, min_order=12000),
            restaurant("BR2", "배스킨라빈스-시청역점", ["카페/디저트"], 4.5, 30, True, min_order=12000),
            restaurant("MT", "맘스터치-광화문점", ["버거"], 4.4, 30, True, min_order=12000),
            restaurant("YA", "요아정-을지로점", ["카페/디저트"], 4.9, 30, True, min_order=12000),
            restaurant("SB", "설빙-명동점", ["카페/디저트"], 4.7, 30, True, min_order=12000),
        ])
        s.flush()
        s.add_all([
            menu("BR1a", "BR1", "민트 초콜릿 칩 레디팩", 12900), menu("BR1b", "BR1", "파인트 (3가지 맛)", 9800),
            menu("BR2a", "BR2", "아몬드봉봉 레디팩", 12900),
            menu("MT1", "MT", "싸이버거 세트", 9900, menu_type="set"),
            menu("YA1", "YA", "요거트 아이스크림 (기본)", 11900),
            menu("SB1", "SB", "인절미아이스크림설빙", 12900), menu("SB2", "SB", "애플망고치즈설빙", 15900),
        ])
        s.commit()
    return db_sessions


def run(sessions, message: str) -> list[dict]:
    constraints, _ = extract_by_rules(message)
    with sessions() as s:
        from app.modules.discovery.catalog_models import Restaurant
        from sqlalchemy import select
        constraints["brands"] = find_brands(message, list(s.scalars(select(Restaurant.name))))
        bodies, _ = search(s, constraints, 3, now=NOON)
    return bodies


def test_brand_request_shows_only_that_brand_and_several_branches(stores):
    bodies = run(stores, "혼자 베라 2만원 이하")
    assert {b["restaurant"]["restaurant_id"] for b in bodies} == {"BR1", "BR2"}  # 같은 브랜드 두 매장 다
    assert all("레디팩" in b["items"][0]["name"] or "파인트" in b["items"][0]["name"] for b in bodies)


def test_brand_without_menu_word(stores):
    bodies = run(stores, "혼자 맘스터치 만원 이하")
    assert [b["restaurant"]["restaurant_id"] for b in bodies] == ["MT"]


def test_dessert_words(stores):
    bodies = run(stores, "혼자 요거트 아이스크림 2만원")
    assert bodies and bodies[0]["restaurant"]["restaurant_id"] in {"YA", "BR1", "BR2", "SB"}
    constraints, _ = extract_by_rules("둘이서 마카롱 2만원")
    assert constraints["cuisine"] == "카페/디저트" and constraints["menu_keywords"] == ["마카롱"]


def test_falls_back_with_notice_when_brand_is_closed(stores):
    from app.modules.discovery.catalog_models import Restaurant
    with stores() as s:
        s.get(Restaurant, "MT").opening_hours = "17:00 ~ 23:00"  # 점심에는 닫힘
        s.commit()
    bodies = run(stores, "혼자 맘스터치 만원 이하")
    assert bodies and all(b["restaurant"]["restaurant_id"] != "MT" for b in bodies)
    assert bodies[0]["search_notice"] == "'맘스터치' 매장은 지금 조건에 맞게 주문할 수 없어 다른 가게를 골랐어요."


def test_create_request_records_brand(client, create):
    res = create("REQ-BRAND-1", "혼자 베라 2만원 이하")
    assert res.status_code in (200, 201)
    assert "brands" in res.json()["constraints"]


def test_brand_store_appears_once_and_seolbing_menus_count_as_bingsu(stores):
    bodies = run(stores, "혼자 설빙 2만원")
    assert [b["restaurant"]["restaurant_id"] for b in bodies] == ["SB"]  # 같은 매장을 여러 번 보여 주지 않는다
    assert "match" not in bodies[0] and not bodies[0].get("search_notice")  # '설빙' 메뉴가 곧 빙수


def test_min_order_filled_with_sides_not_more_mains(db_sessions):
    """혼자 1만원인데 최소주문 1만5천원: 버거를 더 담지 않고 사이드로 채운다 (이슈 #58)."""
    with db_sessions() as s:
        s.add(restaurant("MS", "버거집-광화문점", ["버거"], 4.8, 30, True, min_order=15000, fee=3000))
        s.flush()
        burger = menu("MS1", "MS", "싸이버거 세트", 9000, menu_type="set", popular=True)
        single = menu("MS2", "MS", "싸이버거 단품", 6000, popular=True)
        s.add_all([burger, single, menu("MS3", "MS", "핫윙", 6000, menu_type="side"),
                   menu("MS4", "MS", "PARTY머리띠", 6000, menu_type="side")])
        s.commit()
    constraints, _ = extract_by_rules("혼자 버거 1만원 이하")
    with db_sessions() as s:
        bodies, _ = search(s, constraints, 3, now=NOON)
    body = next(b for b in bodies if b["restaurant"]["restaurant_id"] == "MS")
    names = [i["name"] for i in body["items"]]
    assert names == ["싸이버거 세트", "핫윙"]  # 버거 2개가 아니라 버거 + 사이드, 머리띠는 담지 않는다


def test_card_lists_main_menu_first(stores):
    """최소주문을 채운 싼 메뉴가 먼저 담겨도 카드에는 주메뉴(비싼 것)가 먼저 보인다."""
    from app.modules.discovery.search import main_first

    class _M:
        def __init__(self, name, price, kind="main"):
            self.name, self.price_with_required_options_krw, self.menu_type = name, price, kind

    class _I:
        def __init__(self, menu, hit=True):
            self.menu, self.keyword_hit = menu, hit

    lines = [(_I(_M("블록팩", 4900)), 1), (_I(_M("치즈볼", 3000, "side")), 1), (_I(_M("파인트", 11900)), 1)]
    assert [i.menu.name for i, _ in main_first(lines)] == ["파인트", "블록팩", "치즈볼"]
