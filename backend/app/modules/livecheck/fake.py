"""가짜 확인 (LIVE_CHECK_MODE=fake): 요기요에 접속하지 않고 후보 데이터대로 열린 것처럼 만든다.

주문 문장의 태그로 다른 결과를 시연한다 (장바구니의 #요기요실패와 같은 방식).
- #요기요닫힘     : 1순위 후보 식당이 서비스 미제공
- #요기요가격변경 : 1순위 후보의 첫 메뉴가 500원 비쌈
- #요기요전부닫힘 : 모든 후보 식당이 서비스 미제공
"""

from app.modules.livecheck.checker import CheckTarget


def fake_page(target: CheckTarget, *, rank: int, message: str = "") -> dict:
    url = f"https://www.yogiyo.co.kr/mobile/#/{target.restaurant_id}/"
    closed = "#요기요전부닫힘" in message or ("#요기요닫힘" in message and rank == 0)
    if closed:
        return {"url": url, "denied": False, "not_serving": True, "redirected": False, "menus": []}
    menus = []
    for i, it in enumerate(target.items):
        price = it.expected_price_krw or 0
        if "#요기요가격변경" in message and rank == 0 and i == 0:
            price += 500
        menus.append({"id": str(it.menu_id), "text": f"{it.name or it.menu_id} {price:,}원"})
    return {
        "url": url,
        "denied": False,
        "not_serving": False,
        "redirected": False,
        "menus": menus,
        "delivery_fee_krw": target.expected_delivery_fee_krw,
        "min_order_krw": target.expected_min_order_krw,
    }
