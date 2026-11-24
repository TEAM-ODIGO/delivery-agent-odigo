"""cart_job: 요기요 장바구니 담기 작업 1건 (주문 1건에 여러 번 가능, 화면은 최근 것만 본다)."""

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JsonType, KstDateTime


class CartJob(Base):
    __tablename__ = "cart_job"

    job_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("orchestrator_run.request_id"), index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    # QUEUED → RUNNING → CART_READY | CART_MISMATCH | FAILED
    status: Mapped[str] = mapped_column(String(20), index=True)
    restaurant_id: Mapped[str] = mapped_column(String(64))
    # 담으려는 것 (B-06 승인 대상 견적 기준): items, menu_subtotal_krw, delivery_fee_krw
    expected: Mapped[dict[str, Any]] = mapped_column(JsonType)
    # 요기요에 실제로 담긴 것
    cart_items: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)
    menu_subtotal_krw: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_fee_krw: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mismatches: Mapped[list[str]] = mapped_column(JsonType, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    yogiyo_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    mode: Mapped[str] = mapped_column(String(8))  # real | fake | remote(PC 작업기)
    created_at: Mapped[datetime] = mapped_column(KstDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(KstDateTime, nullable=True)
