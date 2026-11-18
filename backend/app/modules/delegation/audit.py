"""감사 로그 (OWASP Logging Cheat Sheet 적용).

1. 구조화: 자유 문장이 아니라 정해진 칸(JSON)에 기록한다 → 줄바꿈으로 가짜 로그를 끼워 넣을 수 없다.
2. 민감정보 제외: 토큰·카드번호·주소·전화번호 같은 키는 값 대신 "[REDACTED]".
3. 크기 제한: 긴 문자열은 자르고, 줄바꿈 문자는 공백으로 바꾼다.
4. 변조 감지: 각 행이 앞 행의 hash를 품는 사슬 구조.
5. 같은 트랜잭션: 상태 변경과 로그는 함께 저장되거나 함께 취소된다.
   로그를 못 남기면 판단 자체가 취소된다 (fail-closed, OWASP AI Agent).
"""

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.delegation.constants import Actor, EventType
from app.modules.delegation.hashing import canonical_json, sha256_hex
from app.modules.delegation.models import AuditLog

GENESIS = "GENESIS"  # 첫 행의 prev_hash
MAX_TEXT = 300
SENSITIVE_KEYS = {
    "password",
    "token",
    "access_token",
    "authorization",
    "service_key",
    "card_number",
    "address",
    "delivery_address",
    "phone",
    "original_message",
}


def sanitize(value: Any, depth: int = 0) -> Any:
    """로그에 넣기 전에 값을 안전하게 만든다."""
    if depth > 5:
        return "[TOO_DEEP]"
    if isinstance(value, dict):
        return {
            str(k)[:50]: "[REDACTED]"
            if str(k).lower() in SENSITIVE_KEYS
            else sanitize(v, depth + 1)
            for k, v in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(v, depth + 1) for v in list(value)[:50]]
    if isinstance(value, str):
        cleaned = value.replace("\r", " ").replace("\n", " ")
        return cleaned if len(cleaned) <= MAX_TEXT else cleaned[:MAX_TEXT] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize(str(value), depth + 1)  # Enum, datetime 등은 문자열로


def _row_hash(prev_hash: str, fields: dict[str, Any]) -> str:
    return sha256_hex(prev_hash + canonical_json(fields))


def _hash_fields(row: AuditLog) -> dict[str, Any]:
    return {
        "log_id": row.log_id,
        "created_at": row.created_at.isoformat(),
        "request_id": row.request_id,
        "approval_id": row.approval_id,
        "execution_id": row.execution_id,
        "event_type": row.event_type,
        "actor": row.actor,
        "result": row.result,
        "reason_codes": row.reason_codes,
        "details": row.details,
    }


def record(
    session: Session,
    *,
    now: datetime,
    event_type: EventType,
    actor: Actor,
    result: str,
    request_id: str,
    approval_id: str | None = None,
    execution_id: str | None = None,
    reason_codes: Iterable[str] = (),
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """로그 한 줄을 현재 트랜잭션에 추가한다. commit은 호출한 쪽(service)이 한다."""
    if session.get_bind().dialect.name == "postgresql":
        # 로그 사슬에 동시에 두 줄이 붙지 않도록 트랜잭션 동안 '로그 쓰기 자물쇠'를 잡는다.
        # (SQLite는 쓰기가 원래 한 번에 하나라 필요 없다)
        session.execute(text("SELECT pg_advisory_xact_lock(724001)"))
    last = session.scalars(select(AuditLog).order_by(AuditLog.seq.desc()).limit(1)).first()
    prev_hash = last.hash if last else GENESIS

    row = AuditLog(
        log_id=str(uuid.uuid4()),
        created_at=now,
        request_id=request_id,
        approval_id=approval_id,
        execution_id=execution_id,
        event_type=str(event_type),
        actor=str(actor),
        result=result,
        reason_codes=[str(c) for c in reason_codes],
        details=sanitize(details or {}),
        prev_hash=prev_hash,
        hash="",
    )
    row.hash = _row_hash(prev_hash, _hash_fields(row))
    session.add(row)
    session.flush()  # 같은 트랜잭션 안의 다음 record()가 이 행을 '마지막 행'으로 보게 한다
    return row


def verify_chain(session: Session) -> tuple[bool, int | None]:
    """사슬 전체를 다시 계산한다. (정상 여부, 처음 끊긴 행의 seq)"""
    prev_hash = GENESIS
    for row in session.scalars(select(AuditLog).order_by(AuditLog.seq)):
        if row.prev_hash != prev_hash or row.hash != _row_hash(prev_hash, _hash_fields(row)):
            return False, row.seq
        prev_hash = row.hash
    return True, None
