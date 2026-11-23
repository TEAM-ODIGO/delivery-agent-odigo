"""A(탐색·추천) 모듈 전용 테이블. 접두어 a_ (다른 모듈은 읽거나 쓰지 않는다)."""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JsonType as JSONB, KstDateTime


class DiscoveryRequest(Base):
    __tablename__ = "a_request"

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # O가 발급
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    original_message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30))  # NEED_CLARIFICATION·CONFIRMED·RECOMMENDED·SELECTED
    constraints: Mapped[dict] = mapped_column(JSONB)
    constraints_version: Mapped[int] = mapped_column(Integer)
    clarification_questions: Mapped[list] = mapped_column(JSONB)
    # {조건 이름: 메모}. 응답에서는 값만 목록으로 내보내고, 그 조건이 바뀌면 메모를 지운다
    normalization_notes: Mapped[dict] = mapped_column(JSONB)
    selected_candidate_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    search_count: Mapped[int] = mapped_column(Integer, default=0)  # 탐색 회차 (후보 ID 번호를 이어서 매기려고)
    initial_response: Mapped[dict] = mapped_column(JSONB)  # A-01 멱등 응답용 첫 결과
    created_at: Mapped[datetime] = mapped_column(KstDateTime())
    updated_at: Mapped[datetime] = mapped_column(KstDateTime())


class ConstraintsHistory(Base):
    __tablename__ = "a_constraints_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("a_request.request_id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(10))  # LLM·RULE·USER
    constraints: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(KstDateTime())


class Candidate(Base):
    __tablename__ = "a_candidate"

    request_id: Mapped[str] = mapped_column(ForeignKey("a_request.request_id"), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    search_round: Mapped[int] = mapped_column(Integer)
    constraints_version: Mapped[int] = mapped_column(Integer)
    rank: Mapped[int] = mapped_column(Integer)
    restaurant_id: Mapped[str] = mapped_column(String(64))
    score: Mapped[float] = mapped_column(Numeric(5, 1))
    meets_all_constraints: Mapped[bool] = mapped_column(Boolean)
    body: Mapped[dict] = mapped_column(JSONB)  # 응답에 그대로 내보내는 후보 JSON
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)  # 조건이 바뀌거나 재탐색하면 false
    created_at: Mapped[datetime] = mapped_column(KstDateTime())


class LlmCall(Base):
    """LLM 호출 기록 (MVP 2 A-08 추적 데이터에서 그대로 쓸 수 있게 MVP 1부터 남긴다)."""

    __tablename__ = "a_llm_call"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    purpose: Mapped[str] = mapped_column(String(20))  # EXTRACT·EXPLAIN
    model: Mapped[str] = mapped_column(String(100))
    success: Mapped[bool] = mapped_column(Boolean)
    attempt: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(KstDateTime())


class Exclusion(Base):
    """검색에서 후보가 되지 못한 가게와 그 이유 (이슈 #48).

    점주 대시보드가 '왜 빠졌는지'를 세는 데 쓴다.

    음식 종류가 다른 가게는 남기지 않는다. reason 값은 search.exclusion_trace 참고.
    """

    __tablename__ = "a_exclusion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("a_request.request_id"), index=True)
    search_round: Mapped[int] = mapped_column(Integer)
    restaurant_id: Mapped[str] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(KstDateTime(), index=True)
