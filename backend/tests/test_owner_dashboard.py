"""점주 대시보드 (이슈 #50): 추천 흐름·빠진 이유·이번 주 할 일을 가게별로 묶는다."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.db import get_session, init_db, make_engine
from app.main import create_app
from app.modules.discovery.catalog_models import Menu, Restaurant
from app.modules.discovery.models import Candidate, DiscoveryRequest, Exclusion
from app.modules.discovery.timeutil import KST
from app.modules.orchestrator.models import CandidateView, Run
from app.modules.owner import router as owner_router
from app.modules.owner import service

NOW = datetime(2026, 11, 22, 21, 0, tzinfo=KST)


def restaurant(rid, name, min_order=15000, hours="11:00 ~ 21:00"):
    return Restaurant(
        restaurant_id=rid, name=name, collected_categories=["chinese_dish"], yogiyo_categories=["포장", "중국집"],
        rating=4.5, review_count=100, adjusted_rating=4.5, min_order_krw=min_order, base_delivery_fee_krw=2000,
        delivery_fee_tiers=[], delivery_minutes_min=20, delivery_minutes_max=35, distance_m=500,
        accepts_livelihood_voucher=True, local_currency_merchant=True, local_currency_basis="ASSUMED_FROM_VOUCHER",
        local_currency_checked_at=NOW, menu_count=2, address="서울", yogiyo_url="", collected_at=NOW,
        opening_hours=hours,
    )


def menu(mid, rid, name, desc=None):
    return Menu(
        menu_id=mid, restaurant_id=rid, menu_category="메인", name=name, description=desc, list_price_krw=9000,
        sale_price_krw=9000, price_with_required_options_krw=9000, has_required_options=False, menu_type="main",
        spice_level=None, has_soup=None, servings=None, is_signature=False, is_popular=False, is_sold_out=False,
        is_alcohol=False, review_count=0,
    )


def request(rid, at, keywords=(), budget=20000, people=2, selected=None):
    return DiscoveryRequest(
        request_id=rid, user_id="U001", original_message="", status="SELECTED" if selected else "RECOMMENDED",
        constraints={"menu_keywords": list(keywords), "budget_krw": budget, "people": people, "cuisine": "중식",
                     "taste_preferences": ["SPICY"]},
        constraints_version=1, clarification_questions=[], normalization_notes={}, selected_candidate_id=selected,
        search_count=1, initial_response={}, created_at=at, updated_at=at,
    )


def candidate(rid, cid, store, at, items=("짜장면",)):
    return Candidate(
        request_id=rid, candidate_id=cid, search_round=1, constraints_version=1, rank=1, restaurant_id=store,
        score=80, meets_all_constraints=True, is_current=True, created_at=at,
        body={"items": [{"name": n, "quantity": 1} for n in items]},
    )


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'owner.db'}")
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as s:
        s.add_all([restaurant("S1", "우리중국집", min_order=25000), restaurant("S2", "옆중국집", min_order=12000),
                   restaurant("S3", "앞중국집", min_order=13000)])
        s.add_all([menu("M1", "S1", "짜장면"), menu("M2", "S1", "짬뽕")])
        day = NOW - timedelta(days=1)
        old = NOW - timedelta(days=9)
        # 이번 주: 후보 3번(그중 1번 선택·주문 완료), 화면 표시 2번(1번은 요기요 주문 불가)
        s.add_all([request("R1", day, selected="C1"), request("R2", day), request("R3", day)])
        s.add_all([request("R4", day.replace(hour=22), keywords=["마라탕"]),
                   request("R5", day.replace(hour=23), keywords=["짬뽕"]), request("R6", old),
                   request("R7", day, keywords=["국밥"]), request("R8", day, keywords=["국밥"])])
        s.flush()
        s.add_all([candidate("R1", "C1", "S1", day), candidate("R2", "C1", "S1", day, ("짬뽕",)),
                   candidate("R3", "C1", "S1", day), candidate("R6", "C1", "S1", old)])
        s.add_all([
            Exclusion(request_id="R4", search_round=1, restaurant_id="S1", reason="CLOSED_NOW",
                      detail={"opening_hours": "11:00 ~ 21:00"}, created_at=day.replace(hour=22)),
            Exclusion(request_id="R5", search_round=1, restaurant_id="S1", reason="CLOSED_NOW",
                      detail={}, created_at=day.replace(hour=23)),
            Exclusion(request_id="R4", search_round=1, restaurant_id="S1", reason="NO_REQUESTED_MENU",
                      detail={}, created_at=day.replace(hour=22)),
            Exclusion(request_id="R5", search_round=1, restaurant_id="S1", reason="NO_REQUESTED_MENU",
                      detail={}, created_at=day.replace(hour=23)),
            # 중국집에 국밥이 없는 건 할 일 근거가 아니다
            Exclusion(request_id="R7", search_round=1, restaurant_id="S1", reason="NO_REQUESTED_MENU",
                      detail={}, created_at=day),
            Exclusion(request_id="R8", search_round=1, restaurant_id="S1", reason="NO_REQUESTED_MENU",
                      detail={}, created_at=day),
        ])
        for rid, state, sel in [("R1", "COMPLETED", True), ("R2", "CANDIDATES_READY", False)]:
            s.add(Run(request_id=rid, user_id="U001", state=state, state_version=1, row_version=1, message="",
                      delegation={}, event_seq=0, created_at=day, updated_at=day,
                      context={"selected_candidate": {"restaurant": {"restaurant_id": "S1"}}} if sel else {}))
        s.flush()
        s.add(CandidateView(request_id="R1", created_at=day,
                            shown=[{"restaurant_id": "S1", "live_status": "OK"}]))
        s.add(CandidateView(request_id="R2", created_at=day,
                            shown=[{"restaurant_id": "S1", "live_status": "UNAVAILABLE"}]))
        s.commit()
    yield sessions
    engine.dispose()


def test_funnel_and_previous_period(db):
    with db() as s:
        d = service.dashboard(s, "S1", NOW)
    assert d["funnel"] == {"candidates": 3, "shown": 2, "selected": 1, "completed": 1}
    assert d["previous"]["candidates"] == 1
    assert d["shown_unavailable"] == 1
    assert {e["reason"]: e["count"] for e in d["exclusions"]} == {"CLOSED_NOW": 2, "NO_REQUESTED_MENU": 4}
    assert sorted(d["closed_hours"]) == [22, 23]
    assert d["demo_data"] is False


def test_requested_foods_menus_and_quality(db):
    with db() as s:
        d = service.dashboard(s, "S1", NOW)
    foods = {f["name"]: f["count"] for f in d["requested"]["foods"]}
    assert foods["마라탕"] == 1 and foods["국밥"] == 2 and foods["중식"] == 3
    assert d["requested"]["tastes"][0] == {"name": "매운", "count": 7}
    assert d["requested"]["budget_per_person_median"] == 10000
    menus = {m["name"]: m for m in d["menus"]}
    assert menus["짜장면"] == {"name": "짜장면", "recommended": 2, "selected": 1}
    assert d["menu_quality"] == {"mains": 2, "no_description": 2, "no_servings": 2, "no_spice": 2}


def test_actions_have_evidence(db):
    with db() as s:
        d = service.dashboard(s, "S1", NOW)
    by_code = {a["code"]: a for a in d["actions"]}
    assert len(d["actions"]) == 3
    assert "22시, 23시" in by_code["OPENING_HOURS"]["detail"] and "2번" in by_code["OPENING_HOURS"]["detail"]
    menu_detail = by_code["MENU_NAME"]["detail"]
    assert "'마라탕'" in menu_detail and "'짬뽕'" in menu_detail and "국밥" not in menu_detail
    assert "2번" in menu_detail
    assert set(by_code) == {"OPENING_HOURS", "MENU_NAME", "LIVE_STATUS"}  # 실제로 빠진 근거가 메뉴 정리보다 먼저


def test_min_order_action_uses_category_median(db):
    with db() as s:
        r = s.get(service.Restaurant, "S1")
        median = service.category_median_min_order(s, service.primary_category(r))
    quiet = {"exclusions": {}, "shown_unavailable": 0}
    quality = {"mains": 2, "no_description": 0, "no_servings": 0, "no_spice": 0}
    got = service.actions(r, quiet, quality, {"foods": []}, [], median, {})
    assert [a["code"] for a in got] == ["MIN_ORDER"]
    assert "25,000원" in got[0]["detail"] and "13,000원" in got[0]["detail"]


def test_api(db):
    app = create_app()

    def session():
        with db() as s:
            yield s

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[owner_router.get_now] = lambda: NOW
    client = TestClient(app)
    user = {"Authorization": "Bearer dev-token-U001"}
    assert client.get("/owner/restaurants").status_code == 401
    lst = client.get("/owner/restaurants", headers=user).json()["results"]
    assert lst[0]["restaurant_id"] == "S1" and lst[0]["candidates_7d"] == 3
    assert client.get("/owner/restaurants?q=옆", headers=user).json()["results"][0]["name"] == "옆중국집"
    d = client.get("/owner/restaurants/S1/dashboard?days=7", headers=user)
    assert d.status_code == 200 and d.json()["restaurant"]["name"] == "우리중국집"
    assert client.get("/owner/restaurants/NOPE/dashboard", headers=user).status_code == 404
    assert client.get("/owner/restaurants/S1/dashboard?days=0", headers=user).status_code == 400
