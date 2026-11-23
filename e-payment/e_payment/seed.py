"""개발용 결제수단 원장 시드. C의 mock 원장(U001·U002)과 같은 값."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from e_payment.db import now
from e_payment.models import PaymentMethod

SEED = [
    # 카드 이름은 신한카드 실제 상품명. 혜택 규칙(C)의 전월 실적 조건을 채우도록 시연용 실적을 넣었다.
    ("CARD001", "U001", "PRD-CARD-A", "CARD", "요기요 신한카드 체크", None, 350000),   # 조건 전월 20만원
    ("CARD002", "U001", "PRD-CARD-B", "CARD", "신한카드 구독 좋아요", None, 550000),   # 조건 전월 40만원
    ("CARD003", "U001", "PRD-CARD-C", "CARD", "신한카드 Deep On Platinum+", None, 350000),  # 조건 전월 30만원
    ("LOCAL001", "U001", "PRD-LOCAL-A", "LOCAL_CURRENCY", "서울페이(서울사랑상품권)", 120000, None),
    ("CARD101", "U002", "PRD-CARD-A", "CARD", "요기요 신한카드 체크", None, 100000),
    ("LOCAL101", "U002", "PRD-LOCAL-A", "LOCAL_CURRENCY", "서울페이(서울사랑상품권)", 1000, None),
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
