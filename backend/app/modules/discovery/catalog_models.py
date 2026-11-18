"""요기요 수집 데이터 (공용 읽기 전용 테이블).

공통 규칙: restaurant·menu·option 테이블은 수집기만 쓰고 A·B·C는 읽기만 한다.
예외로 restaurant의 local_currency_merchant·local_currency_basis·local_currency_checked_at은
C-08만 쓴다(MVP 1은 수집기가 요기요 '지원금 현장결제 가능' 값으로 추정해 채움).

option 테이블은 이번 수집 CSV에 옵션 데이터가 없어 만들지 않았다.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JsonType as JSONB, KstDateTime


class Restaurant(Base):
    __tablename__ = "restaurant"

    restaurant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    collected_categories: Mapped[list[str]] = mapped_column(JSONB)  # 수집카테고리 (chicken, pizza ...)
    yogiyo_categories: Mapped[list[str]] = mapped_column(JSONB)  # 요기요카테고리 (치킨, 피자/양식 ...)
    rating: Mapped[float] = mapped_column(Numeric(3, 1))
    review_count: Mapped[int] = mapped_column(Integer)
    adjusted_rating: Mapped[float] = mapped_column(Numeric(4, 3))  # 보정별점
    min_order_krw: Mapped[int] = mapped_column(Integer)
    base_delivery_fee_krw: Mapped[int] = mapped_column(Integer)
    # [{"order_amount": 12000, "delivery_fee": 1000}, ...] 주문금액 이상이면 그 배달비
    delivery_fee_tiers: Mapped[list[dict]] = mapped_column(JSONB)
    delivery_minutes_min: Mapped[int | None] = mapped_column(Integer)
    delivery_minutes_max: Mapped[int | None] = mapped_column(Integer)
    distance_m: Mapped[int | None] = mapped_column(Integer)  # 수집 기준점(을지로입구역)에서의 거리
    accepts_livelihood_voucher: Mapped[bool] = mapped_column(Boolean)  # 요기요 원본 '지원금 현장결제 가능'
    local_currency_merchant: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    local_currency_basis: Mapped[str | None] = mapped_column(String(40), nullable=True)
    local_currency_checked_at: Mapped[datetime | None] = mapped_column(KstDateTime(), nullable=True)
    menu_count: Mapped[int] = mapped_column(Integer)
    address: Mapped[str | None] = mapped_column(Text)
    yogiyo_url: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime | None] = mapped_column(KstDateTime())


class Menu(Base):
    __tablename__ = "menu"

    menu_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurant.restaurant_id"), index=True)
    menu_category: Mapped[str | None] = mapped_column(String(200))  # 메뉴분류 (가게가 정한 묶음 이름)
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    list_price_krw: Mapped[int] = mapped_column(Integer)  # 정가
    sale_price_krw: Mapped[int] = mapped_column(Integer)  # 판매가
    price_with_required_options_krw: Mapped[int] = mapped_column(Integer)  # 필수옵션포함가
    has_required_options: Mapped[bool] = mapped_column(Boolean)
    menu_type: Mapped[str] = mapped_column(String(20))  # main·side·set·drink·alcohol
    spice_level: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0~3, 없으면 정보 없음
    has_soup: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    servings: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)  # 인분
    is_signature: Mapped[bool] = mapped_column(Boolean)
    is_popular: Mapped[bool] = mapped_column(Boolean)
    is_sold_out: Mapped[bool] = mapped_column(Boolean)
    is_alcohol: Mapped[bool] = mapped_column(Boolean)
    review_count: Mapped[int] = mapped_column(BigInteger)


def catalog_version_of(collected_at: datetime | None) -> str:
    """수집 시각으로 판본 이름을 만든다. 예: 2026-11-11 12:38:18 → YGY-20261111123818
    같은 CSV를 다시 넣으면 같은 판본, 새로 수집하면 새 판본. (B가 견적을 낼 때 쓴다)"""
    if collected_at is None:
        return "YGY-UNKNOWN"
    return "YGY-" + collected_at.strftime("%Y%m%d%H%M%S")
