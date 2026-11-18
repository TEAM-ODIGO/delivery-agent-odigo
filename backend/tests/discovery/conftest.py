"""A 테스트 준비물. 합친 서버의 SQLite(파일 임시 DB)에 결과를 예상할 수 있는 작은 데이터(가게 4곳)를 넣는다.

원본 A 프로젝트는 PostgreSQL 전용 테스트였다. 여기서는 SQLite로 같은 테스트를 돌리고,
TEST_DATABASE_URL을 주면 PostgreSQL로도 돌릴 수 있다.
"""

import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

os.environ["LLM_ENABLED"] = "false"
os.environ["LLM_EXPLAIN_ENABLED"] = "false"

from app.core.config import settings as core_settings  # noqa: E402
from app.core.db import Base, get_session, init_db, make_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.discovery import security  # noqa: E402
from app.modules.discovery.catalog_models import Menu, Restaurant  # noqa: E402
from app.modules.discovery.settings import get_settings  # noqa: E402
from app.modules.discovery.timeutil import now_kst  # noqa: E402

ADDRESS = {"region_code": "11140", "lat": 37.566, "lng": 126.9826}
HEADERS = {"Authorization": "Bearer test-token"}


def restaurant(rid, name, categories, adjusted_rating, minutes, voucher, min_order=15000, fee=2000):
    return Restaurant(
        restaurant_id=rid, name=name, collected_categories=["chicken"], yogiyo_categories=categories,
        rating=round(adjusted_rating, 1), review_count=500, adjusted_rating=adjusted_rating,
        min_order_krw=min_order, base_delivery_fee_krw=fee,
        delivery_fee_tiers=[{"order_amount": min_order, "delivery_fee": fee}],
        delivery_minutes_min=minutes - 15, delivery_minutes_max=minutes, distance_m=500,
        accepts_livelihood_voucher=voucher, local_currency_merchant=voucher,
        local_currency_basis="ASSUMED_FROM_VOUCHER", local_currency_checked_at=now_kst(),
        menu_count=3, address="서울특별시 중구", yogiyo_url="https://www.yogiyo.co.kr/",
        collected_at=now_kst(),
    )


def menu(mid, rid, name, price, menu_type="main", spice=None, popular=False, description=None):
    return Menu(
        menu_id=mid, restaurant_id=rid, menu_category="메인", name=name, description=description,
        list_price_krw=price, sale_price_krw=price, price_with_required_options_krw=price,
        has_required_options=False, menu_type=menu_type, spice_level=spice, has_soup=None,
        servings=None, is_signature=False, is_popular=popular, is_sold_out=False,
        is_alcohol=menu_type == "alcohol", review_count=10,
    )


def seed(session) -> None:
    session.add_all([
        restaurant("R1", "빠른치킨-을지로점", ["치킨", "야식"], 4.9, 35, True),
        restaurant("R2", "느린치킨-종로점", ["치킨"], 4.95, 58, True),
        restaurant("R3", "현금치킨-명동점", ["치킨"], 4.8, 30, False),
        restaurant("R4", "테스트피자-시청점", ["피자/양식"], 4.7, 30, True),
    ])
    session.flush()
    session.add_all([
        menu("M11", "R1", "후라이드치킨", 18000, spice=0, popular=True),
        menu("M12", "R1", "매운양념치킨", 19000, spice=2),
        menu("M13", "R1", "생맥주", 4000, menu_type="alcohol"),
        menu("M21", "R2", "후라이드치킨", 16000, spice=0),
        menu("M31", "R3", "땅콩 후라이드치킨", 17000, spice=0, description="땅콩 소스"),
        menu("M32", "R3", "간장치킨", 17500, spice=0),
        menu("M41", "R4", "치즈피자", 22000, spice=0),
    ])
    session.commit()


@pytest.fixture
def db_sessions(tmp_path):
    url = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'a.db'}"
    engine = make_engine(url)
    Base.metadata.drop_all(engine)
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as s:
        seed(s)
    yield sessions
    engine.dispose()


@pytest.fixture(autouse=True)
def _settings(monkeypatch, db_sessions):
    get_settings.cache_clear()
    monkeypatch.setattr(security, "settings", replace(core_settings, service_token="test-token"))

    def override():
        with db_sessions() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield
    app.dependency_overrides.pop(get_session, None)
    get_settings.cache_clear()


@pytest.fixture
def client():
    return TestClient(app)  # with 없이: 서버 시작 처리(lifespan)를 돌리지 않는다


@pytest.fixture
def create(client):
    def _create(request_id: str, message: str, address: dict | None = None):
        return client.post(
            "/discovery/requests",
            headers={**HEADERS, "X-Request-ID": request_id},
            json={"request_id": request_id, "user_id": "U001", "message": message,
                  "delivery_address": address or ADDRESS},
        )

    return _create
