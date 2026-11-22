"""가게별 화면 표시·주문 완료 기록 조회 (이슈 #48). 점주 대시보드(이슈 ③)가 쓴다. 읽기 전용.

- 화면 표시: 후보 화면이 그 가게를 실제로 보여 준 주문 수
  (접혀 있다가 펼친 것 포함, 같은 주문은 한 번)
- 요기요 실시간 주문 불가로 보인 주문 수: 화면 표시 때 live_status가 UNAVAILABLE·BLOCKED였던 것
- 주문 완료: 그 가게로 결제가 끝난 주문 수 (E는 모의결제라 '매출'이 아니다)
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import KST
from app.modules.orchestrator.constants import RunState
from app.modules.orchestrator.models import CandidateView, Run

UNAVAILABLE = {"UNAVAILABLE", "BLOCKED"}


def _until(until: datetime | None) -> datetime:
    return until or datetime.max.replace(tzinfo=KST)


def shown_summary(
    session: Session, restaurant_id: str, since: datetime, until: datetime | None = None
) -> dict:
    shown: set[str] = set()
    unavailable: set[str] = set()
    views = select(CandidateView).where(
        CandidateView.created_at >= since, CandidateView.created_at < _until(until)
    )
    for view in session.scalars(views):
        for item in view.shown or []:
            if item.get("restaurant_id") != restaurant_id:
                continue
            shown.add(view.request_id)
            if item.get("live_status") in UNAVAILABLE:
                unavailable.add(view.request_id)
    return {"shown": len(shown), "shown_unavailable": len(unavailable)}


def completed_count(
    session: Session, restaurant_id: str, since: datetime, until: datetime | None = None
) -> int:
    runs = session.scalars(
        select(Run).where(
            Run.state == RunState.COMPLETED,
            Run.updated_at >= since,
            Run.updated_at < _until(until),
        )
    ).all()
    return sum(
        1
        for r in runs
        if ((r.context or {}).get("selected_candidate") or {})
        .get("restaurant", {})
        .get("restaurant_id")
        == restaurant_id
    )
