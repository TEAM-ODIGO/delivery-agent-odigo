"""검색에서 후보가 되지 못한 가게와 이유 기록 (이슈 #48)."""

from datetime import datetime

from sqlalchemy import select

from app.modules.discovery import service, tracking
from app.modules.discovery.catalog_models import Restaurant
from app.modules.discovery.models import Exclusion
from app.modules.discovery.timeutil import KST
from tests.discovery.conftest import HEADERS

NOON = datetime(2026, 11, 20, 12, 30, tzinfo=KST)
SINCE = datetime(2026, 1, 1, tzinfo=KST)


def h(rid):
    return {**HEADERS, "X-Request-ID": rid}


def test_search_records_why_stores_were_left_out(client, create, db_sessions, monkeypatch):
    with db_sessions() as s:
        s.get(Restaurant, "R2").opening_hours = "10:00 ~ 11:00"  # 12:30에는 닫힘
        s.commit()
    monkeypatch.setattr(service, "filter_now", lambda: NOON)

    create("REQ-EX-1", "둘이서 2만원 치킨 시켜줘")
    found = client.post(
        "/discovery/requests/REQ-EX-1/search", headers=h("REQ-EX-1"), json={"top_n": 1}
    ).json()
    chosen = found["candidates"][0]["restaurant"]["restaurant_id"]

    with db_sessions() as s:
        rows = {
            r.restaurant_id: r
            for r in s.scalars(select(Exclusion).where(Exclusion.request_id == "REQ-EX-1"))
        }
    assert chosen not in rows  # 후보가 된 가게는 남기지 않음
    assert "R4" not in rows  # 음식 종류가 다른 피자집도 남기지 않음
    assert rows["R2"].reason == "CLOSED_NOW" and rows["R2"].detail == {
        "opening_hours": "10:00 ~ 11:00"
    }
    other = ({"R1", "R3"} - {chosen}).pop()
    assert rows[other].reason == "OUT_OF_RANK" and rows[other].detail["rank"] >= 2
    assert rows["R2"].search_round == 1


def test_budget_exceeded_is_recorded_with_numbers(client, create, db_sessions, monkeypatch):
    monkeypatch.setattr(service, "filter_now", lambda: None)
    create("REQ-EX-2", "둘이서 1만원 치킨 시켜줘")  # 모든 치킨집이 예산 초과
    client.post("/discovery/requests/REQ-EX-2/search", headers=h("REQ-EX-2"), json={"top_n": 1})
    with db_sessions() as s:
        rows = s.scalars(select(Exclusion).where(Exclusion.request_id == "REQ-EX-2")).all()
    budget = [r for r in rows if r.reason == "BUDGET_EXCEEDED"]
    assert budget and all(r.detail["limit"] == 10000 and r.detail["actual"] > 10000 for r in budget)


def test_restaurant_summary_counts_candidates_selection_and_reasons(
    client, create, db_sessions, monkeypatch
):
    monkeypatch.setattr(service, "filter_now", lambda: None)
    create("REQ-EX-3", "둘이서 2만원 치킨 시켜줘")
    found = client.post(
        "/discovery/requests/REQ-EX-3/search", headers=h("REQ-EX-3"), json={"top_n": 1}
    ).json()
    cand = found["candidates"][0]
    client.post(
        "/discovery/requests/REQ-EX-3/select",
        headers=h("REQ-EX-3"),
        json={"candidate_id": cand["candidate_id"]},
    )
    rid = cand["restaurant"]["restaurant_id"]
    with db_sessions() as s:
        got = tracking.restaurant_summary(s, rid, SINCE)
        other = tracking.restaurant_summary(s, ({"R1", "R2", "R3"} - {rid}).pop(), SINCE)
    assert (got["candidates"], got["selected"], got["exclusions"]) == (1, 1, {})
    assert other["candidates"] == 0 and other["exclusions"] == {"OUT_OF_RANK": 1}
