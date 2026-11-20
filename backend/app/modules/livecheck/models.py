"""restaurant_live_status: 식당별 최근 요기요 확인 결과 (식당 하나 = 행 하나, 확인할 때마다 덮어씀).

주문마다 쌓지 않는다. 행 수는 수집한 식당 수를 넘지 않고, 이 행이 곧 캐시다.
후보별 판정은 저장하지 않고 읽을 때 `page`(식당 화면 원본)로 다시 계산한다.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JsonType, KstDateTime


class RestaurantLiveStatus(Base):
    __tablename__ = "restaurant_live_status"

    restaurant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    # 식당 화면 상태: OPEN / NOT_SERVING / REDIRECTED / BLOCKED / ERROR
    page_status: Mapped[str] = mapped_column(String(20))
    # 식당 화면 원본: url, 메뉴 카드(id·글자), 배달비, 최소 주문 금액
    page: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(10))  # fake | real
    checking_since: Mapped[datetime | None] = mapped_column(KstDateTime, nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(KstDateTime, nullable=True)
