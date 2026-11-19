"""요기요 장바구니 담기 수동 확인 — 서버 없이 Playwright만 돌려 본다.

요기요 화면이 바뀌어 담기가 깨졌는지 확인할 때 쓴다. 장바구니까지만 채우고 멈추며
요기요의 '주문하기'나 결제는 누르지 않는다. 스크린샷은 --out 폴더(기본 cart_runs/)에 남는다.

준비: uv sync 만 하면 된다. 크로미움 브라우저는 처음 실행할 때 자동으로 받는다 (1~2분).
    (설치된 Chrome을 쓰려면 --chrome)

실행:
    uv run python scripts/yogiyo_cart_try.py --find-open         # 지금 열린 식당을 찾아 담기
    uv run python scripts/yogiyo_cart_try.py --check-only        # 기본 식당 페이지가 열리는지만
    uv run python scripts/yogiyo_cart_try.py                     # 기본 식당·메뉴 담기
(창 없는 --headless 는 요기요가 Access Denied로 막는다)
    uv run python scripts/yogiyo_cart_try.py --restaurant 1016816 \\
        --item 2267040809:1 --expected-subtotal 19000 --expected-fee 1000
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ 를 import 경로에 추가

from app.modules.cart.yogiyo import CartItem, CartOrder, fill_cart_sync  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"


def _fee(restaurant: dict, subtotal: int) -> int | None:
    """주문금액별 배달비 중 subtotal 이하에서 가장 큰 구간의 배달비 (없으면 기본 배달비)."""
    try:
        tiers = json.loads(restaurant["주문금액별배달비"] or "[]")
    except ValueError:
        tiers = []
    ok = [t for t in tiers if t["order_amount"] <= subtotal]
    if ok:
        return max(ok, key=lambda t: t["order_amount"])["delivery_fee"]
    return int(restaurant["기본배달비"]) if restaurant.get("기본배달비") else None


def _candidates():
    """수집 데이터에서 (식당, 담을 메뉴) 후보. 리뷰 많은 식당 먼저.

    메뉴는 필수 옵션 없는 본메뉴(유형 main) 중 가장 싼 것 (사이드·음료·추가토핑 제외).
    """
    with open(DATA / "1_식당.csv", encoding="utf-8-sig") as f:
        restaurants = {r["식당id"]: r for r in csv.DictReader(f)}
    best: dict[str, dict] = {}
    with open(DATA / "2_메뉴.csv", encoding="utf-8-sig") as f:
        for m in csv.DictReader(f):
            if m["유형"] != "main" or m["필수옵션있음"] != "f" or m["품절"] != "f":
                continue
            if m["주류"] != "f":
                continue
            if not m["판매가"].isdigit() or int(m["판매가"]) <= 0:
                continue
            cur = best.get(m["식당id"])
            if cur is None or int(m["판매가"]) < int(cur["판매가"]):
                best[m["식당id"]] = m
    order = sorted(best, key=lambda rid: -int(restaurants[rid]["리뷰수"] or 0))
    return [(restaurants[rid], best[rid]) for rid in order if rid in restaurants]


def _find_open(limit: int, chrome: bool, out: str, headless: bool):
    """지금 요기요에서 주문을 받는 식당을 찾는다 (식당 페이지만 열어 봄)."""
    for n, (r, m) in enumerate(_candidates()[:limit], 1):
        order = CartOrder(restaurant_id=r["식당id"], items=[CartItem(menu_id=m["메뉴id"])])
        res = fill_cart_sync(
            order,
            headless=headless,
            out_dir=out,
            check_only=True,
            browser_channel="chrome" if chrome else None,
            name=f"check-{r['식당id']}",
        )
        print(f"[{n}/{limit}] {r['식당명']} ({r['식당id']}): {res.status} {res.error or ''}")
        if res.error and "Access Denied" in res.error:
            return None  # 접속 자체가 막힘: 다른 식당도 마찬가지
        if res.status == "REACHABLE":
            return r, m
    return None


def _item(text: str) -> CartItem:
    menu, _, qty = text.partition(":")
    return CartItem(menu_id=menu, quantity=int(qty or 1))


def main() -> int:
    p = argparse.ArgumentParser(description="요기요 장바구니 담기 수동 확인 (결제 안 함)")
    p.add_argument("--restaurant", default="1016816")
    p.add_argument("--item", action="append", help="menu_id:수량 (여러 번 가능)")
    p.add_argument("--expected-subtotal", type=int, default=19000)
    p.add_argument("--expected-fee", type=int, default=1000)
    p.add_argument(
        "--headless",
        action="store_true",
        help="창 없이 실행 (요기요가 Access Denied로 막으므로 보통 쓰지 않음)",
    )
    p.add_argument("--headed", action="store_true", help=argparse.SUPPRESS)  # 예전 옵션 (기본값)
    p.add_argument("--keep-open", type=float, default=0, help="끝난 뒤 창을 N초 더 둔다")
    p.add_argument("--check-only", action="store_true", help="식당 페이지가 열리는지만 본다")
    p.add_argument("--chrome", action="store_true", help="설치된 Chrome으로 실행")
    p.add_argument("--out", default="cart_runs")
    p.add_argument(
        "--find-open",
        nargs="?",
        const=20,
        type=int,
        metavar="N",
        help="수집 데이터에서 지금 열린 식당을 최대 N곳(기본 20) 찾아 그 식당으로 담는다",
    )
    a = p.parse_args()

    if a.find_open:
        found = _find_open(a.find_open, a.chrome, a.out, a.headless)
        if found is None:
            print("지금 주문을 받는 식당을 찾지 못했어요 (영업시간 전일 수 있어요).")
            return 1
        r, m = found
        price = int(m["판매가"])
        print(f"→ {r['식당명']} / {m['메뉴명']} {price}원 담기")
        a.restaurant = r["식당id"]
        a.item = [f"{m['메뉴id']}:1"]
        a.expected_subtotal, a.expected_fee = price, _fee(r, price)

    order = CartOrder(
        restaurant_id=a.restaurant,
        items=[_item(t) for t in (a.item or ["2267040809:1"])],
        expected_menu_subtotal_krw=a.expected_subtotal,
        expected_delivery_fee_krw=a.expected_fee,
    )
    r = fill_cart_sync(
        order,
        headless=a.headless,
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
