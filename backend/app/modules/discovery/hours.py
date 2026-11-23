"""식당 영업시간 (이슈 ⑤): 지금 닫힌 식당을 A-05 후보에서 미리 뺀다.

- 영업시간은 요기요 식당 페이지의 open_time_description 값이다 ("17:00 ~ 03:00").
  scripts/collect_opening_hours.py --collect 로 모아 data/1_식당.csv의 '영업시간' 열에 넣는다.
- "00:00 ~ 00:00"은 24시간 영업, 닫는 시각이 여는 시각보다 이르면 자정을 넘겨 영업한다.
- 브레이크타임·휴무일은 요기요 데이터에 없어 여기서는 거르지 못한다 → 실시간 확인이 거른다.
- 영업시간을 모르는 식당은 열린 것으로 본다 (후보에 넣고 실시간 확인에 맡긴다).
- DISCOVERY_OPENING_HOURS_FILTER=0 이면 거르지 않는다 (시간과 상관없는 시연·테스트용).
"""

import csv
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.modules.discovery.catalog_models import Restaurant
from app.modules.discovery.timeutil import KST, now_kst

log = logging.getLogger("app")

_RANGE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*~\s*(\d{1,2}):(\d{2})\s*$")


def parse(hours: str | None) -> tuple[int, int] | None:
    """'17:00 ~ 03:00' → (1020, 180) 분 단위. 모르는 모양이면 None."""
    m = _RANGE.match(hours or "")
    if not m:
        return None
    oh, om, ch, cm = (int(g) for g in m.groups())
    if oh > 24 or ch > 24 or om > 59 or cm > 59:
        return None
    return (oh * 60 + om) % 1440, (ch * 60 + cm) % 1440


def is_open(hours: str | None, at: datetime) -> bool | None:
    """그 시각에 영업 중인가. 영업시간을 모르면 None."""
    window = parse(hours)
    if window is None:
        return None
    start, end = window
    t = at.astimezone(KST)
    minute = t.hour * 60 + t.minute
    if start == end:  # 00:00 ~ 00:00 → 24시간
        return True
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end  # 자정을 넘겨 영업


def filter_now() -> datetime | None:
    """A-05가 영업시간을 볼 기준 시각. 끄면(DISCOVERY_OPENING_HOURS_FILTER=0) None."""
    if (os.getenv("DISCOVERY_OPENING_HOURS_FILTER") or "1").strip().lower() in ("0", "false"):
        return None
    return now_kst()


# ---------------------------------------------------------------- DB 준비
def ensure_column(engine) -> None:
    """예전 restaurant 테이블에 opening_hours 열이 없으면 추가한다 (create_all은 열을 안 더함)."""
    insp = inspect(engine)
    if not insp.has_table("restaurant"):
        return
    if "opening_hours" in {c["name"] for c in insp.get_columns("restaurant")}:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE restaurant ADD COLUMN opening_hours VARCHAR(40)"))
    log.info("restaurant.opening_hours 열을 추가했습니다")


def read_hours(path: Path) -> dict[str, str | None]:
    """CSV의 '영업시간' 열. 열이 없으면 빈 dict."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "영업시간" not in (reader.fieldnames or []):
            return {}
        return {r["식당id"]: (r["영업시간"] or "").strip() or None for r in reader}


def sync_from_csv(session: Session, path: Path | None) -> int:
    """CSV의 영업시간을 DB에 맞춘다 (서버 시작 때). 바뀐 식당 수를 돌려준다."""
    if path is None:
        return 0
    hours = read_hours(path)
    if not hours:
        return 0
    changed = 0
    for r in session.scalars(select(Restaurant)):
        value = hours.get(r.restaurant_id, r.opening_hours)
        if r.opening_hours != value:
            r.opening_hours = value
            changed += 1
    session.commit()
    if changed:
        log.info("식당 영업시간 %d곳을 CSV에 맞췄습니다", changed)
    return changed
