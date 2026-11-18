"""B 테스트 준비물.

- DB: 기본은 테스트마다 새 SQLite 파일, TEST_DATABASE_URL을 주면 PostgreSQL
- 수집 데이터: 결과를 예상할 수 있는 작은 가짜 데이터 (가게 2곳)
- C-05·D-04: 가짜 구현을 끼운다 (ports.configure). 실패·불일치 상황을 테스트마다 바꿀 수 있다
- 시계: 테스트가 직접 시간을 넘길 수 있다 (견적·승인 만료 확인용)
"""

import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.modules.discovery.catalog_models import Menu, Restaurant
from app.core.clock import KST
from app.core.db import Base, get_session, init_db, make_engine
from app.main import app
from app.modules.ordering import ports
from app.modules.ordering.config import get_settings
from app.modules.ordering.router import get_service
from app.modules.ordering.service import OrderingService

TOKEN = "test-token"
START = datetime(2026, 11, 17, 12, 0, tzinfo=KST)


class Clock:
    def __init__(self):
        self.value = START

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        self.value = self.value + timedelta(**kwargs)


class FakeBenefit:
    """C-05 흉내: CARD001은 2,000원 즉시할인, LOCAL001은 지역화폐 가맹점에서만 가능."""

    def __init__(self):
        self.mode = "ok"  # ok · unavailable · rejected · mismatch
        self.calls = []

    def calculate(self, body: dict) -> dict:
        self.calls.append(body)
        if self.mode == "unavailable":
            raise ports.DependencyUnavailable("C 꺼짐")
        if self.mode == "rejected":
            raise ports.DependencyRejected(404, "PAYMENT_METHOD_NOT_FOUND", "결제수단 없음")
        total = body["menu_subtotal_krw"] + body["delivery_fee_krw"]
        if body["payment_method_id"] == "LOCAL001" and body["restaurant_id"] != "R1":
            return {"payment_method_id": "LOCAL001", "eligible": False, "benefit_rule_version": "BEN-V1",
                    "instant_discount_krw": 0, "charge_krw": total, "applied_benefits": [],
                    "reason_codes": ["NOT_LOCAL_CURRENCY_MERCHANT"]}
        discount = 2000 if body["payment_method_id"] == "CARD001" else 0
        charge = total - discount
        if self.mode == "mismatch":
            charge = charge - 500  # 할인과 결제액이 안 맞는 결과
        return {"payment_method_id": body["payment_method_id"], "eligible": True,
                "benefit_rule_version": body.get("benefit_rule_version") or "BEN-V1",
                "instant_discount_krw": discount, "charge_krw": charge,
                "applied_benefits": [{"rule_id": "RULE-1", "type": "INSTANT_DISCOUNT", "amount_krw": discount}],
                "reason_codes": []}


class FakeApprovals:
    """D-04 흉내: 테스트가 승인 기록을 직접 넣는다."""

    def __init__(self):
        self.approvals = {}
        self.unavailable = False

    def get_approval(self, approval_id: str) -> dict:
        if self.unavailable:
            raise ports.DependencyUnavailable("D 꺼짐")
        if approval_id not in self.approvals:
            raise ports.DependencyRejected(404, "APPROVAL_NOT_FOUND", "승인 없음")
        return self.approvals[approval_id]


def restaurant(restaurant_id: str, local_currency: bool | None) -> Restaurant:
    return Restaurant(
        restaurant_id=restaurant_id, name=f"테스트치킨-{restaurant_id}",
        collected_categories=["chicken"], yogiyo_categories=["치킨"], rating=4.8, review_count=100,
        adjusted_rating=4.7, min_order_krw=15000, base_delivery_fee_krw=3000,
        delivery_fee_tiers=[{"order_amount": 15000, "delivery_fee": 3000}, {"order_amount": 30000, "delivery_fee": 1000}],
        delivery_minutes_min=20, delivery_minutes_max=35, distance_m=500,
        accepts_livelihood_voucher=bool(local_currency), local_currency_merchant=local_currency,
        local_currency_basis="ASSUMED_FROM_VOUCHER" if local_currency is not None else None,
        local_currency_checked_at=None, menu_count=3, address="서울 중구", yogiyo_url=None, collected_at=START,
    )


def menu(menu_id: str, restaurant_id: str, name: str, price: int, sold_out=False, alcohol=False) -> Menu:
    return Menu(
        menu_id=menu_id, restaurant_id=restaurant_id, menu_category="메인", name=name, description="국내산 닭",
        list_price_krw=price, sale_price_krw=price, price_with_required_options_krw=price,
        has_required_options=False, menu_type="alcohol" if alcohol else "main", spice_level=0, has_soup=None,
        servings=None, is_signature=False, is_popular=True, is_sold_out=sold_out, is_alcohol=alcohol,
        review_count=1,
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("ORDERING_QUOTE_TTL_SECONDS", "600")
    get_settings.cache_clear()

    url = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'b.db'}"
    engine = make_engine(url)
    Base.metadata.drop_all(engine)
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as s:
        s.add_all([restaurant("R1", True), restaurant("R2", None)])
        s.flush()
        s.add_all([
            menu("M1", "R1", "후라이드치킨", 18000),
            menu("M2", "R1", "양념치킨", 19000, sold_out=True),
            menu("M3", "R1", "생맥주", 4000, alcohol=True),
            menu("M4", "R1", "치킨무", 500),
            menu("M21", "R2", "간장치킨", 18000),
        ])
        s.commit()

    clock = Clock()
    benefit = FakeBenefit()
    approvals = FakeApprovals()

    def session_override():
        with sessions() as session:
            yield session

    def service_override():
        with sessions() as session:
            yield OrderingService(session, benefit=benefit, approvals=approvals, clock=clock)

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_service] = service_override
    client = TestClient(app)

    class Env:
        pass

    e = Env()
    e.client, e.clock, e.benefit, e.approvals, e.sessions = client, clock, benefit, approvals, sessions
    yield e

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    engine.dispose()
