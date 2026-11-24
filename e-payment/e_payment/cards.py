"""내 카드 추가 · 모의 마이데이터 (로컬 확장, 팀 합의 전).

- 진짜 마이데이터가 아니다. 진짜 마이데이터는 본인신용정보관리업 허가를 받은 사업자만 연동할 수 있어서,
  여기서는 "동의 → 카드 목록 → 선택" 흐름만 흉내 내고 카드 목록은 아래 고정 목록(MYDATA_CARDS)이다.
- 카드 번호 전체·유효기간·CVC는 받지도 저장하지도 않는다 (끝 4자리만).
- 혜택 규칙이 있는 카드(SUPPORTED)는 그 상품으로, 없는 카드는 EXTERNAL_PRODUCT_ID로 결제수단 원장에 넣는다.
  규칙이 없는 카드는 C가 추천·결제에서 빼고, 카드 목록에는 "혜택 규칙 없음"으로 보인다.
"""

import uuid
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from e_payment.db import now
from e_payment.errors import AppError
from e_payment.models import CardRegistration, MyDataConsent, Payment, PaymentMethod

# C에 혜택 규칙이 있는 카드 (C의 products.CARD_PRODUCTS와 같은 상품 ID·이름)
SUPPORTED = {
    "PRD-CARD-A": "요기요 신한카드 체크",
    "PRD-CARD-B": "신한카드 구독 좋아요",
    "PRD-CARD-C": "신한카드 Deep On Platinum+",
}
EXTERNAL_PRODUCT_ID = "PRD-EXT-CARD"  # 혜택 규칙이 없는 카드

CONSENT_SCOPES = ("CARD_LIST", "CARD_USAGE")
CONSENT_MINUTES = 10

# 모의 마이데이터가 돌려주는 카드 목록 (누구에게나 같다). monthly_performance_krw는 "이번 달 이용액" 대용.
MYDATA_CARDS = [
    {"card_id": "MD-CARD-001", "issuer": "신한카드", "name": SUPPORTED["PRD-CARD-A"], "last4": "5512",
     "product_id": "PRD-CARD-A", "monthly_performance_krw": 210000},
    {"card_id": "MD-CARD-002", "issuer": "신한카드", "name": SUPPORTED["PRD-CARD-B"], "last4": "4821",
     "product_id": "PRD-CARD-B", "monthly_performance_krw": 430000},
    {"card_id": "MD-CARD-003", "issuer": "신한카드", "name": SUPPORTED["PRD-CARD-C"], "last4": "7733",
     "product_id": "PRD-CARD-C", "monthly_performance_krw": 320000},
    {"card_id": "MD-CARD-004", "issuer": "KB국민카드", "name": "KB국민 노리2 체크카드", "last4": "1209",
     "product_id": None, "monthly_performance_krw": 85000},
    {"card_id": "MD-CARD-005", "issuer": "현대카드", "name": "현대카드 ZERO", "last4": "3340",
     "product_id": None, "monthly_performance_krw": 150000},
]


def _iso(v):
    return v.isoformat(timespec="seconds")


def catalog() -> dict:
    """직접 입력 화면이 고를 수 있는 '혜택 규칙이 있는 카드' 목록."""
    return {"supported": [{"product_id": k, "name": v} for k, v in SUPPORTED.items()], "mock": True}


# ---------------------------------------------------------------- 모의 마이데이터
def create_consent(db: Session, user_id: str) -> dict:
    n = now()
    consent = MyDataConsent(
        consent_id="MDC" + uuid.uuid4().hex[:12].upper(), user_id=user_id, scopes=",".join(CONSENT_SCOPES),
        status="ACTIVE", created_at=n, expires_at=n + timedelta(minutes=CONSENT_MINUTES),
    )
    db.add(consent)
    db.commit()
    return {"consent_id": consent.consent_id, "user_id": user_id, "scopes": list(CONSENT_SCOPES),
            "expires_at": _iso(consent.expires_at), "mock": True}


def _valid_consent(db: Session, user_id: str, consent_id: str) -> MyDataConsent:
    consent = db.get(MyDataConsent, consent_id)
    if consent is None or consent.user_id != user_id:  # 남의 동의는 있는지도 알려 주지 않는다
        raise AppError(404, "CONSENT_NOT_FOUND", "마이데이터 동의를 찾을 수 없어요.")
    if consent.status != "ACTIVE" or now() >= consent.expires_at:
        raise AppError(410, "CONSENT_EXPIRED", "마이데이터 동의가 끝났어요. 다시 동의해 주세요.")
    return consent


def _held(db: Session, user_id: str) -> tuple[set[str], set[str]]:
    products = set(db.scalars(select(PaymentMethod.product_id).where(PaymentMethod.user_id == user_id)))
    refs = set(db.scalars(select(CardRegistration.external_ref).where(CardRegistration.user_id == user_id)))
    return products, refs


def _already(card: dict, products: set[str], refs: set[str]) -> bool:
    if card["product_id"]:
        return card["product_id"] in products
    return f"mydata:{card['card_id']}" in refs


def list_mydata_cards(db: Session, user_id: str, consent_id: str) -> dict:
    consent = _valid_consent(db, user_id, consent_id)
    products, refs = _held(db, user_id)
    results = [
        {"card_id": c["card_id"], "issuer": c["issuer"], "name": c["name"], "last4": c["last4"],
         "supported": c["product_id"] is not None, "monthly_performance_krw": c["monthly_performance_krw"],
         "already_added": _already(c, products, refs)}
        for c in MYDATA_CARDS
    ]
    return {"consent_id": consent.consent_id, "user_id": user_id, "count": len(results), "results": results,
            "mock": True}


# ---------------------------------------------------------------- 카드 추가·삭제
def _new_method(user_id: str, product_id: str, name: str, performance: int) -> PaymentMethod:
    return PaymentMethod(
        payment_method_id="CARD" + uuid.uuid4().hex[:8].upper(), user_id=user_id, product_id=product_id,
        type="CARD", name=name, balance_krw=None, monthly_performance_krw=performance, status="ACTIVE",
        updated_at=now(),
    )


def register_card(db: Session, body: dict, method_item) -> dict:
    user_id, source = body["user_id"], body["source"]
    products, refs = _held(db, user_id)
    issuer, last4, consent_id = body.get("issuer"), body.get("last4"), None

    if source == "MYDATA":
        consent_id = _valid_consent(db, user_id, body["consent_id"]).consent_id
        card = next((c for c in MYDATA_CARDS if c["card_id"] == body["mydata_card_id"]), None)
        if card is None:
            raise AppError(404, "MYDATA_CARD_NOT_FOUND", "마이데이터에서 이 카드를 찾을 수 없어요.")
        product_id = card["product_id"] or EXTERNAL_PRODUCT_ID
        name, issuer, last4 = card["name"], card["issuer"], card["last4"]
        if card["product_id"] is None:
            name = f"{name} ({last4})"
        performance, ref = card["monthly_performance_krw"], f"mydata:{card['card_id']}"
    elif body.get("product_id"):
        product_id = body["product_id"]
        if product_id not in SUPPORTED:
            raise AppError(400, "VALIDATION_ERROR", "혜택 규칙이 있는 카드가 아니에요.",
                           {"fields": [{"loc": "body.product_id", "reason": "invalid_value"}]})
        name, performance, ref = SUPPORTED[product_id], 0, f"product:{product_id}"
    else:
        product_id, performance = EXTERNAL_PRODUCT_ID, 0
        name = f"{body['name']} ({last4})" if last4 else body["name"]
        ref = f"manual:{body['name'].strip().lower()}:{last4 or ''}"

    # 혜택 규칙이 있는 카드는 한 사람이 같은 상품을 한 장만 갖는다 (처음 넣어 둔 카드 포함)
    if (product_id in SUPPORTED and product_id in products) or ref in refs:
        raise AppError(409, "ALREADY_REGISTERED", "이미 내 결제수단에 있는 카드예요.")

    method = _new_method(user_id, product_id, name, performance)
    db.add(method)
    db.add(CardRegistration(
        payment_method_id=method.payment_method_id, user_id=user_id, source=source, external_ref=ref,
        issuer=issuer, last4=last4, consent_id=consent_id, created_at=now(),
    ))
    try:
        db.commit()
    except IntegrityError:  # 거의 동시에 같은 카드를 두 번 넣은 경우
        db.rollback()
        raise AppError(409, "ALREADY_REGISTERED", "이미 내 결제수단에 있는 카드예요.") from None
    return {"payment_method": method_item(method),
            "registration": {"source": source, "issuer": issuer, "last4": last4, "supported": product_id in SUPPORTED},
            "mock": True}


def list_registrations(db: Session, user_id: str) -> dict:
    """이 사용자가 직접 추가한 카드의 등록 정보 (화면이 '뺄 수 있는 카드'를 가리는 데 쓴다)."""
    rows = db.scalars(select(CardRegistration).where(CardRegistration.user_id == user_id)
                      .order_by(CardRegistration.created_at, CardRegistration.payment_method_id)).all()
    results = [{"payment_method_id": r.payment_method_id, "source": r.source, "issuer": r.issuer,
                "last4": r.last4, "created_at": _iso(r.created_at)} for r in rows]
    return {"user_id": user_id, "count": len(results), "results": results, "mock": True}


def remove_card(db: Session, user_id: str, payment_method_id: str) -> dict:
    """직접 추가한 카드만 뺄 수 있다. 결제 내역이 있으면 장부가 어긋나지 않게 막는다."""
    method = db.get(PaymentMethod, payment_method_id)
    if method is None or method.user_id != user_id:
        raise AppError(404, "PAYMENT_METHOD_NOT_FOUND", "결제수단을 찾을 수 없어요.")
    registration = db.get(CardRegistration, payment_method_id)
    if registration is None:
        raise AppError(409, "NOT_REMOVABLE", "직접 추가한 카드만 뺄 수 있어요.")
    used = db.scalar(select(Payment.payment_id).where(or_(
        Payment.payment_method_id == payment_method_id,
        Payment.local_currency_payment_method_id == payment_method_id)).limit(1))
    if used is not None:
        raise AppError(409, "HAS_PAYMENTS", "결제 내역이 있는 카드는 뺄 수 없어요.")
    db.delete(registration)
    db.delete(method)
    db.commit()
    return {"payment_method_id": payment_method_id, "removed": True}
