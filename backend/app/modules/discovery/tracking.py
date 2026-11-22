"""가게별 추천 기록 조회 (이슈 #48). 점주 대시보드(이슈 ③)가 쓴다. 읽기 전용.

- 후보 포함: A가 후보로 내보낸 횟수 (a_candidate, 재탐색 회차 포함)
- 선택: 사용자가 그 가게 후보를 고른 횟수 (a_request.selected_candidate_id)
- 제외 사유: 음식 종류는 맞았지만 후보가 되지 못한 횟수와 이유 (a_exclusion)
화면에 실제로 보였는지는 O의 주문 이벤트(CANDIDATES_SHOWN)에 있다 (orchestrator.tracking).
"""

from collections import Counter
from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.modules.discovery.models import Candidate, DiscoveryRequest, Exclusion
from app.modules.discovery.timeutil import KST


def _until(until: datetime | None) -> datetime:
    return until or datetime.max.replace(tzinfo=KST)


def candidate_count(
    db: Session, restaurant_id: str, since: datetime, until: datetime | None = None
) -> int:
    stmt = (
        select(func.count())
        .select_from(Candidate)
        .where(
            Candidate.restaurant_id == restaurant_id,
            Candidate.created_at >= since,
            Candidate.created_at < _until(until),
        )
    )
    return db.scalar(stmt) or 0


def selected_count(
    db: Session, restaurant_id: str, since: datetime, until: datetime | None = None
) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(DiscoveryRequest)
            .join(
                Candidate,
                and_(
                    Candidate.request_id == DiscoveryRequest.request_id,
                    Candidate.candidate_id == DiscoveryRequest.selected_candidate_id,
                ),
            )
            .where(
            Candidate.restaurant_id == restaurant_id,
            DiscoveryRequest.updated_at >= since,
            DiscoveryRequest.updated_at < _until(until),
        )
        )
        or 0
    )


def exclusion_reasons(
    db: Session, restaurant_id: str, since: datetime, until: datetime | None = None
) -> dict[str, int]:
    rows = db.scalars(
        select(Exclusion.reason).where(
            Exclusion.restaurant_id == restaurant_id,
            Exclusion.created_at >= since,
            Exclusion.created_at < _until(until),
        )
    ).all()
    return dict(Counter(rows).most_common())


def restaurant_summary(
    db: Session, restaurant_id: str, since: datetime, until: datetime | None = None
) -> dict:
    return {
        "restaurant_id": restaurant_id,
        "candidates": candidate_count(db, restaurant_id, since, until),
        "selected": selected_count(db, restaurant_id, since, until),
        "exclusions": exclusion_reasons(db, restaurant_id, since, until),
    }
