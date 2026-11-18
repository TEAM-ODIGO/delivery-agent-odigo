"""요기요 장바구니 담기 수동 확인 — 서버 없이 Playwright만 돌려 본다.

요기요 화면이 바뀌어 담기가 깨졌는지 확인할 때 쓴다. 장바구니까지만 채우고 멈추며
요기요의 '주문하기'나 결제는 누르지 않는다. 스크린샷은 --out 폴더(기본 cart_runs/)에 남는다.

준비 (한 번만, backend 폴더 안에서):
    uv sync --extra yogiyo
    uv run playwright install chromium        # 또는 --chrome 으로 설치된 Chrome 사용

실행:
    uv run python scripts/yogiyo_cart_try.py --check-only        # 식당 페이지가 열리는지만
    uv run python scripts/yogiyo_cart_try.py --headed            # 기본 식당·메뉴 담기 (창 보임)
    uv run python scripts/yogiyo_cart_try.py --restaurant 1016816 \\
        --item 2267040809:1 --expected-subtotal 19000 --expected-fee 1000
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ 를 import 경로에 추가

from app.modules.cart.yogiyo import CartItem, CartOrder, fill_cart_sync  # noqa: E402


def _item(text: str) -> CartItem:
    menu, _, qty = text.partition(":")
    return CartItem(menu_id=menu, quantity=int(qty or 1))


def main() -> int:
    p = argparse.ArgumentParser(description="요기요 장바구니 담기 수동 확인 (결제 안 함)")
    p.add_argument("--restaurant", default="1016816")
    p.add_argument("--item", action="append", help="menu_id:수량 (여러 번 가능)")
    p.add_argument("--expected-subtotal", type=int, default=19000)
    p.add_argument("--expected-fee", type=int, default=1000)
    p.add_argument("--headed", action="store_true", help="브라우저 창을 띄운다")
    p.add_argument("--keep-open", type=float, default=0, help="--headed 일 때 창을 N초 더 둔다")
    p.add_argument("--check-only", action="store_true", help="식당 페이지가 열리는지만 본다")
    p.add_argument("--chrome", action="store_true", help="설치된 Chrome으로 실행")
    p.add_argument("--out", default="cart_runs")
    a = p.parse_args()

    order = CartOrder(
        restaurant_id=a.restaurant,
        items=[_item(t) for t in (a.item or ["2267040809:1"])],
        expected_menu_subtotal_krw=a.expected_subtotal,
        expected_delivery_fee_krw=a.expected_fee,
    )
    r = fill_cart_sync(
        order,
        headless=not a.headed,
        out_dir=a.out,
        check_only=a.check_only,
        browser_channel="chrome" if a.chrome else None,
        keep_open_seconds=a.keep_open,
        name=f"try-{a.restaurant}",
    )
    print(json.dumps(r.__dict__, ensure_ascii=False, indent=2, default=str))
    return 0 if r.status in ("CART_READY", "REACHABLE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
