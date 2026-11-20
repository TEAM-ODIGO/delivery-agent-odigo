"""공용 수집 데이터(요기요 restaurant·menu 테이블) 읽기. B는 읽기만 한다.

A가 고른 후보의 단가를 믿지 않고, B가 여기서 가격·품절·최소주문금액·배달비를 다시 읽어 견적을 만든다.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.discovery.catalog_models import Menu, Restaurant, catalog_version_of


@dataclass
class CatalogMenu:
    menu_id: str
    name: str
    description: str | None
    unit_price_krw: int  # 필수옵션 포함가 (옵션 데이터가 없어 이 값을 단가로 쓴다, A와 같은 기준)
    sold_out: bool
    alcohol: bool


@dataclass
class CatalogRestaurant:
    restaurant_id: str
    name: str
    catalog_version: str
    min_order_krw: int
    base_delivery_fee_krw: int
    delivery_fee_tiers: list[dict]  # [{"order_amount": 13000, "delivery_fee": 4500}, ...]
    local_currency_merchant: bool | None
    menus: dict[str, CatalogMenu]


def read_restaurant(db: Session, restaurant_id: str) -> CatalogRestaurant | None:
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        return None

    menus = {}
    for menu in db.scalars(select(Menu).where(Menu.restaurant_id == restaurant_id)):
        menus[menu.menu_id] = CatalogMenu(
            menu_id=menu.menu_id,
            name=menu.name,
            description=menu.description,
            unit_price_krw=menu.price_with_required_options_krw,
            sold_out=menu.is_sold_out,
            alcohol=menu.is_alcohol,
        )

    return CatalogRestaurant(
        restaurant_id=restaurant.restaurant_id,
        name=restaurant.name,
        catalog_version=catalog_version_of(restaurant.collected_at),
        min_order_krw=restaurant.min_order_krw,
        base_delivery_fee_krw=restaurant.base_delivery_fee_krw,
        delivery_fee_tiers=list(restaurant.delivery_fee_tiers or []),
        local_currency_merchant=restaurant.local_currency_merchant,
        menus=menus,
    )
