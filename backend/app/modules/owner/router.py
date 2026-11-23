"""점주 대시보드 API (이슈 #50, 로컬 확장). 읽기 전용, 가게 단위 집계만 돌려준다.

MVP 1 시연: 로그인한 사용자면 가게를 골라 볼 수 있다.
실제 서비스에서는 점주별 접근 권한(자기 가게만)이 필요하다 (MVP 2).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.clock import now
from app.core.db import get_session
from app.modules.orchestrator.router import current_user_id
from app.modules.owner import service

router = APIRouter(prefix="/owner", tags=["owner (점주 대시보드)"])

DB = Annotated[Session, Depends(get_session)]
User = Annotated[str, Depends(current_user_id)]


def get_now():
    return now()


Now = Annotated[object, Depends(get_now)]


@router.get("/restaurants", summary="(로컬 확장) 점주 대시보드: 가게 고르기")
def list_restaurants(db: DB, _user: User, current: Now, q: str = "", limit: int = 50):
    return service.list_restaurants(db, current, q.strip(), min(max(limit, 1), 200))


@router.get("/restaurants/{restaurant_id}/dashboard", summary="(로컬 확장) 점주 대시보드")
def dashboard(restaurant_id: str, db: DB, _user: User, current: Now, days: int = 7):
    return service.dashboard(db, restaurant_id, current, days)
