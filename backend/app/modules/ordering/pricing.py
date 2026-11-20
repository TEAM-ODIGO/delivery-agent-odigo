"""견적 계산 (메뉴 합계·배달비). B는 금융 할인을 계산하지 않는다 (C-05가 계산)."""

from dataclasses import dataclass, field

from app.modules.ordering.catalog import CatalogRestaurant


@dataclass
class PricedItem:
    menu_id: str
    name: str
    description: str | None
    quantity: int
    unit_price_krw: int
    options: list[dict] = field(default_factory=list)  # [{"option_id", "price_krw"}]

    @property
    def line_total_krw(self) -> int:
        option_total = 0
        for option in self.options:
            option_total += option["price_krw"]
        return (self.unit_price_krw + option_total) * self.quantity

    def to_dict(self) -> dict:
        return {
            "menu_id": self.menu_id,
            "name": self.name,
            "description": self.description,
            "quantity": self.quantity,
            "unit_price_krw": self.unit_price_krw,
            "options": list(self.options),
        }


@dataclass
class PriceResult:
    items: list[PricedItem]
    issues: list[dict]  # 비어 있으면 주문 가능
    menu_subtotal_krw: int = 0
    delivery_fee_krw: int = 0

    @property
    def orderable(self) -> bool:
        return not self.issues

    @property
    def pre_financial_amount_krw(self) -> int:
        return self.menu_subtotal_krw + self.delivery_fee_krw


def delivery_fee_for(restaurant: CatalogRestaurant, subtotal_krw: int) -> int | None:
    """요기요 주문금액별 배달비: 주문금액 이상인 구간 중 가장 높은 구간의 배달비.
    최소주문금액·최소 구간에 못 미치면 None (주문 불가)."""
    if subtotal_krw < restaurant.min_order_krw:
        return None
    tiers = sorted(restaurant.delivery_fee_tiers, key=lambda tier: tier["order_amount"], reverse=True)
    for tier in tiers:
        if subtotal_krw >= tier["order_amount"]:
            return int(tier["delivery_fee"])
    if not tiers:
        return restaurant.base_delivery_fee_krw
    return None


def issue(code: str, message: str, **extra) -> dict:
    body = {"code": code, "message": message}
    body.update(extra)
    return body


def price_items(restaurant: CatalogRestaurant, items: list[dict]) -> PriceResult:
    """items: [{"menu_id", "option_ids", "quantity"}] → 메뉴별 단가·합계·배달비, 주문 불가 사유."""
    priced = []
    issues = []

    for index, item in enumerate(items):
        menu = restaurant.menus.get(item["menu_id"])
        if menu is None:
            issues.append(issue("MENU_NOT_FOUND", "메뉴가 더 이상 없습니다.", menu_id=item["menu_id"], line=index))
            continue
        if menu.sold_out:
            issues.append(issue("MENU_SOLD_OUT", f"'{menu.name}'이(가) 품절입니다.", menu_id=menu.menu_id, line=index))
            continue
        if menu.alcohol:
            issues.append(issue("ALCOHOL_NOT_ALLOWED", "주류는 대리 주문할 수 없습니다.", menu_id=menu.menu_id, line=index))
            continue
        # 수집 데이터에 옵션 표가 아직 없다 → 옵션 ID가 오면 확인할 수 없으니 주문 불가로 본다
        if item.get("option_ids"):
            issues.append(issue(
                "OPTION_NOT_FOUND", "옵션 정보를 확인할 수 없습니다.",
                menu_id=menu.menu_id, option_ids=list(item["option_ids"]), line=index,
            ))
            continue
        priced.append(PricedItem(
            menu_id=menu.menu_id,
            name=menu.name,
            description=menu.description,
            quantity=item["quantity"],
            unit_price_krw=menu.unit_price_krw,
        ))

    result = PriceResult(items=priced, issues=issues)
    if issues:
        return result

    subtotal = 0
    for priced_item in priced:
        subtotal += priced_item.line_total_krw
    fee = delivery_fee_for(restaurant, subtotal)
    if fee is None:
        result.issues.append(issue(
            "BELOW_MIN_ORDER",
            f"최소주문금액 {restaurant.min_order_krw:,}원보다 적습니다.",
            min_order_krw=restaurant.min_order_krw,
            menu_subtotal_krw=subtotal,
        ))
        return result

    result.menu_subtotal_krw = subtotal
    result.delivery_fee_krw = fee
    return result
