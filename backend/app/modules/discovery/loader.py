"""요기요 수집 CSV → restaurant·menu 테이블 (A·B·C가 함께 읽는 공용 읽기 전용 데이터).

- 서버가 시작될 때 restaurant 테이블이 비어 있으면 data/ 의 CSV를 한 번 적재한다 (load_if_empty).
- 이미 들어 있어도 CSV의 수집시각이 DB보다 새로우면 CSV에 맞춘다
  (sync_if_newer: 재수집한 뒤 서버를 다시 켤 때).
- 직접 다시 적재하려면: uv run python -m app.modules.discovery.loader
- 같은 ID는 덮어쓴다(다시 돌려도 안전). SQLite·PostgreSQL 모두 동작.
- MVP 1 지역화폐 규칙: 요기요 '지원금 현장결제 가능' 값을 local_currency_merchant에 추정값으로
  넣고 basis = ASSUMED_FROM_VOUCHER (원본 A 프로젝트와 같다).
"""

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.discovery.catalog_models import Menu, Restaurant
from app.modules.discovery.timeutil import now_kst

log = logging.getLogger("app")

DATA_DIR = Path(os.getenv("CATALOG_DATA_DIR", Path(__file__).resolve().parents[3] / "data"))
RESTAURANT_CSV = "1_식당.csv"
MENU_CSV = "2_메뉴.csv"
BATCH = 1000


def _bool(v: str) -> bool | None:
    return True if v == "t" else False if v == "f" else None


def _int(v) -> int | None:
    return None if v in (None, "") else int(float(v))


def _float(v) -> float | None:
    return None if v in (None, "") else float(v)


def _list(v: str) -> list[str]:
    return [p.strip() for p in v.split(",") if p.strip()] if v else []


def _minutes(v: str) -> tuple[int | None, int | None]:
    if not v or "~" not in v:
        return None, None
    lo, hi = v.split("~", 1)
    return int(lo), int(hi)


def _collected_at(v: str) -> datetime | None:
    if not v:
        return None
    if v[-3] in "+-":
        v += ":00"
    return datetime.fromisoformat(v)


def read_restaurants(path: Path) -> list[dict]:
    rows, checked = [], now_kst()
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            lo, hi = _minutes(r["예상배달분"])
            voucher = _bool(r["지역화폐가능"]) is True
            rows.append(
                {
                    "restaurant_id": r["식당id"],
                    "name": r["식당명"],
                    "collected_categories": _list(r["수집카테고리"]),
                    "yogiyo_categories": _list(r["요기요카테고리"]),
                    "rating": _float(r["별점"]) or 0.0,
                    "review_count": _int(r["리뷰수"]) or 0,
                    "adjusted_rating": _float(r["보정별점"]) or 0.0,
                    "min_order_krw": _int(r["최소주문금액"]) or 0,
                    "base_delivery_fee_krw": _int(r["기본배달비"]) or 0,
                    "delivery_fee_tiers": json.loads(r["주문금액별배달비"])
                    if r["주문금액별배달비"]
                    else [],
                    "delivery_minutes_min": lo,
                    "delivery_minutes_max": hi,
                    "distance_m": _int(r["거리m"]),
                    "accepts_livelihood_voucher": voucher,
                    "local_currency_merchant": voucher,
                    "local_currency_basis": "ASSUMED_FROM_VOUCHER",
                    "local_currency_checked_at": checked,
                    "menu_count": _int(r["메뉴수"]) or 0,
                    "address": r["주소"],
                    "yogiyo_url": r["요기요링크"],
                    "collected_at": _collected_at(r["수집시각"]),
                    "opening_hours": (r.get("영업시간") or "").strip() or None,
                }
            )
    return rows


def read_menus(path: Path, restaurant_ids: set[str]) -> tuple[list[dict], int]:
    rows, skipped = [], 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r["식당id"] not in restaurant_ids:
                skipped += 1
                continue
            rows.append(
                {
                    "menu_id": r["메뉴id"],
                    "restaurant_id": r["식당id"],
                    "menu_category": r["메뉴분류"],
                    "name": r["메뉴명"],
                    "description": r["설명"] or None,
                    "list_price_krw": _int(r["정가"]) or 0,
                    "sale_price_krw": _int(r["판매가"]) or 0,
                    "price_with_required_options_krw": _int(r["필수옵션포함가"]) or 0,
                    "has_required_options": _bool(r["필수옵션있음"]) is True,
                    "menu_type": r["유형"],
                    "spice_level": _int(r["맵기"]),
                    "has_soup": _bool(r["국물"]),
                    "servings": _float(r["인분"]),
                    "is_signature": _bool(r["대표메뉴"]) is True,
                    "is_popular": _bool(r["인기"]) is True,
                    "is_sold_out": _bool(r["품절"]) is True,
                    "is_alcohol": _bool(r["주류"]) is True,
                    "review_count": _int(r["메뉴리뷰수"]) or 0,
                }
            )
    return rows, skipped


def load_csv(session: Session, restaurants_csv: Path, menus_csv: Path) -> tuple[int, int]:
    restaurants = read_restaurants(restaurants_csv)
    menus, _ = read_menus(menus_csv, {r["restaurant_id"] for r in restaurants})
    for start in range(0, len(restaurants), BATCH):
        for row in restaurants[start : start + BATCH]:
            session.merge(Restaurant(**row))
    session.flush()
    for start in range(0, len(menus), BATCH):
        for row in menus[start : start + BATCH]:
            session.merge(Menu(**row))
    session.commit()
    return len(restaurants), len(menus)


def load_if_empty(session: Session) -> tuple[int, int] | None:
    """restaurant 테이블이 비어 있고 CSV가 있으면 적재한다. 이미 있으면 아무것도 하지 않는다."""
    if session.scalar(select(func.count()).select_from(Restaurant)):
        return None
    r, m = _find_csv(RESTAURANT_CSV), _find_csv(MENU_CSV)
    if r is None or m is None:
        # 조용히 넘어가면 '후보 없음'으로만 보여 원인을 찾기 어렵다 → 로그에 남긴다
        log.warning("식당·메뉴 CSV를 찾지 못해 적재하지 않았습니다: %s (%s, %s 필요)", DATA_DIR, RESTAURANT_CSV, MENU_CSV)
        return None
    return load_csv(session, r, m)


# C-08만 쓰는 열: 다시 맞출 때 건드리지 않는다
_KEEP_ON_SYNC = {"local_currency_merchant", "local_currency_basis", "local_currency_checked_at"}


def sync_if_newer(session: Session) -> tuple[int, int, int] | None:
    """CSV가 DB보다 새로 수집됐으면(수집시각 기준) DB를 CSV에 맞춘다.

    돌려주는 값: (식당 수, 메뉴 수, 품절 처리한 메뉴 수). 새롭지 않으면 None.

    scripts/recollect_catalog.py --write 로 CSV를 고친 뒤 서버를 다시 켜면 여기서 반영된다.
    - 같은 ID는 값을 고치고, 새 ID는 넣는다.
    - CSV에 있는 식당의 메뉴인데 CSV에서 빠진 것(메뉴 번호가 바뀐 옛 줄)은 지우지 않고 품절로 둔다.
    """
    r, m = _find_csv(RESTAURANT_CSV), _find_csv(MENU_CSV)
    if r is None or m is None:
        return None
    restaurants = read_restaurants(r)
    csv_latest = max((x["collected_at"] for x in restaurants if x["collected_at"]), default=None)
    db_latest = session.scalar(select(func.max(Restaurant.collected_at)))
    if csv_latest is None or db_latest is None or csv_latest <= db_latest:
        return None
    existing = {x.restaurant_id: x for x in session.scalars(select(Restaurant))}
    for row in restaurants:
        obj = existing.get(row["restaurant_id"])
        if obj is None:
            session.add(Restaurant(**row))
            continue
        for key, value in row.items():
            if key not in _KEEP_ON_SYNC:
                setattr(obj, key, value)
    session.flush()
    ids = {x["restaurant_id"] for x in restaurants}
    menus, _ = read_menus(m, ids)
    in_csv = select(Menu).where(Menu.restaurant_id.in_(ids))
    db_menus = {x.menu_id: x for x in session.scalars(in_csv)}
    for row in menus:
        obj = db_menus.pop(row["menu_id"], None)
        if obj is None:
            session.add(Menu(**row))
        else:
            for key, value in row.items():
                setattr(obj, key, value)
    gone = 0
    for obj in db_menus.values():  # CSV에서 빠진 메뉴
        if not obj.is_sold_out:
            obj.is_sold_out = True
            gone += 1
    session.commit()
    log.info(
        "식당·메뉴를 새 CSV(%s 수집)에 맞췄습니다: 식당 %d곳, 메뉴 %d개, 빠진 메뉴 품절 %d개",
        csv_latest, len(restaurants), len(menus), gone,
    )
    return len(restaurants), len(menus), gone


def find_restaurant_csv() -> Path | None:
    return _find_csv(RESTAURANT_CSV)


def _find_csv(name: str) -> Path | None:
    """data/ 에서 CSV를 찾는다. 압축을 풀다가 한글 파일명이 %ec%8b%9d… 처럼 깨진 경우도 알아본다."""
    exact = DATA_DIR / name
    if exact.exists():
        return exact
    if DATA_DIR.is_dir():
        for path in DATA_DIR.iterdir():
            if unquote(path.name) == name:
                return path
    return None


if __name__ == "__main__":
    from app.core.db import SessionLocal, init_db

    init_db()
    with SessionLocal() as s:
        print("적재:", load_csv(s, _find_csv(RESTAURANT_CSV) or DATA_DIR / RESTAURANT_CSV, _find_csv(MENU_CSV) or DATA_DIR / MENU_CSV))
