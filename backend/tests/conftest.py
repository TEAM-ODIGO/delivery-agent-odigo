"""테스트 공용 준비물 (pytest fixture).

- 테스트마다 새 SQLite 파일 DB를 만든다 → 테스트끼리 영향을 주지 않는다.
- FakeClock: 시간을 마음대로 앞으로 돌릴 수 있는 가짜 시계 (만료 테스트용).
- make_*: 정상 값으로 채운 요청을 만들고, 바꾸고 싶은 부분만 덮어쓴다.
"""

import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.clock import KST
from app.core.db import Base, init_db, make_engine
from app.modules.delegation.benefits import StaticBenefitPort
from app.modules.delegation.schemas import (
    ConfirmRequest,
    OrderValidationRequest,
    PolicyCreate,
)
from app.modules.delegation.service import DelegationService

T0 = datetime(2026, 11, 15, 12, 0, tzinfo=KST)


class FakeClock:
    def __init__(self, start: datetime = T0):
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current += timedelta(**kwargs)


@pytest.fixture
def engine(tmp_path):
    """기본은 테스트마다 새 SQLite 파일.
    TEST_DATABASE_URL을 주면 PostgreSQL로 같은 테스트를 돌린다 (테이블을 매번 새로 만듦).
      TEST_DATABASE_URL=postgresql+psycopg://user@host:5432/db uv run pytest
    """
    url = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    eng = make_engine(url)
    Base.metadata.drop_all(eng)
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session(session_factory):
    with session_factory() as s:
        yield s


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def benefits():
    return StaticBenefitPort()


@pytest.fixture
def svc(session, benefits, clock):
    return DelegationService(session, benefits, now=clock)


# --- 요청 만들기 ---------------------------------------------------------------------
def make_policy(**over) -> PolicyCreate:
    data = {
        "request_id": "REQ001",
        "user_id": "U001",
        "original_message": "3명이 먹을 치킨을 배달비 포함 4만원 이하로 주문해줘",
        "constraints_confirmed": True,
        "constraints": {"budget_krw": 40000, "people": 3, "cuisine": "치킨"},
        "delegation": {
            "allowed_actions": ["SEARCH", "BUILD_ORDER", "REQUEST_PAYMENT"],
            "allowed_payment_method_ids": ["CARD001", "LOCAL001"],
            "max_charge_krw": 40000,
        },
    }
    data.update(over)
    return PolicyCreate.model_validate(data)


def make_quote(**over) -> dict:
    """정상 견적: (30,000 + 옵션 2,000) × 1 + 배달비 3,000 - 카드할인 2,000 = 33,000원"""
    quote = {
        "quote_id": "QT001",
        "restaurant_id": "STORE101",
        "items": [
            {
                "menu_id": "MENU001",
                "name": "후라이드치킨",
                "description": "바삭한 국내산 닭",
                "quantity": 1,
                "unit_price_krw": 30000,
                "options": [{"option_id": "OPT001", "price_krw": 2000}],
            }
        ],
        "menu_subtotal_krw": 32000,
        "delivery_fee_krw": 3000,
        "payment_method_id": "CARD001",
        "instant_discount_krw": 2000,
        "charge_krw": 33000,
        "catalog_version": "CAT-V1",
        "benefit_rule_version": "BEN-V1",
        "expires_at": (T0 + timedelta(minutes=30)).isoformat(),
        "unverified_conditions": [],
    }
    quote.update(over)
    return quote


def make_validation(policy_out, quote: dict | None = None, **over) -> OrderValidationRequest:
    data = {
        "request_id": policy_out.request_id,
        "user_id": "U001",
        "policy_id": policy_out.policy_id,
        "policy_version": policy_out.policy_version,
        "approval_target_id": "target_001",
        "order_version": 1,
        "quote": quote or make_quote(),
    }
    data.update(over)
    return OrderValidationRequest.model_validate(data)


def approve(
    svc: DelegationService, policy_overrides: dict | None = None, quote: dict | None = None
):
    """정상 흐름을 '승인 완료'까지 진행하고 (검증 결과, 승인 결과)를 돌려준다."""
    policy = svc.register_policy(make_policy(**(policy_overrides or {})))
    validated = svc.validate_order(make_validation(policy, quote))
    assert validated.valid, validated.reason_codes
    confirmed = svc.confirm(
        validated.approval_id, ConfirmRequest(user_id="U001", proposal_hash=validated.proposal_hash)
    )
    return validated, confirmed
