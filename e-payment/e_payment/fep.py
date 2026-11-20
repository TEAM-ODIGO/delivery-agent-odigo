"""모의 기관(FEP). 진짜 카드사·지역화폐 기관을 흉내 내는 E 내부 장부 (E-07·E-08·E-09).

결과는 mode로 고른다 (config.E_MOCK_MODE 또는 요청 헤더 X-Mock-Outcome).
  APPROVE : 승인
  DECLINE : 거절
  UNKNOWN : 기관은 승인했지만 응답이 유실됨 → FepUnavailable. 나중에 조회·대사로 APPROVED가 된다
  LOST    : 기관이 처리했는지도 알 수 없음 → FepUnavailable. 조회도 안 됨 (UNKNOWN 유지)
응답이 없으면 같은 거래를 다시 보내지 않고 lookup으로만 확인한다 (명세 E-07·E-08).
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from e_payment.db import now
from e_payment.errors import AppError
from e_payment.models import FepTransaction

MODES = {"APPROVE", "DECLINE", "UNKNOWN", "LOST"}


class FepUnavailable(Exception):
    """기관 응답이 없다. 결과를 모른다 (재전송 금지, 조회로 확인)."""


def _out(t: FepTransaction) -> dict:
    out = {"transaction_id": t.transaction_id, "status": t.status, "mock": True}
    if t.approval_code:
        out["approval_code"] = t.approval_code
    return out


def authorize(db: Session, transaction_id: str, amount_krw: int, mode: str) -> dict:
    """E-07. 같은 transaction_id는 처음 결과를 그대로 돌려준다 (멱등)."""
    existing = db.get(FepTransaction, transaction_id)
    if existing is not None and existing.kind == "PAY":
        return _out(existing)
    if mode == "LOST":
        raise FepUnavailable("기관 응답 없음")
    approved = mode != "DECLINE"
    row = FepTransaction(
        transaction_id=transaction_id,
        kind="PAY",
        status="APPROVED" if approved else "DECLINED",
        approval_code=("TEST" + uuid.uuid4().hex[:6].upper()) if approved else None,
        amount_krw=amount_krw,
        created_at=now(),
    )
    db.add(row)
    db.flush()
    if mode == "UNKNOWN":
        raise FepUnavailable("기관 응답 유실")
    return _out(row)


def lookup(db: Session, transaction_id: str) -> dict:
    """E-08. 기관이 가진 거래 상태. 기관에 기록이 없으면 확인 불가."""
    row = db.get(FepTransaction, transaction_id)
    if row is None:
        raise FepUnavailable("기관 상태 조회 불가")
    return _out(row)


def refund(
    db: Session,
    refund_transaction_id: str,
    original_transaction_id: str,
    amount_krw: int,
    refund_mode: str,
) -> dict:
    """E-09. 같은 refund_transaction_id는 처음 결과 그대로. 원거래 금액을 넘는 환불은 거부."""
    existing = db.get(FepTransaction, refund_transaction_id)
    if existing is not None and existing.kind == "REFUND":
        return _out(existing)
    original = db.get(FepTransaction, original_transaction_id)
    if original is None or original.status != "APPROVED":
        raise AppError(404, "TRANSACTION_NOT_FOUND", "환불할 원거래가 없습니다.")
    done = db.scalar(
        select(func.coalesce(func.sum(FepTransaction.amount_krw), 0)).where(
            FepTransaction.kind == "REFUND",
            FepTransaction.original_transaction_id == original_transaction_id,
        )
    )
    if done + amount_krw > original.amount_krw:
        raise AppError(409, "ALREADY_REFUNDED", "이미 환불된 거래입니다.")
    if refund_mode != "REFUNDED":
        return {"refund_transaction_id": refund_transaction_id, "status": "FAILED", "mock": True}
    row = FepTransaction(
        transaction_id=refund_transaction_id,
        kind="REFUND",
        status="REFUNDED",
        amount_krw=amount_krw,
        original_transaction_id=original_transaction_id,
        created_at=now(),
    )
    db.add(row)
    db.flush()
    return {"refund_transaction_id": refund_transaction_id, "status": "REFUNDED", "mock": True}
