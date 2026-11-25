"""요기요 실시간 확인 remote 모드 (이슈 #80): 한국 PC 작업기가 후보 식당을 대신 확인한다.

배포 서버(해외 클라우드 IP)는 요기요가 Access Denied로 막는다. 장바구니 담기(#75)처럼
서버는 확인 작업을 쌓아 두고, PC 작업기(scripts/cart_worker.py)가 가져가 확인한 뒤 결과를 올린다.

- 서버 → 작업기 (claim): 후보별 확인 대상과, 서버가 이미 최근에 확인한 식당의 판정(cached)
- 작업기 → 서버 (result): 후보별 판정과 식당 화면 원본(page). 서버는 원본을 저장하고 판정은 읽을 때 다시 한다.
이 파일의 함수는 작업기에서도 돈다 (DB를 쓰지 않는다).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from app.modules.livecheck import checker
from app.modules.livecheck.checker import CheckItem, CheckResult, CheckTarget


def target_to_json(t: CheckTarget) -> dict:
    return asdict(t)


def target_from_json(d: dict) -> CheckTarget:
    return CheckTarget(
        restaurant_id=str(d["restaurant_id"]),
        items=[CheckItem(str(i["menu_id"]), i.get("expected_price_krw"), i.get("name")) for i in d.get("items") or []],
        expected_delivery_fee_krw=d.get("expected_delivery_fee_krw"),
        expected_min_order_krw=d.get("expected_min_order_krw"),
        name=d.get("name"),
    )


async def check_on_browser(
    browser,
    targets: list[CheckTarget],
    *,
    cached: dict[int, dict] | None = None,
    concurrency: int = 3,
    want: int = 3,
    timeout_seconds: float = 8,
) -> list[CheckResult]:
    """작업기의 브라우저에 확인용 창을 하나 열어 후보를 순위대로 확인하고, 끝나면 그 창만 닫는다.

    cached: {후보 순번: {"status", "reason"}} 서버가 최근에 확인한 식당은 다시 열지 않는다.
    같은 식당이 두 후보에 있으면 한 번만 연다.
    """
    from app.modules.cart.yogiyo import DEMO_LOCATION, _init_script

    cached = cached or {}
    order = {id(t): i for i, t in enumerate(targets)}
    loc = json.loads(DEMO_LOCATION.read_text(encoding="utf-8"))
    context = await browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 900})
    try:
        await context.add_init_script(_init_script(loc))
        check = checker.browser_checker(context, timeout_ms=int(timeout_seconds * 1000))
        seen: dict[str, CheckResult] = {}
        locks: dict[str, asyncio.Lock] = {}

        async def one(t: CheckTarget) -> CheckResult:
            c = cached.get(order[id(t)])
            if c:
                return CheckResult(t.restaurant_id, c["status"], [], c.get("reason"), cached=True)
            lock = locks.setdefault(t.restaurant_id, asyncio.Lock())
            async with lock:
                prev = seen.get(t.restaurant_id)
                if prev is not None and prev.page is not None:  # 같은 식당: 원본으로 이 후보 기준 판정만
                    status, changes, reason = checker.judge(t, checker.observed_for(prev.page, t))
                    return CheckResult(t.restaurant_id, status, changes, reason, page=prev.page)
                r = await check(t)
                seen[t.restaurant_id] = r
                return r

        return await checker.run_checks(targets, one, concurrency=concurrency, want=want)
    finally:
        try:
            await context.close()
        except Exception:  # noqa: BLE001 — 사용자가 창을 먼저 닫은 경우
            pass


def result_to_json(index: int, r: CheckResult) -> dict:
    return {
        "index": index,
        "restaurant_id": r.restaurant_id,
        "status": r.status,
        "reason": r.reason,
        "page": r.page,
        "seconds": round(r.seconds or 0, 2),
        "cached": bool(r.cached),
    }
