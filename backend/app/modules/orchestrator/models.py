"""O 모듈의 DB 테이블 (설계 문서 03 7절).

- orchestrator_run: 주문 1건의 현재 상태.
  사용자 대기 중에도 DB에 있으므로 서버를 다시 켜도 이어진다.
- orchestrator_run_event: 상태 변경·사용자 응답·하위 호출·결제 결과 기록 (추가만 함).
- orchestrator_idempotency: O-01·O-02 멱등 키 (같은 키 = 처음 응답 그대로).

동시 제출 방어는 두 겹이다.
1) state_version: 화면이 보낸 expected_state_version과 다르면 409.
2) row_version: 같은 버전으로 두 요청이 동시에 들어와도 먼저 저장한 쪽만 성공
   (SQLAlchemy 낙관적 잠금, SQLite·PostgreSQL 모두 동작). PostgreSQL에서는 행 잠금도 건다.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JsonType, KstDateTime


class Run(Base):
    __tablename__ = "orchestrator_run"

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    message: Mapped[str] = mapped_column(Text)
    delegation: Mapped[dict[str, Any]] = mapped_column(JsonType)
    # 단계별 결과: constraints, policy_id, candidates, order_plan_id, quote, approval_id …
    context: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    prompt: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    notice: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    blocked: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    event_seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(KstDateTime)
    updated_at: Mapped[datetime] = mapped_column(KstDateTime)

    __mapper_args__ = {"version_id_col": row_version}


class RunEvent(Base):
    __tablename__ = "orchestrator_run_event"
    __table_args__ = (UniqueConstraint("request_id", "seq"),)

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("orchestrator_run.request_id"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(32))
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    service: Mapped[str | None] = mapped_column(String(8), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    # PAYMENT_RESULT 행만: B-08 전달 상태 (PENDING → DONE). D-08은 MVP 2
    sync_b08: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(KstDateTime)


class IdempotencyRecord(Base):
    __tablename__ = "orchestrator_idempotency"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), primary_key=True)  # '{API}:{키}'
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(KstDateTime)


class CandidateView(Base):
    """후보 화면이 실제로 보여 준 후보 (이슈 #48, 추가만 함). 점주 대시보드의 '화면 표시' 수가 된다.

    주문(orchestrator_run)과 따로 둔다: 화면이 보내는 기록이 주문 행을 바꾸면
    같은 순간의 후보 선택과 낙관적 잠금이 부딪힌다.
    shown: [{candidate_id, restaurant_id, position, collapsed, live_status}]
    """

    __tablename__ = "orchestrator_candidate_view"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("orchestrator_run.request_id"), index=True)
    shown: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(KstDateTime, index=True)
