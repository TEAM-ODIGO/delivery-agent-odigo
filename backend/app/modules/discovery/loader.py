"""요기요 수집 CSV → restaurant·menu 테이블 (A·B·C가 함께 읽는 공용 읽기 전용 데이터).

- 서버가 시작될 때 restaurant 테이블이 비어 있으면 data/ 의 CSV를 한 번 적재한다 (load_if_empty).
- 직접 다시 적재하려면: uv run python -m app.modules.discovery.loader
- 같은 ID는 덮어쓴다(다시 돌려도 안전). SQLite·PostgreSQL 모두 동작.
- MVP 1 지역화폐 규칙: 요기요 '지원금 현장결제 가능' 값을 local_currency_merchant에 추정값으로
  넣고 basis = ASSUMED_FROM_VOUCHER (원본 A 프로젝트와 같다).
"""

import csv
import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.discovery.catalog_models import Menu, Restaurant
from app.modules.discovery.timeutil import now_kst

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
    r, m = DATA_DIR / RESTAURANT_CSV, DATA_DIR / MENU_CSV
    if not (r.exists() and m.exists()):
        return None
    return load_csv(session, r, m)


if __name__ == "__main__":
    from app.core.db import SessionLocal, init_db

    init_db()
    with SessionLocal() as s:
        print("적재:", load_csv(s, DATA_DIR / RESTAURANT_CSV, DATA_DIR / MENU_CSV))
