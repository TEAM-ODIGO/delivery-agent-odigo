"""B(주문) 모듈 전용 테이블. 접두어 b_ (다른 모듈은 읽거나 쓰지 않는다).

    b_order_plan          주문안 (현재 버전·현재 견적·현재 혜택·현재 승인 대상을 가리킴)
    b_quote               견적 (주문 버전마다 새로 만든다. 메뉴별 단가·수량·배달비·만료)
    b_benefit_binding     C-05 혜택 결과를 견적에 연결한 기록 (benefit_revision 1, 2, …)
    b_approval_target     D-02로 보낼 승인 대상 (견적 상세 전부)
    b_execution_check     B-07 실행 전 확인 기록
    b_payment_event       B-08 결제 결과 (event_id 단위)
    b_idempotency         멱등 키별 첫 응답 (같은 키 + 같은 내용 → 같은 응답)
"""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JsonType, KstDateTime


class OrderPlan(Base):
    __tablename__ = "b_order_plan"

    order_plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)  # O 발급
    # O가 인증한 사용자 (C-05에 필요). 명세 B-01 요청에는 없어 X-User-ID 헤더로 받는다 (README 참고)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    candidate_id: Mapped[str] = mapped_column(String(64))  # A 발급
    restaurant_id: Mapped[str] = mapped_column(String(64))
    policy_id: Mapped[str] = mapped_column(String(64))  # D-01 발급
    constraints_version: Mapped[int] = mapped_column(Integer)
    constraints: Mapped[dict] = mapped_column(JsonType)  # A의 constraints 그대로
    budget_basis: Mapped[str] = mapped_column(String(20))
    requested_catalog_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))  # PlanStatus
    order_version: Mapped[int] = mapped_column(Integer)
    current_quote_id: Mapped[str] = mapped_column(String(64))
    current_benefit_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_approval_target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # E 기준 이름
    created_at: Mapped[datetime] = mapped_column(KstDateTime)
    updated_at: Mapped[datetime] = mapped_column(KstDateTime)


class Quote(Base):
    __tablename__ = "b_quote"

    quote_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_plan_id: Mapped[str] = mapped_column(ForeignKey("b_order_plan.order_plan_id"), index=True)
    order_version: Mapped[int] = mapped_column(Integer)
    restaurant_id: Mapped[str] = mapped_column(String(64))
    catalog_version: Mapped[str] = mapped_column(String(64))  # 가격을 읽은 수집 판본
    # [{"menu_id", "name", "description", "quantity", "unit_price_krw", "options": [...]}]
    items: Mapped[list] = mapped_column(JsonType)
    menu_subtotal_krw: Mapped[int] = mapped_column(Integer)
    delivery_fee_krw: Mapped[int] = mapped_column(Integer)
    pre_financial_amount_krw: Mapped[int] = mapped_column(Integer)
    local_currency_merchant: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # 견적 시점 값
    expires_at: Mapped[datetime] = mapped_column(KstDateTime)
    created_at: Mapped[datetime] = mapped_column(KstDateTime)


class BenefitBinding(Base):
    __tablename__ = "b_benefit_binding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_plan_id: Mapped[str] = mapped_column(ForeignKey("b_order_plan.order_plan_id"), index=True)
    order_version: Mapped[int] = mapped_column(Integer)
    quote_id: Mapped[str] = mapped_column(String(64))
    benefit_revision: Mapped[int] = mapped_column(Integer)
    recommendation_id: Mapped[str] = mapped_column(String(64))  # C-01 발급
    payment_method_id: Mapped[str] = mapped_column(String(64))
    benefit_rule_version: Mapped[str] = mapped_column(String(64))
    instant_discount_krw: Mapped[int] = mapped_column(Integer)
    charge_krw: Mapped[int] = mapped_column(Integer)
    applied_benefits: Mapped[list] = mapped_column(JsonType)
    # 분할결제(로컬 확장, 팀 합의 전): 지역화폐로 낸 몫. 일반 결제면 모두 NULL
    local_currency_payment_method_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    local_currency_amount_krw: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 혜택 전 몫
    local_currency_charge_krw: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 혜택 후 실제 차감액
    created_at: Mapped[datetime] = mapped_column(KstDateTime)


class ApprovalTarget(Base):
    __tablename__ = "b_approval_target"

    approval_target_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_plan_id: Mapped[str] = mapped_column(ForeignKey("b_order_plan.order_plan_id"), index=True)
    request_id: Mapped[str] = mapped_column(String(64))
    order_version: Mapped[int] = mapped_column(Integer)
    constraints_version: Mapped[int] = mapped_column(Integer)
    benefit_revision: Mapped[int] = mapped_column(Integer)
    quote_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))  # TargetStatus
    quote: Mapped[dict] = mapped_column(JsonType)  # D-02 요청의 quote에 그대로 들어가는 값
    created_at: Mapped[datetime] = mapped_column(KstDateTime)


class ExecutionCheck(Base):
    __tablename__ = "b_execution_check"

    execution_check_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_plan_id: Mapped[str] = mapped_column(ForeignKey("b_order_plan.order_plan_id"), index=True)
    approval_target_id: Mapped[str] = mapped_column(String(64))
    approval_id: Mapped[str] = mapped_column(String(64))
    execution_id: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(20))
    ready: Mapped[bool] = mapped_column(Boolean)
    reasons: Mapped[list] = mapped_column(JsonType)
    execution_input: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(KstDateTime)


class PaymentEvent(Base):
    __tablename__ = "b_payment_event"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_plan_id: Mapped[str] = mapped_column(ForeignKey("b_order_plan.order_plan_id"), index=True)
    approval_target_id: Mapped[str] = mapped_column(String(64))
    quote_id: Mapped[str] = mapped_column(String(64))
    execution_id: Mapped[str] = mapped_column(String(64))
    payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # PaymentStatus
    observed_at: Mapped[datetime] = mapped_column(KstDateTime)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    application_status: Mapped[str] = mapped_column(String(30))  # ApplicationStatus
    applied_to_current_quote: Mapped[bool] = mapped_column(Boolean)
    request_hash: Mapped[str] = mapped_column(String(80))  # 같은 event_id에 다른 내용이 왔는지 비교
    received_at: Mapped[datetime] = mapped_column(KstDateTime)


class IdempotencyRecord(Base):
    __tablename__ = "b_idempotency"

    idempotency_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    operation: Mapped[str] = mapped_column(String(10))  # B-01, B-05 …
    request_hash: Mapped[str] = mapped_column(String(80))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(JsonType)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(KstDateTime)
