"""영업시간으로 지금 닫힌 식당을 A-05 후보에서 미리 빼기 (이슈 ⑤)."""

import csv
from datetime import datetime

from sqlalchemy import inspect, text

from app.core.db import make_engine
from app.modules.discovery import hours
from app.modules.discovery.catalog_models import Restaurant
from app.modules.discovery.timeutil import KST
from tests.discovery.test_tastes import search


def at(hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 11, 20, hh, mm, tzinfo=KST)


# ---------------------------------------------------------------- 영업시간 읽기
def test_parse_hours():
    assert hours.parse("11:00 ~ 23:40") == (660, 1420)
    assert hours.parse("17:00 ~ 03:00") == (1020, 180)
    assert hours.parse("00:00 ~ 00:00") == (0, 0)
    assert hours.parse("24:00 ~ 24:00") == (0, 0)
    for bad in (None, "", "연중무휴", "11시 ~ 22시", "25:00 ~ 01:00"):
        assert hours.parse(bad) is None, bad


def test_is_open_normal_overnight_and_24h():
    assert hours.is_open("11:00 ~ 23:40", at(12)) is True
    assert hours.is_open("11:00 ~ 23:40", at(23, 40)) is False  # 닫는 시각부터는 닫힘
    assert hours.is_open("11:00 ~ 23:40", at(9, 30)) is False
    assert hours.is_open("17:00 ~ 03:00", at(23, 39)) is True  # 자정을 넘겨 영업
    assert hours.is_open("17:00 ~ 03:00", at(2, 59)) is True
    assert hours.is_open("17:00 ~ 03:00", at(14, 51)) is False
    assert hours.is_open("00:00 ~ 00:00", at(4)) is True  # 24시간
    assert hours.is_open(None, at(4)) is None  # 모르면 판단하지 않음


def test_filter_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("DISCOVERY_OPENING_HOURS_FILTER", "0")
    assert hours.filter_now() is None
    monkeypatch.setenv("DISCOVERY_OPENING_HOURS_FILTER", "1")
    assert hours.filter_now() is not None


# ---------------------------------------------------------------- A-05 후보에서 빼기
def _set_hours(db_sessions, values: dict[str, str | None]) -> None:
    with db_sessions() as s:
        for rid, value in values.items():
            s.get(Restaurant, rid).opening_hours = value
        s.commit()


def _ids(found: dict) -> list[str]:
    return [c["restaurant"]["restaurant_id"] for c in found["candidates"]]


def test_closed_restaurant_is_left_out_of_candidates(client, create, db_sessions, monkeypatch):
    monkeypatch.setenv("DISCOVERY_OPENING_HOURS_FILTER", "1")
    monkeypatch.setattr(hours, "now_kst", lambda: at(9, 30))  # 오전 9시 30분
    # R1: 아직 안 엶 / R2: 영업 중(24시간) / R3: 영업시간 모름 → 후보에 남음
    _set_hours(db_sessions, {"R1": "11:00 ~ 23:00", "R2": "00:00 ~ 00:00", "R3": None})
    _, found = search(client, create, "REQ-HOURS-1", "둘이서 4만원 이하로 치킨 시켜줘")
    ids = _ids(found)
    assert "R1" not in ids
    assert "R2" in ids and "R3" in ids


def test_same_restaurant_is_back_when_open(client, create, db_sessions, monkeypatch):
    monkeypatch.setenv("DISCOVERY_OPENING_HOURS_FILTER", "1")
    monkeypatch.setattr(hours, "now_kst", lambda: at(12))
    _set_hours(db_sessions, {"R1": "11:00 ~ 23:00"})
    _, found = search(client, create, "REQ-HOURS-2", "둘이서 4만원 이하로 치킨 시켜줘")
    assert "R1" in _ids(found)


def test_filter_off_keeps_closed_restaurants(client, create, db_sessions, monkeypatch):
    monkeypatch.setenv("DISCOVERY_OPENING_HOURS_FILTER", "0")
    monkeypatch.setattr(hours, "now_kst", lambda: at(9, 30))
    _set_hours(db_sessions, {"R1": "11:00 ~ 23:00"})
    _, found = search(client, create, "REQ-HOURS-3", "둘이서 4만원 이하로 치킨 시켜줘")
    assert "R1" in _ids(found)


# ---------------------------------------------------------------- DB 준비 (예전 DB · CSV 맞추기)
def test_old_db_gets_column_and_hours_from_csv(tmp_path, db_sessions):
    engine = make_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:  # opening_hours 열이 없던 예전 restaurant 테이블
        conn.execute(text("CREATE TABLE restaurant (restaurant_id VARCHAR(64) PRIMARY KEY)"))
    hours.ensure_column(engine)
    hours.ensure_column(engine)  # 두 번 불러도 괜찮다
    cols = {c["name"] for c in inspect(engine).get_columns("restaurant")}
    assert "opening_hours" in cols
    engine.dispose()

    path = tmp_path / "1_식당.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["식당id", "식당명", "영업시간"])
        w.writeheader()
        w.writerow({"식당id": "R1", "식당명": "빠른치킨", "영업시간": "17:00 ~ 03:00"})
        w.writerow({"식당id": "R2", "식당명": "느린치킨", "영업시간": ""})
    with db_sessions() as s:
        assert hours.sync_from_csv(s, path) == 1  # R1만 바뀜 (R2는 빈 값 = 원래도 없음)
        assert s.get(Restaurant, "R1").opening_hours == "17:00 ~ 03:00"
        assert s.get(Restaurant, "R2").opening_hours is None
        assert hours.sync_from_csv(s, path) == 0  # 다시 해도 바뀌는 것 없음


def test_csv_without_hours_column_changes_nothing(tmp_path, db_sessions):
    path = tmp_path / "1_식당.csv"
    path.write_text("식당id,식당명\nR1,빠른치킨\n", encoding="utf-8-sig")
    with db_sessions() as s:
        assert hours.sync_from_csv(s, path) == 0


def test_collector_writes_hours_column_and_keeps_unknown(tmp_path, monkeypatch):
    from scripts import collect_opening_hours as collector

    path = tmp_path / "1_식당.csv"
    path.write_text("식당id,식당명,리뷰수\nR1,빠른치킨,10\nR2,느린치킨,5\n", encoding="utf-8-sig")
    monkeypatch.setattr(collector, "DATA", path)
    found = [
        {"restaurant_id": "R1", "hours": "17:00 ~ 03:00"},
        {"restaurant_id": "R2", "hours": None},  # 이번에 못 읽음
    ]
    assert collector.write_csv(found) == 1
    assert hours.read_hours(path) == {"R1": "17:00 ~ 03:00", "R2": None}
    found[0]["hours"] = None  # 다음 수집에서 못 읽어도 기존 값은 남는다
    assert collector.write_csv(found) == 0
    assert hours.read_hours(path)["R1"] == "17:00 ~ 03:00"
