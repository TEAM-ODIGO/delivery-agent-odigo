"""E 전용 표 (접두어 e_). 모의 기관(FEP)의 거래 장부도 E 안에 둔다."""

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from e_payment.db import Base, JsonType, KstDateTime


class PaymentMethod(Base):
    """결제수단 원장 (E-11). 결제·환불이 끝나면 E가 직접 갱신한다."""

    __tablename__ = "e_payment_method"
    payment_method_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(20))  # CARD / LOCAL_CURRENCY
    name: Mapped[str] = mapped_column(String(100))
    balance_krw: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 지역화폐 잔액
    monthly_performance_krw: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 카드 당월 실적
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    updated_at: Mapped[datetime] = mapped_column(KstDateTime())


class Payment(Base):
    __tablename__ = "e_payment"
    payment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(64), unique=True)  # 멱등 키
    fingerprint: Mapped[str] = mapped_column(String(64))  # 요청 내용 해시 (같은 키 다른 내용 = 409)
    transaction_id: Mapped[str] = mapped_column(String(64), unique=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    approval_id: Mapped[str] = mapped_column(String(64))
    quote_id: Mapped[str] = mapped_column(String(64))
    payment_method_id: Mapped[str] = mapped_column(String(64))  # 분할이면 나머지를 낸 카드
    charge_krw: Mapped[int] = mapped_column(Integer)  # 전체 결제금액(분할이면 두 몫의 합)
    local_currency_payment_method_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 분할결제
    local_currency_charge_krw: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 분할결제: 지역화폐 몫
    refunded_krw: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20))  # PENDING / APPROVED / FAILED / UNKNOWN
    approved_at: Mapped[datetime | None] = mapped_column(KstDateTime(), nullable=True)
    reason_codes: Mapped[list] = mapped_column(JsonType, default=list)
    created_at: Mapped[datetime] = mapped_column(KstDateTime())
    updated_at: Mapped[datetime] = mapped_column(KstDateTime())


class Refund(Base):
    __tablename__ = "e_refund"
    __table_args__ = (UniqueConstraint("payment_id", "idempotency_key"),)
    refund_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payment_id: Mapped[str] = mapped_column(ForeignKey("e_payment.payment_id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64))
    refund_transaction_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20))  # PENDING / REFUNDED / FAILED / UNKNOWN
    amount_krw: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(KstDateTime())


class FepTransaction(Base):
    """모의 기관(FEP)이 가진 거래 장부. E-07·E-08·E-09가 읽고 쓴다."""

    __tablename__ = "e_fep_transaction"
    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # PAY / REFUND
    status: Mapped[str] = mapped_column(String(20))  # APPROVED / DECLINED / REFUNDED
    approval_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount_krw: Mapped[int] = mapped_column(Integer)
    original_transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(KstDateTime())


class Reconciliation(Base):
    __tablename__ = "e_reconciliation"
    reconciliation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    transaction_ids: Mapped[list] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(String(20))  # QUEUED / DONE
    created_at: Mapped[datetime] = mapped_column(KstDateTime())


class MyDataConsent(Base):
    """모의 마이데이터 정보제공 동의 (로컬 확장, 팀 합의 전). 진짜 마이데이터 연동이 아니다."""

    __tablename__ = "e_mydata_consent"
    consent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    scopes: Mapped[str] = mapped_column(String(200))  # 쉼표로 이은 동의 범위
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(KstDateTime())
    expires_at: Mapped[datetime] = mapped_column(KstDateTime())


class CardRegistration(Base):
    """사용자가 직접 추가한(직접 입력·모의 마이데이터) 카드의 등록 기록 (로컬 확장, 팀 합의 전).
    결제수단 원장(e_payment_method)은 그대로 두고, 어떻게 추가됐는지만 따로 적어 둔다.
    카드 번호는 받지도 저장하지도 않는다 (끝 4자리만)."""

    __tablename__ = "e_card_registration"
    payment_method_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(20))  # MANUAL / MYDATA
    external_ref: Mapped[str] = mapped_column(String(160))  # 같은 카드를 두 번 넣지 못하게 하는 키
    issuer: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    consent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(KstDateTime())

    __table_args__ = (UniqueConstraint("user_id", "external_ref", name="uq_card_registration_user_ref"),)
