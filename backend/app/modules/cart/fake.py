"""가짜 요기요 장바구니. 견적을 그대로 담은 것처럼 답한다 (Playwright·네트워크 없이).

주문 문장의 태그로 다른 결과를 낸다.
- #요기요불일치: 요기요 메뉴 합계가 견적보다 500원 비싼 것처럼 → CART_MISMATCH
- #요기요실패:   식당 페이지를 못 연 것처럼 → FAILED
"""

from app.modules.cart.yogiyo import CartOrder, CartResult, _compare

TAG_MISMATCH = "#요기요불일치"
TAG_FAIL = "#요기요실패"


def fake_fill(order: CartOrder, *, message: str = "", **_: object) -> CartResult:
    if TAG_FAIL in message:
        return CartResult(
            status="FAILED",
            restaurant_id=order.restaurant_id,
            error="TimeoutError: 식당 페이지를 열지 못함",
        )
    items = [
        {
            "menu_id": it.menu_id,
            "name": it.name,
            "unit_price_krw": it.unit_price_krw,
            "quantity": it.quantity,
            "options": [],
        }
        for it in order.items
    ]
    subtotal = sum((i["unit_price_krw"] or 0) * i["quantity"] for i in items)
    if TAG_MISMATCH in message and items:
        items[0]["unit_price_krw"] = (items[0]["unit_price_krw"] or 0) + 500
        subtotal += 500 * items[0]["quantity"]
    r = CartResult(
        status="CART_READY",
        restaurant_id=order.restaurant_id,
        cart_items=items,
        menu_subtotal_krw=subtotal,
        delivery_fee_krw=order.expected_delivery_fee_krw,
        url=f"https://www.yogiyo.co.kr/mobile/#/{order.restaurant_id}/",
    )
    r.mismatches = _compare(order, r)
    r.status = "CART_MISMATCH" if r.mismatches else "CART_READY"
    return r
