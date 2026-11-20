"""수집 데이터(CSV)의 식당 기본 배달비·최소 주문 금액.

후보의 배달비는 주문 금액 구간에 따른 값이라, 요기요 화면(빈 주문표)의 '배달요금'과 비교하려면
기본 배달비가 필요하다. A 모듈의 테이블은 읽지 않고 팀 공용 CSV를 직접 읽는다.
"""

import csv
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[3] / "data" / "1_식당.csv"


def _int(v: str | None) -> int | None:
    return int(v) if v and v.strip().isdigit() else None


@lru_cache(maxsize=1)
def _rows() -> dict[str, dict]:
    if not DATA.exists():
        return {}
    with open(DATA, encoding="utf-8-sig") as f:
        return {
            r["식당id"]: {
                "base_delivery_fee_krw": _int(r.get("기본배달비")),
                "min_order_krw": _int(r.get("최소주문금액")),
            }
            for r in csv.DictReader(f)
        }


def restaurant_info(restaurant_id: str) -> dict:
    return _rows().get(str(restaurant_id), {})
