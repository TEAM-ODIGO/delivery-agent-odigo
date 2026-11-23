"""'베라'·'아이스크림' 요청 (이슈 #58): 줄임말을 메뉴 단어로 바꾸고, 이름에 '아이스크림'이 없는 메뉴도 찾는다."""

from app.modules.discovery.extractor import extract_by_rules, find_menu_keywords
from app.modules.discovery.search import name_has_any_keyword, name_has_keyword


def test_slang_becomes_menu_keyword_and_cuisine():
    assert find_menu_keywords("베라 먹고 싶어") == ["아이스크림"]
    assert find_menu_keywords("설빙 시켜줘") == ["빙수"]
    constraints, _ = extract_by_rules("둘이서 2만원 이하로 아이스크림")
    assert constraints["menu_keywords"] == ["아이스크림"] and constraints["cuisine"] == "카페/디저트"
    constraints, _ = extract_by_rules("혼자 베라 만원 이하")
    assert constraints["cuisine"] == "카페/디저트" and constraints["menu_keywords"] == ["아이스크림"]


def test_icecream_menus_without_the_word_in_name():
    assert name_has_keyword("민트 초콜릿 칩 레디팩", "아이스크림")
    assert name_has_keyword("파인트 (3가지 맛)", "아이스크림")
    assert name_has_keyword("NEW 블록팩 （엄마는 외계인）", "아이스크림")
    assert not name_has_keyword("디카페인 콜드브루 라떼", "아이스크림")
    assert name_has_any_keyword("짜장면", ["짜장"]) and not name_has_any_keyword("짬뽕", ["짜장"])
