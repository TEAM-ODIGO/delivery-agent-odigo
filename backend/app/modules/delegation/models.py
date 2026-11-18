"""D 모듈의 DB 테이블 4개 (조건·승인·실행·감사 로그).

다른 모듈은 이 파일을 import하지 않는다. D의 데이터는 service.py를 통해서만 바뀐다.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JsonType, KstDateTime


class Policy(Base):
    """사용자 조건 + Agent에게 맡긴 범위. 바뀔 때마다 새 버전 행이 생긴다."""

    __tablename__ = "delegation_policy"
    __table_args__ = (UniqueConstraint("request_id", "version"),)

    policy_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # ACTIVE / SUPERSEDED
    original_message: Mapped[str] = mapped_column(Text)
    constraints_confirmed: Mapped[bool] = mapped_column(Boolean)
    constraints: Mapped[dict] = mapped_column(JsonType)
    delegation: Mapped[dict] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(KstDateTime)


class Approval(Base):
    """검증을 통과한 주문안의 스냅샷 + 승인 상태.

    사용자가 승인하는 대상은 '이 행의 proposal_hash'다. 해시가 다르면 다른 주문이다.
    """

    __tablename__ = "delegation_approval"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    policy_id: Mapped[str] = mapped_column(String(64), ForeignKey("delegation_policy.policy_id"))
    policy_version: Mapped[int] = mapped_column(Integer)
    approval_target_id: Mapped[str] = mapped_column(String(64))
    order_version: Mapped[int] = mapped_column(Integer)
    quote_id: Mapped[str] = mapped_column(String(64))
    proposal_hash: Mapped[str] = mapped_column(String(80))
    proposal_snapshot: Mapped[dict] = mapped_column(JsonType)
    charge_krw: Mapped[int] = mapped_column(BigInteger)
    payment_method_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    revoke_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    execution_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(KstDateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(KstDateTime)
    created_at: Mapped[datetime] = mapped_column(KstDateTime)


class Execution(Base):
    """결제 실행 권한 선점 기록. approval_id가 UNIQUE라서 승인 1건당 딱 한 행만 생긴다."""

    __tablename__ = "delegation_execution"

    execution_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    approval_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("delegation_approval.approval_id"), unique=True
    )
    proposal_hash: Mapped[str] = mapped_column(String(80))
    charge_krw: Mapped[int] = mapped_column(BigInteger)
    payment_method_id: Mapped[str] = mapped_column(String(64))
    claimed_at: Mapped[datetime] = mapped_column(KstDateTime)
    # D-08: O가 전달한 E 결제 결과 (최종 기준은 E 원장, 여기는 D의 실행 기록)
    payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    result_charge_krw: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    result_recorded_at: Mapped[datetime | None] = mapped_column(KstDateTime, nullable=True)


class AuditLog(Base):
    """모든 판단의 기록. 추가만 하고 수정·삭제하지 않는다.

    prev_hash(바로 앞 행의 hash)를 품고 있어 행들이 사슬처럼 이어진다.
    중간 행을 고치거나 지우면 사슬이 끊겨 audit.verify_chain()이 찾아낸다.
    prev_hash가 UNIQUE라서 두 요청이 동시에 같은 행 뒤에 붙으려 하면 하나는 실패한다.
    """

    __tablename__ = "delegation_audit_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    log_id: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(KstDateTime)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    approval_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    execution_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(16))
    result: Mapped[str] = mapped_column(String(16))
    reason_codes: Mapped[list] = mapped_column(JsonType)
    details: Mapped[dict] = mapped_column(JsonType)
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hash: Mapped[str] = mapped_column(String(64))
