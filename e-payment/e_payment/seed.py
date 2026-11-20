"""개발용 결제수단 원장 시드. C의 mock 원장(U001·U002)과 같은 값."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from e_payment.db import now
from e_payment.models import PaymentMethod

SEED = [
    ("CARD001", "U001", "PRD-CARD-A", "CARD", "가상카드 A", None, 350000),
    # 가상카드 B: 혜택 조건(당월 실적 50만원 이상)을 채우도록 실적을 올려 결제수단으로 고를 수 있게 함
    ("CARD002", "U001", "PRD-CARD-B", "CARD", "가상카드 B", None, 550000),
    ("CARD003", "U001", "PRD-CARD-C", "CARD", "가상카드 C", None, 200000),
    ("LOCAL001", "U001", "PRD-LOCAL-A", "LOCAL_CURRENCY", "가상 지역화폐", 120000, None),
    ("CARD101", "U002", "PRD-CARD-A", "CARD", "가상카드 A", None, 100000),
    ("LOCAL101", "U002", "PRD-LOCAL-A", "LOCAL_CURRENCY", "가상 지역화폐", 1000, None),
]


def seed_if_empty(db: Session) -> int:
    if db.scalar(select(func.count()).select_from(PaymentMethod)):
        return 0
    for mid, uid, pid, typ, name, bal, perf in SEED:
        db.add(
            PaymentMethod(
                payment_method_id=mid, user_id=uid, product_id=pid, type=typ, name=name,
                balance_krw=bal, monthly_performance_krw=perf, status="ACTIVE", updated_at=now(),
            )
        )
    db.commit()
    return len(SEED)
