"""비슷한 메뉴 단계와 메뉴 조합 (이슈 #37).

이 파일에서만 가게 3곳을 더 넣는다.
RM 마라탕집: 10:30 ~ 21:30 영업 (밤에는 닫힘)
RC 중국집:   09:00 ~ 00:00 영업. 짜장면이 가장 인기, 짬뽕(매운·국물), 탕수육(2인분), 사이드 공기밥·군만두
RB 떡볶이집: 10:00 ~ 01:00 영업. 마라떡볶이(매운)
"""

from datetime import datetime

import pytest

from app.modules.discovery.extractor import extract_by_rules, find_menu_keywords
from app.modules.discovery.search import search
from app.modules.discovery.timeutil import KST
from tests.discovery.conftest import HEADERS, menu, restaurant

NIGHT = datetime(2026, 11, 20, 23, 0, tzinfo=KST)
NOON = datetime(2026, 11, 20, 12, 30, tzinfo=KST)


def soup(item, spice):
    item.has_soup = True
    item.spice_level = spice
    return item


@pytest.fixture(autouse=True)
def stores(db_sessions):
    with db_sessions() as s:
        rm = restaurant(
            "RM", "마라탕집-명동점", ["중국집"], 4.8, 30, True, min_order=13900, fee=2000
        )
        rc = restaurant(
            "RC", "중국집-을지로점", ["중국집"], 4.6, 30, True, min_order=15000, fee=2000
        )
        rb = restaurant("RB", "떡볶이집-종로점", ["분식"], 4.5, 30, True, min_order=12000, fee=2000)
        rm.opening_hours, rc.opening_hours, rb.opening_hours = (
            "10:30 ~ 21:30",
            "09:00 ~ 00:00",
            "10:00 ~ 01:00",
        )
        s.add_all([rm, rc, rb])
        s.flush()
        tangsu = menu("C3", "RC", "탕수육", 16000)
        tangsu.servings = 2.0
        jjajang = menu("C1", "RC", "짜장면", 8000, spice=0, popular=True)
        jjajang.review_count = 500
        mandu = menu("C5", "RC", "군만두", 5000, menu_type="side", popular=True)
        s.add_all(
            [
                soup(menu("M1", "RM", "대표 마라탕", 9900), 3),
                menu("M2", "RM", "마라샹궈", 15900, spice=3),
                menu("M3", "RM", "꿔바로우", 12900, spice=0),
                jjajang,
                soup(menu("C2", "RC", "짬뽕", 9000), 2),
                tangsu,
                menu("C4", "RC", "공기밥", 1000, menu_type="side"),
                mandu,
                menu("B1", "RB", "마라떡볶이", 14000, spice=3),
                menu("B2", "RB", "로제떡볶이", 14000, spice=1),
            ]
        )
        s.commit()


def run(db_sessions, message: str, now: datetime, top_n: int = 3) -> list[dict]:
    constraints, _ = extract_by_rules(message)
    with db_sessions() as s:
        bodies, _ = search(s, constraints, top_n, now=now)
    return bodies


def names(body: dict) -> list[str]:
    return [item["name"] for item in body["items"]]


def by_store(bodies: list[dict]) -> dict[str, dict]:
    return {b["restaurant"]["restaurant_id"]: b for b in bodies}


# ---------------------------------------------------------------- 비슷한 메뉴
def test_closed_dish_falls_back_to_similar_menus_not_jjajang(db_sessions):
    bodies = run(db_sessions, "마라탕 먹고 싶어 2명 3만원", NIGHT)
    stores = by_store(bodies)
    assert "RM" not in stores  # 마라탕집은 닫힘
    assert "짬뽕" in names(stores["RC"])  # 같은 중국집이라도 짜장면이 아니라 매운 국물
    assert stores["RC"]["match"]["level"] == "SIMILAR_TASTE"
    assert "마라떡볶이" in names(stores["RB"])  # 다른 종류라도 '마라' 메뉴
    assert stores["RB"]["match"]["label"] == "비슷한 메뉴"
    notice = bodies[0]["search_notice"]
    assert "마라탕집-명동점(10:30 ~ 21:30)" in notice and "영업시간이 아니에요" in notice
    assert any("'마라탕' 대신 비슷한" in r for r in stores["RC"]["reasons"])


def test_open_dish_comes_first_and_others_are_marked_similar(db_sessions):
    bodies = run(db_sessions, "마라탕 먹고 싶어 2명 3만원", NOON)
    assert bodies[0]["restaurant"]["restaurant_id"] == "RM"
    assert "match" not in bodies[0]
    assert all("match" in b for b in bodies[1:])
    assert "1곳이에요" in bodies[0]["search_notice"]


def test_requested_dish_over_budget_stays_ahead_of_similar(db_sessions):
    bodies = run(db_sessions, "마라탕 1명 1만원", NOON)
    assert bodies[0]["restaurant"]["restaurant_id"] == "RM"
    assert bodies[0]["meets_all_constraints"] is False  # 예산 초과여도 말한 메뉴가 먼저


def test_no_keyword_request_has_no_similar_marks(db_sessions):
    bodies = run(db_sessions, "둘이서 4만원 치킨", NOON)
    assert bodies and all("match" not in b for b in bodies)


def test_few_matching_stores_are_filled_with_near_misses(db_sessions):
    # 치킨집 3곳 중 1만8천원 안은 R2(18,000원)뿐 → 나머지를 "조건 일부 벗어남"으로 채운다
    bodies = run(db_sessions, "둘이서 1만8천원 치킨", NOON)
    meeting = [b for b in bodies if b["meets_all_constraints"]]
    near = [b for b in bodies if not b["meets_all_constraints"]]
    assert meeting and near
    assert bodies.index(near[0]) == len(meeting)  # 조건에 맞는 후보가 먼저
    assert "search_notice" not in bodies[0]  # 개수 안내는 화면이 보이는 개수로 만든다
    assert all(b.get("near_miss_fill") for b in near)
    assert not any(b.get("near_miss_fill") for b in meeting)


def test_near_miss_fill_is_capped_at_30_percent(db_sessions):
    # 채운 근접 후보는 모두 예산 30% 이내 초과
    from app.modules.discovery.search import NEAR_MISS_FILL_MAX

    bodies = run(db_sessions, "둘이서 1만8천원 치킨", NOON, top_n=6)
    for b in bodies:
        if b.get("near_miss_fill"):
            over = b["estimated_total_krw"] / 18000 - 1
            assert over <= NEAR_MISS_FILL_MAX


# ---------------------------------------------------------------- 조합
def test_two_dishes_named_are_both_in_basket(db_sessions):
    stores = by_store(run(db_sessions, "짜장면 탕수육 2명 3만원", NOON))
    got = names(stores["RC"])
    assert "짜장면" in got and "탕수육" in got


def test_two_people_get_two_kinds_not_one_dish_repeated(db_sessions):
    stores = by_store(run(db_sessions, "중식 2명 3만원", NOON))
    body = stores["RC"]
    mains = [i for i in body["items"] if i["name"] not in ("군만두", "공기밥")]
    assert len(mains) >= 2
    assert all(i["quantity"] <= 2 for i in body["items"])


def test_one_person_min_order_filled_with_other_dish_not_same_twice(db_sessions):
    stores = by_store(run(db_sessions, "짬뽕 1명 2만원", NOON))
    items = stores["RC"]["items"]
    assert "짬뽕" in [i["name"] for i in items]
    assert all(i["quantity"] == 1 for i in items)  # 짬뽕×2 대신 다른 메뉴로 최소주문 채움


def test_side_is_added_when_budget_allows_but_not_rice(db_sessions):
    stores = by_store(run(db_sessions, "짬뽕 2명 4만원", NOON))
    got = names(stores["RC"])
    assert "군만두" in got and "공기밥" not in got


def test_side_not_added_without_room_in_budget(db_sessions):
    stores = by_store(run(db_sessions, "짬뽕 2명 2만원", NOON))
    assert "군만두" not in names(stores["RC"])


# ---------------------------------------------------------------- 조건 해석
def test_takoyaki_is_not_read_as_taco():
    assert find_menu_keywords("타코야끼 3명 3만원") == ["타코야끼"]
    constraints, _ = extract_by_rules("타코야끼 3명 3만원")
    assert constraints["cuisine"] == "분식"


def test_api_returns_search_notice(client, create, monkeypatch):
    from app.modules.discovery import service

    monkeypatch.setattr(service, "filter_now", lambda: NIGHT)
    create("REQ-SIM-1", "마라탕 2명 3만원 시켜줘")
    found = client.post(
        "/discovery/requests/REQ-SIM-1/search",
        headers={**HEADERS, "X-Request-ID": "REQ-SIM-1"},
        json={"top_n": 3},
    ).json()
    assert "영업시간이 아니에요" in found["search_notice"]
    assert "search_notice" not in found["candidates"][0]
