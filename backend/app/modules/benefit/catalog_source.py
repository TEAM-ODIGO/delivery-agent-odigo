"""식당 지역화폐 정보를 팀 공용 식당 데이터(요기요 수집 DB 대역, app/fakes/catalog.py)에서 읽는다.

명세: A·C는 같은 식당 데이터를 직접 읽는다. 진짜 수집 DB가 생기면 이 파일의
`restaurant_rows()`만 DB 조회로 바꾸면 된다. C-08만 local_currency_* 세 열을 쓴다.
"""
from . import seed
from .domain import Restaurant


def restaurant_rows():
    from app.fakes import catalog  # 수집 DB가 생기면 이 한 줄을 DB 조회로 교체

    return catalog.RESTAURANTS


def use_shared_restaurants() -> None:
    rows = {}
    for r in restaurant_rows():
        basis = "ASSUMED_FROM_VOUCHER" if r.local_currency_merchant is not None else None
        rows[r.restaurant_id] = Restaurant(
            r.restaurant_id, r.accepts_livelihood_voucher, r.local_currency_merchant, basis, None, name=r.name)
    seed.RESTAURANTS.clear()
    seed.RESTAURANTS.update(rows)
    seed._INITIAL["restaurants"] = {k: v for k, v in rows.items()}


def use_db_restaurants(session_factory) -> int:
    """진짜 수집 데이터(restaurant 테이블, A·B와 같은 데이터)에서 지역화폐 정보를 읽는다."""
    from sqlalchemy import select

    from app.modules.discovery.catalog_models import Restaurant as Row

    rows = {}
    with session_factory() as db:
        for r in db.scalars(select(Row)):
            rows[r.restaurant_id] = Restaurant(
                r.restaurant_id,
                r.accepts_livelihood_voucher,
                r.local_currency_merchant,
                r.local_currency_basis,
                None,
                name=r.name,
            )
    seed.RESTAURANTS.clear()
    seed.RESTAURANTS.update(rows)
    seed._INITIAL["restaurants"] = {k: v for k, v in rows.items()}
    return len(rows)
