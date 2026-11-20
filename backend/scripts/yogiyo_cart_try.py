"""요기요 장바구니 담기 수동 확인 — 서버 없이 Playwright만 돌려 본다.

요기요 화면이 바뀌어 담기가 깨졌는지 확인할 때 쓴다. 장바구니까지만 채우고 멈추며
요기요의 '주문하기'나 결제는 누르지 않는다. 스크린샷은 --out 폴더(기본 cart_runs/)에 남는다.

준비: uv sync 만 하면 된다. 크로미움 브라우저는 처음 실행할 때 자동으로 받는다 (1~2분).
    (설치된 Chrome을 쓰려면 --chrome)

실행:
    uv run python scripts/yogiyo_cart_try.py --find-open         # 지금 열린 식당을 찾아 담기
    uv run python scripts/yogiyo_cart_try.py --check             # 상위 6곳 확인만 (담지 않음)
    uv run python scripts/yogiyo_cart_try.py --check 20 --want 20  # 20곳 모두 확인 (실측용)
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
from app.modules.livecheck.checker import (  # noqa: E402
    CheckItem,
    CheckTarget,
    check_restaurants_sync,
)

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


def _int(v: str | None) -> int | None:
    return int(v) if v and v.isdigit() else None


def _run_check(n: int, want: int, concurrency: int, chrome: bool, out: str, headless: bool) -> int:
    """수집 데이터 상위 n곳을 요기요에서 확인만 한다 (장바구니에 담지 않음). 실측 기록을 남긴다."""
    import time
    from datetime import datetime

    picked = _candidates()[:n]
    targets = [
        CheckTarget(
            restaurant_id=r["식당id"],
            name=r["식당명"],
            items=[CheckItem(m["메뉴id"], _int(m["판매가"]), m["메뉴명"])],
            expected_delivery_fee_krw=_int(r.get("기본배달비")),
            expected_min_order_krw=_int(r.get("최소주문금액")),
        )
        for r, m in picked
    ]
    print(f"{len(targets)}곳 확인 (동시 {concurrency}탭, 통과 {want}곳이면 멈춤)")

    def show(i: int, res) -> None:
        t = targets[i]
        detail = "; ".join(res.changes) or res.reason or ""
        print(f"[{i + 1}] {t.name} ({t.restaurant_id}): {res.status} {res.seconds:.1f}초 {detail}")

    started = time.monotonic()
    results = check_restaurants_sync(
        targets,
        concurrency=concurrency,
        want=want,
        headless=headless,
        browser_channel="chrome" if chrome else None,
        on_result=show,
    )
    total = time.monotonic() - started
    counts: dict[str, int] = {}
    for res in results:
        counts[res.status] = counts.get(res.status, 0) + 1
    print(f"끝: {total:.1f}초 · " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))

    Path(out).mkdir(parents=True, exist_ok=True)
    path = Path(out) / f"check-{datetime.now():%Y%m%d-%H%M}.json"
    path.write_text(
        json.dumps(
            {
                "at": datetime.now().isoformat(timespec="seconds"),
                "total_seconds": round(total, 1),
                "concurrency": concurrency,
                "want": want,
                "results": [
                    {"name": t.name, **json.loads(res.to_json())}
                    for t, res in zip(targets, results, strict=True)
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"기록: {path}")
    return 0 if any(res.status in ("OK", "CHANGED") for res in results) else 1


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
    p.add_argument(
        "--check",
        nargs="?",
        const=6,
        type=int,
        metavar="N",
        help="수집 데이터 상위 N곳(기본 6)이 지금 주문 가능한지 확인만 한다 (담지 않음)",
    )
    p.add_argument("--want", type=int, default=3, help="--check: 통과가 이만큼이면 멈춘다")
    p.add_argument("--concurrency", type=int, default=3, help="--check: 동시에 여는 탭 수")
    a = p.parse_args()

    if a.check:
        return _run_check(a.check, a.want, a.concurrency, a.chrome, a.out, a.headless)

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
