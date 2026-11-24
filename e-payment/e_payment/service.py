"""E 업무 로직. E-01 결제 요청의 처리 순서는 명세 그대로:
 ① Idempotency-Key(= execution_id) 멱등 확인 → ② D-06, allowed: true일 때만 진행
 → ③ PENDING 저장 → ④ E-07 모의 기관 승인 → ⑤ 원장 기록(결제수단 잔액·실적 갱신).
결제 결과(승인·거절·미확정·차단)는 모두 200으로 돌려준다.
"""

import base64
import hashlib
import json
import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from e_payment import fep, nfc
from e_payment.db import KST, now
from e_payment.dclient import D06_FIELDS, D06_SPLIT_FIELDS, DClient
from e_payment.errors import AppError
from e_payment.models import Payment, PaymentMethod, Reconciliation, Refund

PAY_FIELDS = (
    "request_id", "user_id", "execution_id", "approval_id", "quote_id",
    "proposal_hash", "payment_method_id", "charge_krw",
)
SPLIT_FIELDS = D06_SPLIT_FIELDS  # 분할결제(로컬 확장). 있을 때만 지문·D-06에 포함


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:8].upper()


def _fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _iso(v: datetime | None) -> str | None:
    return v.astimezone(KST).isoformat(timespec="seconds") if v else None


# ---------------------------------------------------------------- 응답 모양
def payment_out(p: Payment) -> dict:
    out = {
        "payment_id": p.payment_id,
        "execution_id": p.execution_id,
        "transaction_id": p.transaction_id,
        "request_id": p.request_id,
        "status": p.status,
        "charge_krw": p.charge_krw,
        "refunded_krw": p.refunded_krw,
        "payment_method_id": p.payment_method_id,
        "approved_at": _iso(p.approved_at),
        "mock": True,
    }
    if p.local_currency_payment_method_id:  # 분할결제: 수단별 몫
        out["payments"] = [
            {"payment_method_id": p.local_currency_payment_method_id, "charge_krw": p.local_currency_charge_krw},
            {"payment_method_id": p.payment_method_id, "charge_krw": p.charge_krw - p.local_currency_charge_krw},
        ]
    if p.reason_codes:
        out["reason_codes"] = list(p.reason_codes)
    return out


def blocked_out(execution_id: str, reason_codes: list[str]) -> dict:
    return {"payment_id": None, "execution_id": execution_id, "status": "BLOCKED", "reason_codes": reason_codes}


def refund_out(r: Refund) -> dict:
    return {"refund_id": r.refund_id, "payment_id": r.payment_id, "status": r.status, "amount_krw": r.amount_krw, "mock": True}


# ---------------------------------------------------------------- 원장 갱신
def _apply_payment(method: PaymentMethod, charge_krw: int) -> None:
    if method.type == "LOCAL_CURRENCY":
        method.balance_krw = (method.balance_krw or 0) - charge_krw
    else:
        method.monthly_performance_krw = (method.monthly_performance_krw or 0) + charge_krw
    method.updated_at = now()


def _apply_refund(method: PaymentMethod, amount_krw: int) -> None:
    if method.type == "LOCAL_CURRENCY":
        method.balance_krw = (method.balance_krw or 0) + amount_krw
    else:
        method.monthly_performance_krw = max(0, (method.monthly_performance_krw or 0) - amount_krw)
    method.updated_at = now()


def _legs(p: Payment) -> list[tuple[str, int]]:
    """결제를 수단별 몫으로 나눈다. 분할이 아니면 한 몫."""
    if p.local_currency_payment_method_id:
        return [
            (p.local_currency_payment_method_id, p.local_currency_charge_krw),
            (p.payment_method_id, p.charge_krw - p.local_currency_charge_krw),
        ]
    return [(p.payment_method_id, p.charge_krw)]


def _refund_legs(p: Payment, amount_krw: int) -> list[tuple[str, int]]:
    """환불금을 수단별 몫으로 나눈다. 분할결제는 지역화폐 몫부터 먼저 돌려준다(누적 환불액 기준)."""
    if not p.local_currency_payment_method_id:
        return [(p.payment_method_id, amount_krw)]
    local_total = p.local_currency_charge_krw
    local_part = min(local_total, p.refunded_krw + amount_krw) - min(local_total, p.refunded_krw)
    return [(p.local_currency_payment_method_id, local_part), (p.payment_method_id, amount_krw - local_part)]


def _method(db: Session, payment_method_id: str, lock: bool = False) -> PaymentMethod | None:
    stmt = select(PaymentMethod).where(PaymentMethod.payment_method_id == payment_method_id)
    return db.scalars(stmt.with_for_update() if lock else stmt).first()


# ---------------------------------------------------------------- E-01
def _replay(existing: Payment, fp: str) -> dict:
    if existing.fingerprint != fp:
        raise AppError(409, "IDEMPOTENCY_CONFLICT", "같은 execution_id에 다른 내용의 요청이 이미 있습니다.", {"execution_id": existing.execution_id})
    return payment_out(existing)


def _by_execution(db: Session, execution_id: str) -> Payment | None:
    return db.scalars(select(Payment).where(Payment.execution_id == execution_id)).first()


def request_payment(
    db: Session,
    body: dict,
    request_id: str,
    d: DClient,
    mode: str,
    nfc_cards: dict[str, str] | None = None,
    nfc_tokens: dict[str, str] | None = None,
) -> dict:
    exe = body["execution_id"]
    split = {k: body[k] for k in SPLIT_FIELDS if body.get(k) is not None}
    fp = _fingerprint({**{k: body[k] for k in PAY_FIELDS}, **split})

    existing = _by_execution(db, exe)  # ① 멱등
    if existing is not None:
        return _replay(existing, fp)

    try:  # ② D-06 (차단·D 오류·응답 없음은 결제하지 않는다)
        check = d.check({**{k: body[k] for k in D06_FIELDS}, **split}, request_id)
        allowed = check.get("allowed") is True
        reasons = [str(c) for c in (check.get("reason_codes") or [])]
    except Exception:
        allowed, reasons = False, ["DELEGATION_UNAVAILABLE"]
    if not allowed:
        existing = _by_execution(db, exe)  # 동시에 들어온 같은 요청이 이미 결제를 만들었을 수 있다
        if existing is not None:
            return _replay(existing, fp)
        return blocked_out(exe, reasons or ["PAYMENT_NOT_ALLOWED"])

    t = now()  # ③ PENDING 저장
    p = Payment(
        payment_id=_new_id("PAY"), execution_id=exe, fingerprint=fp, transaction_id=_new_id("TXN"),
        request_id=body["request_id"], user_id=body["user_id"], approval_id=body["approval_id"],
        quote_id=body["quote_id"], payment_method_id=body["payment_method_id"],
        charge_krw=body["charge_krw"], refunded_krw=0, status="PENDING", approved_at=None,
        local_currency_payment_method_id=split.get("local_currency_payment_method_id"),
        local_currency_charge_krw=split.get("local_currency_charge_krw"),
        reason_codes=[], created_at=t, updated_at=t,
    )
    db.add(p)
    try:
        db.commit()
    except IntegrityError:  # 같은 execution_id가 거의 동시에 들어옴 → 먼저 저장된 것을 돌려준다
        db.rollback()
        return _replay(_by_execution(db, exe), fp)

    _settle(db, p, mode, body.get("card_tap"), nfc_cards or {}, nfc_tokens or {})  # ④ 모의 기관 승인 → ⑤ 원장 기록
    return payment_out(p)


def _settle(
    db: Session,
    p: Payment,
    mode: str,
    card_tap: dict | None = None,
    nfc_cards: dict | None = None,
    nfc_tokens: dict | None = None,
) -> None:
    legs = _legs(p)
    methods = {mid: _method(db, mid, lock=True) for mid, _ in legs}
    reason = None
    for mid, amount in legs:
        method = methods[mid]
        if method is None or method.user_id != p.user_id:
            reason = "PAYMENT_METHOD_NOT_FOUND"
        elif method.status != "ACTIVE":
            reason = "PAYMENT_METHOD_INACTIVE"
        elif method.type == "LOCAL_CURRENCY" and (method.balance_krw or 0) < amount:
            reason = "INSUFFICIENT_BALANCE"
        if reason:
            break
    if not reason:  # NFC 실물 카드(로컬 확장): 카드 몫(payment_method_id)을 낼 카드를 태그했는지
        reason = nfc.check(nfc_cards or {}, nfc_tokens or {}, p.payment_method_id, card_tap)
    if reason:
        p.status, p.reason_codes = "FAILED", [reason]
    else:
        try:
            res = fep.authorize(db, p.transaction_id, p.charge_krw, mode)
            if res["status"] == "APPROVED":
                p.status, p.approved_at = "APPROVED", now()
                for mid, amount in legs:  # 몫마다 원장 갱신 (둘 다 한 트랜잭션으로 커밋)
                    _apply_payment(methods[mid], amount)
            else:
                p.status, p.reason_codes = "FAILED", ["MOCK_DECLINED"]
        except fep.FepUnavailable:
            p.status, p.reason_codes = "UNKNOWN", ["FEP_TIMEOUT"]
    p.updated_at = now()
    db.commit()


# ---------------------------------------------------------------- 미확정 확정 (E-08 조회 / E-10 대사)
def resolve_unknown(db: Session, p: Payment) -> bool:
    """UNKNOWN 건을 기관 조회(E-08)로 확정한다. 확인 불가면 UNKNOWN 유지."""
    if p.status != "UNKNOWN":
        return False
    try:
        res = fep.lookup(db, p.transaction_id)
    except fep.FepUnavailable:
        return False
    if res["status"] == "APPROVED":
        p.status, p.approved_at, p.reason_codes = "APPROVED", now(), []
        for mid, amount in _legs(p):
            method = _method(db, mid, lock=True)
            if method is not None:
                _apply_payment(method, amount)
    else:
        p.status, p.reason_codes = "FAILED", ["MOCK_DECLINED"]
    p.updated_at = now()
    db.commit()
    return True


def reconcile_all(db: Session) -> int:
    rows = db.scalars(select(Payment).where(Payment.status == "UNKNOWN")).all()
    return sum(resolve_unknown(db, p) for p in rows)


# ---------------------------------------------------------------- E-02·E-03·E-04
def get_by_execution(db: Session, execution_id: str, reconcile: bool) -> dict:
    p = _by_execution(db, execution_id)
    if p is None:
        raise AppError(404, "PAYMENT_NOT_FOUND", "해당 실행 ID의 결제 요청이 없습니다.")
    if reconcile:
        resolve_unknown(db, p)
    return payment_out(p)


def get_payment(db: Session, payment_id: str, reconcile: bool) -> dict:
    p = db.get(Payment, payment_id)
    if p is None:
        raise AppError(404, "PAYMENT_NOT_FOUND", "결제 내역이 없습니다.")
    if reconcile:
        resolve_unknown(db, p)
    refunds = db.scalars(select(Refund).where(Refund.payment_id == payment_id).order_by(Refund.requested_at)).all()
    out = payment_out(p)
    out["refunds"] = [
        {"refund_id": r.refund_id, "status": r.status, "amount_krw": r.amount_krw, "requested_at": _iso(r.requested_at)}
        for r in refunds
    ]
    return out


def _encode_cursor(p: Payment) -> str:
    return base64.urlsafe_b64encode(f"{p.created_at.isoformat()}|{p.payment_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, pid = raw.split("|", 1)
        return datetime.fromisoformat(ts), pid
    except Exception as exc:
        raise AppError(400, "INVALID_CURSOR", "조회 커서가 올바르지 않습니다.") from exc


def list_payments(db: Session, user_id: str, cursor: str | None, limit: int) -> dict:
    if not 1 <= limit <= 100:
        raise AppError(400, "VALIDATION_ERROR", "limit은 1~100이어야 합니다.", {"fields": [{"loc": "query.limit", "reason": "out_of_range"}]})
    stmt = select(Payment).where(Payment.user_id == user_id)
    if cursor:
        ts, pid = _decode_cursor(cursor)
        stmt = stmt.where(or_(Payment.created_at < ts, and_(Payment.created_at == ts, Payment.payment_id < pid)))
    rows = db.scalars(stmt.order_by(Payment.created_at.desc(), Payment.payment_id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    items = [
        {k: v for k, v in payment_out(p).items() if k in ("payment_id", "request_id", "status", "charge_krw", "refunded_krw", "approved_at", "mock")}
        for p in page
    ]
    return {"items": items, "next_cursor": _encode_cursor(page[-1]) if len(rows) > limit else None}


def list_card_payments(db: Session, user_id: str, payment_method_id: str, limit: int) -> dict:
    """카드 혜택 화면용 (로컬 확장, 명세 밖): 결제수단 하나의 최근 결제.

    E-04 응답 모양(명세)은 그대로 두고 따로 둔다. 분할결제는 이 결제수단이 낸 몫(share_krw)을 함께 준다.
    """
    if not 1 <= limit <= 20:
        raise AppError(400, "VALIDATION_ERROR", "limit은 1~20이어야 합니다.", {"fields": [{"loc": "query.limit", "reason": "out_of_range"}]})
    stmt = (
        select(Payment)
        .where(Payment.user_id == user_id)
        .where(or_(Payment.payment_method_id == payment_method_id,
                   Payment.local_currency_payment_method_id == payment_method_id))
        .order_by(Payment.created_at.desc(), Payment.payment_id.desc())
        .limit(limit)
    )
    items = []
    for p in db.scalars(stmt).all():
        item = payment_out(p)
        item["created_at"] = _iso(p.created_at)
        if p.local_currency_payment_method_id == payment_method_id:
            item["share_krw"] = p.local_currency_charge_krw
        elif p.local_currency_payment_method_id:
            item["share_krw"] = p.charge_krw - (p.local_currency_charge_krw or 0)
        else:
            item["share_krw"] = p.charge_krw
        item["refundable_krw"] = max(p.charge_krw - p.refunded_krw - _open_refund_krw(db, p.payment_id), 0) if p.status == "APPROVED" else 0
        items.append(item)
    return {"payment_method_id": payment_method_id, "items": items}


def _open_refund_krw(db: Session, payment_id: str) -> int:
    return sum(
        r.amount_krw for r in db.scalars(select(Refund).where(Refund.payment_id == payment_id, Refund.status.in_(("PENDING", "UNKNOWN"))))
    )


# ---------------------------------------------------------------- E-05·E-06 환불
def create_refund(db: Session, payment_id: str, body: dict, idem_key: str, refund_mode: str) -> dict:
    p = db.get(Payment, payment_id)
    if p is None:
        raise AppError(404, "PAYMENT_NOT_FOUND", "결제 내역이 없습니다.")
    if p.user_id != body["user_id"]:
        raise AppError(403, "FORBIDDEN_USER", "본인의 결제만 환불할 수 있습니다.")
    fp = _fingerprint({"payment_id": payment_id, **body})
    prev = db.scalars(select(Refund).where(Refund.payment_id == payment_id, Refund.idempotency_key == idem_key)).first()
    if prev is not None:
        if prev.fingerprint != fp:
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "같은 키에 다른 내용의 환불 요청이 이미 있습니다.")
        return refund_out(prev)
    if p.status != "APPROVED":
        raise AppError(409, "INVALID_STATUS", "승인된 결제만 환불할 수 있습니다.", {"status": p.status})
    open_amount = sum(
        r.amount_krw for r in db.scalars(select(Refund).where(Refund.payment_id == payment_id, Refund.status.in_(("PENDING", "UNKNOWN"))))
    )
    if body["amount_krw"] > p.charge_krw - p.refunded_krw - open_amount:
        raise AppError(409, "REFUND_AMOUNT_EXCEEDED", "환불 가능 금액을 초과했습니다.", {"refundable_krw": p.charge_krw - p.refunded_krw - open_amount})

    r = Refund(
        refund_id=_new_id("RF"), payment_id=payment_id, idempotency_key=idem_key, fingerprint=fp,
        refund_transaction_id=_new_id("RTXN"), status="PENDING", amount_krw=body["amount_krw"],
        reason=body.get("reason"), requested_at=now(),
    )
    db.add(r)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        prev = db.scalars(select(Refund).where(Refund.payment_id == payment_id, Refund.idempotency_key == idem_key)).first()
        return refund_out(prev)
    first = refund_out(r)  # 접수 시점 응답(PENDING). 처리 결과는 E-06으로 확인한다
    _process_refund(db, p, r, refund_mode)
    return first


def _process_refund(db: Session, p: Payment, r: Refund, refund_mode: str) -> None:
    try:
        res = fep.refund(db, r.refund_transaction_id, p.transaction_id, r.amount_krw, refund_mode)
    except fep.FepUnavailable:
        r.status = "UNKNOWN"
        db.commit()
        return
    except AppError:
        r.status = "FAILED"
        db.commit()
        return
    if res["status"] == "REFUNDED":
        r.status = "REFUNDED"
        for mid, amount in _refund_legs(p, r.amount_krw):  # 분할결제는 몫별로 돌려준다
            method = _method(db, mid, lock=True)
            if method is not None and amount:
                _apply_refund(method, amount)
        p.refunded_krw += r.amount_krw
        p.updated_at = now()
    else:
        r.status = "FAILED"
    db.commit()


def get_refund(db: Session, payment_id: str, refund_id: str) -> dict:
    if db.get(Payment, payment_id) is None:
        raise AppError(404, "PAYMENT_NOT_FOUND", "결제 내역이 없습니다.")
    r = db.get(Refund, refund_id)
    if r is None or r.payment_id != payment_id:
        raise AppError(404, "REFUND_NOT_FOUND", "환불 내역이 없습니다.")
    return refund_out(r)


# ---------------------------------------------------------------- E-10 대사
def create_reconciliation(db: Session, body: dict, idem_key: str) -> dict:
    ids = sorted(set(body["transaction_ids"]))
    fp = _fingerprint({"transaction_ids": ids, "reason": body.get("reason")})
    prev = db.scalars(select(Reconciliation).where(Reconciliation.idempotency_key == idem_key)).first()
    if prev is not None:
        if prev.fingerprint != fp:
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "같은 키에 다른 내용의 대사 요청이 이미 있습니다.")
        return {"reconciliation_id": prev.reconciliation_id, "status": "QUEUED"}
    for other in db.scalars(select(Reconciliation).where(Reconciliation.status == "QUEUED")):
        if set(other.transaction_ids) & set(ids):
            raise AppError(409, "RECONCILIATION_IN_PROGRESS", "동일 거래의 대사가 진행 중입니다.")
    rec = Reconciliation(
        reconciliation_id=_new_id("recon_").lower(), idempotency_key=idem_key, fingerprint=fp,
        transaction_ids=ids, status="QUEUED", created_at=now(),
    )
    db.add(rec)
    db.commit()
    for p in db.scalars(select(Payment).where(Payment.transaction_id.in_(ids), Payment.status == "UNKNOWN")):
        resolve_unknown(db, p)
    rec.status = "DONE"
    db.commit()
    return {"reconciliation_id": rec.reconciliation_id, "status": "QUEUED"}


# ---------------------------------------------------------------- E-11
def list_methods(db: Session, user_id: str, type_: str | None) -> dict:
    if type_ not in (None, "CARD", "LOCAL_CURRENCY"):
        raise AppError(400, "VALIDATION_ERROR", "type은 CARD 또는 LOCAL_CURRENCY여야 합니다.", {"fields": [{"loc": "query.type", "reason": "invalid_value"}]})
    rows = db.scalars(select(PaymentMethod).where(PaymentMethod.user_id == user_id).order_by(PaymentMethod.payment_method_id)).all()
    if not rows:
        raise AppError(404, "USER_NOT_FOUND", "사용자를 찾을 수 없습니다.")
    results = [method_item(m) for m in rows if not type_ or m.type == type_]
    return {"user_id": user_id, "count": len(results), "results": results, "mock": True}


def method_item(m: PaymentMethod) -> dict:
    """E-11 결제수단 한 건의 모양 (카드 추가 응답도 같은 모양을 쓴다)."""
    item = {"payment_method_id": m.payment_method_id, "product_id": m.product_id, "type": m.type, "name": m.name}
    if m.type == "LOCAL_CURRENCY":
        item["balance_krw"] = m.balance_krw or 0
    else:
        item["monthly_performance_krw"] = m.monthly_performance_krw or 0
    item.update(status=m.status, updated_at=_iso(m.updated_at))
    return item
