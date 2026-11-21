"""요기요 실시간 식당 확인 도구 (이슈 ①).

후보 식당의 요기요 페이지를 열어 네 가지를 읽고 판정한다. 장바구니에는 담지 않는다.
- 영업 여부: "요기요 서비스가 제공되지 않습니다" 문구
- 후보 메뉴의 존재·현재 가격·품절: 메뉴 카드(`select_menuitem`)의 메뉴 id와 카드 글자
- 배달비: 주문표 영역의 "배달요금 N원"
- 최소 주문 금액: 식당 정보의 "최소주문금액 N원"

판정: OK / CHANGED(가격·배달비·최소 주문 금액만 다름) / UNAVAILABLE(서비스 미제공·메뉴 없음·품절)
      / BLOCKED(Access Denied) / ERROR(시간 안에 못 읽음) / SKIPPED(조기 종료·차단으로 확인 안 함)

- 탭을 최대 concurrency개 동시에 연다. 순서는 후보 순위 그대로.
- 통과(OK·CHANGED)가 want곳이 되면 나머지는 확인하지 않는다 (SKIPPED).
- BLOCKED가 나오면 나머지도 막힐 것이므로 멈춘다. ERROR는 한 번 다시 시도한다.
- 창 없는(headless) 브라우저는 요기요가 막는다 (우회하지 않음). 기본은 창을 띄운다.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from app.modules.cart.yogiyo import BASE, DEMO_LOCATION, _init_script, _launch, _won

PASSED = ("OK", "CHANGED")

# 페이지 상태를 한 번에 읽는다: 메뉴 수, 서비스 미제공, 접속 차단
_PAGE_STATE = """() => {
  const body = document.body ? document.body.innerText : '';
  return {
    menus: document.querySelectorAll('[ng-click^="select_menuitem"]').length,
    notServing: /요기요\\s*서비스가\\s*제공되지/.test(body),
    denied: /Access Denied/i.test(document.title + ' ' + body),
    url: location.href,
  };
}"""
# 메뉴 카드: 메뉴 id와 카드 글자. '요기서결제시 할인' 묶음의 중복 카드는 뺀다
_READ_MENUS = """() => [...document.querySelectorAll('[ng-click^="select_menuitem"]')]
  .map(e => {
    let id = null;
    try { id = String(angular.element(e).scope().item_id); } catch (err) {}
    return {id, text: (e.innerText || '').replace(/\\s+/g, ' ').trim()};
  })
  .filter(m => m.id && !m.text.includes('요기서결제시'))"""
_PAGE_TEXT = """() => document.body ? document.body.innerText : ''"""
# 페이지 데이터(angular scope)의 식당 영업 상태: 임시 휴무·휴무일·영업시간 밖·일시 정지 등
_OPEN_STATUS = """() => {
  for (const e of [...document.querySelectorAll('.ng-scope')].slice(0, 400)) {
    let s = null;
    try { s = angular.element(e).scope(); } catch (err) {}
    const r = s && s.restaurant;
    if (r && r.open_status) {
      const o = r.open_status;
      return {current: o.current_open_status || null,
              next_opening_at: o.next_opening_start_at || null,
              open_time: r.open_time_description || null};
    }
  }
  return null;
}"""
_CART_TEXT = """() => (document.querySelector('.cart') || {innerText: ''}).innerText"""


@dataclass
class CheckItem:
    menu_id: str
    expected_price_krw: int | None = None
    name: str | None = None


@dataclass
class CheckTarget:
    restaurant_id: str
    items: list[CheckItem]
    expected_delivery_fee_krw: int | None = None
    expected_min_order_krw: int | None = None
    name: str | None = None


@dataclass
class Observed:
    """요기요에서 읽은 값 (판정 전 원본)."""

    url: str | None = None
    not_serving: bool = False
    denied: bool = False
    redirected: bool = False  # 식당 페이지 대신 첫 화면으로 돌아감 (배달 지역 밖·영업 종료 등)
    open_status: dict | None = None  # 요기요 영업 상태 {current, next_opening_at, open_time}
    menus_total: int = 0
    items: dict[str, dict] = field(
        default_factory=dict
    )  # menu_id → {found, price_krw, sold_out, text}
    delivery_fee_krw: int | None = None
    min_order_krw: int | None = None


@dataclass
class CheckResult:
    restaurant_id: str
    status: str
    changes: list[str] = field(default_factory=list)
    reason: str | None = None
    observed: dict | None = None
    seconds: float = 0.0
    attempts: int = 0
    page: dict | None = None  # 식당 화면에서 읽은 원본 (메뉴 카드 전체). 저장·재판정용
    cached: bool = False  # 최근 확인 결과를 다시 씀

    def to_json(self) -> str:
        data = asdict(self)
        data.pop("page", None)  # 메뉴 카드 전체는 길어서 기록 파일에는 넣지 않는다
        return json.dumps(data, ensure_ascii=False)


# ------------------------------------------ 읽은 값 해석 (페이지 없이 시험 가능)
def parse_menu_card(text: str) -> dict:
    """메뉴 카드 글자 → 가격(마지막 'N원')과 품절 여부."""
    prices = re.findall(r"([\d,]+)\s*원", text)
    price = int(prices[-1].replace(",", "")) if prices else None
    return {"price_krw": price, "sold_out": "품절" in text}


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def read_items(menus: list[dict], wanted: list[CheckItem]) -> dict[str, dict]:
    """후보 메뉴를 메뉴 번호로 찾고, 없으면 이름(카드 글자가 이름으로 시작)으로 한 번 더 찾는다."""
    by_id: dict[str, dict] = {}
    for m in menus:
        by_id.setdefault(str(m["id"]), m)  # 같은 메뉴가 여러 번 나오면 첫 카드
    out = {}
    for it in wanted:
        m = by_id.get(str(it.menu_id))
        new_id = None
        if m is None and it.name:
            m = next((c for c in menus if _norm(c["text"]).startswith(_norm(it.name))), None)
            new_id = str(m["id"]) if m else None
        if m is None:
            out[str(it.menu_id)] = {"found": False}
            continue
        got = {"found": True, **parse_menu_card(m["text"]), "text": m["text"][:200]}
        if new_id:
            got["new_id"] = new_id  # 이름으로 찾음: 요기요 메뉴 번호가 바뀌었다
        out[str(it.menu_id)] = got
    return out


def delivery_fee(cart_text: str, page_text: str) -> int | None:
    """주문표의 '배달요금 N원'. '배달요금 무료'는 0원. 주문표에 없으면 페이지 전체에서 찾는다."""
    for text in (cart_text or "", page_text or ""):
        if re.search(r"배달요금\s*무료", text):
            return 0
        fee = _won(text, "배달요금")
        if fee is not None:
            return fee
    return None


# 요기요 영업 상태(open_status.current_open_status) → 주문 불가 사유. OPENING_TIME이 영업 중
CLOSED_STATUS = {
    "TEMP_HOLIDAY": "오늘 임시 휴무예요",
    "HOLIDAY": "오늘은 정기 휴무일이에요",
    "CLOSED": "지금은 영업시간이 아니에요",
    "PAUSE": "가게가 잠시 주문을 받지 않아요",
    "BREAK_TIME": "지금은 브레이크타임이에요",
    "PREPARING": "영업 준비 중이에요",
}
KST = timezone(timedelta(hours=9))


def _opening_at(value: str | None, now: datetime) -> str | None:
    """'2026-11-21T08:00:00.000Z' → '17:00' (오늘) / '내일 11:00' / '11/23 11:00'."""
    if not value:
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(KST)
    except ValueError:
        return None
    today = now.astimezone(KST).date()
    hm = at.strftime("%H:%M")
    if at.date() == today:
        return hm
    if at.date() == today + timedelta(days=1):
        return f"내일 {hm}"
    return f"{at.month}/{at.day} {hm}"


def closed_reason(open_status: dict | None, now: datetime | None = None) -> str | None:
    """요기요 영업 상태가 '영업 중'이 아니면 그 사유. 모르는 상태나 영업 중이면 None."""
    if not open_status:
        return None
    reason = CLOSED_STATUS.get(open_status.get("current") or "")
    if reason is None:
        return None
    when = _opening_at(open_status.get("next_opening_at"), now or datetime.now(KST))
    return f"{reason} ({when}에 영업 시작)" if when else reason


def _krw(n: int | None) -> str:
    return f"{n:,}원" if n is not None else "?"


def judge(target: CheckTarget, obs: Observed) -> tuple[str, list[str], str | None]:
    """요기요에서 읽은 값 → (판정, 바뀐 점, 불가 사유)."""
    if obs.denied:
        return "BLOCKED", [], "요기요가 접속을 막았어요 (Access Denied)"
    closed = closed_reason(obs.open_status)
    if obs.not_serving or closed:
        return "UNAVAILABLE", [], closed or "지금 요기요 주문을 받지 않아요 (서비스 미제공)"
    if obs.redirected:
        return (
            "UNAVAILABLE",
            [],
            "요기요에서 식당 페이지가 열리지 않아요 (배달 지역 밖이거나 영업 종료)",
        )
    if obs.menus_total == 0:
        return "ERROR", [], "메뉴를 읽지 못했어요"
    changes: list[str] = []
    for it in target.items:
        got = obs.items.get(str(it.menu_id)) or {"found": False}
        label = it.name or f"메뉴 {it.menu_id}"
        if not got.get("found"):
            return "UNAVAILABLE", [], f"{label}이(가) 요기요 메뉴에 없어요"
        if got.get("sold_out"):
            return "UNAVAILABLE", [], f"{label} 품절"
        if got.get("new_id"):
            changes.append(f"{label} 메뉴 번호 바뀜 ({it.menu_id} → {got['new_id']})")
        price = got.get("price_krw")
        if (
            it.expected_price_krw is not None
            and price is not None
            and price != it.expected_price_krw
        ):
            changes.append(f"{label} {_krw(it.expected_price_krw)} → {_krw(price)}")
    fee, want_fee = obs.delivery_fee_krw, target.expected_delivery_fee_krw
    if want_fee is not None and fee is not None and fee != want_fee:
        changes.append(f"배달비 {_krw(want_fee)} → {_krw(fee)}")
    mo, want_mo = obs.min_order_krw, target.expected_min_order_krw
    if want_mo is not None and mo is not None and mo != want_mo:
        changes.append(f"최소 주문 금액 {_krw(want_mo)} → {_krw(mo)}")
    return ("CHANGED" if changes else "OK"), changes, None


# ---------------------------------------------------------------- 브라우저로 한 곳 확인
async def observe_page(
    page, restaurant_id: str, *, base_url: str = BASE, timeout_ms: int = 8000
) -> dict:
    """식당 화면 하나를 읽는다. 후보와 상관없는 값이라 저장해 두고 후보마다 다시 판정할 수 있다.

    서비스 미제공·차단·첫 화면으로 돌아감은 보이는 즉시 끝낸다.
    """
    await page.goto(f"{base_url}{restaurant_id}/", wait_until="domcontentloaded")
    deadline = time.monotonic() + timeout_ms / 1000
    here = f"/{restaurant_id}/"
    state: dict = {}
    while True:
        state = await page.evaluate(_PAGE_STATE)
        if state["denied"] or state["notServing"] or state["menus"] > 0:
            break
        if here not in (state.get("url") or ""):
            break  # 첫 화면으로 돌아감: 기다려도 메뉴가 뜨지 않는다
        if time.monotonic() >= deadline:
            break
        await page.wait_for_timeout(250)
    url = state.get("url")
    obs = {
        "url": url,
        "denied": state["denied"],
        "not_serving": state["notServing"],
        "redirected": bool(url) and here not in url and not state["denied"],
        "menus": [],
        "delivery_fee_krw": None,
        "min_order_krw": None,
        "open_status": None,
    }
    if not obs["denied"] and not obs["redirected"]:
        try:
            obs["open_status"] = await page.evaluate(_OPEN_STATUS)
        except Exception:  # noqa: BLE001 — 못 읽어도 화면 글자로 판정한다
            obs["open_status"] = None
    if state["menus"] > 0 and not obs["not_serving"] and not obs["redirected"]:
        obs["menus"] = await page.evaluate(_READ_MENUS)
        page_text = await page.evaluate(_PAGE_TEXT)
        obs["delivery_fee_krw"] = delivery_fee(await page.evaluate(_CART_TEXT), page_text)
        obs["min_order_krw"] = _won(page_text, "최소주문금액")
    return obs


def observed_for(page_obs: dict, target: CheckTarget) -> Observed:
    """식당 화면 원본 → 이 후보 기준으로 읽은 값."""
    menus = page_obs.get("menus") or []
    return Observed(
        url=page_obs.get("url"),
        not_serving=bool(page_obs.get("not_serving")),
        denied=bool(page_obs.get("denied")),
        redirected=bool(page_obs.get("redirected")),
        open_status=page_obs.get("open_status"),
        menus_total=len(menus),
        items=read_items(menus, target.items) if menus else {},
        delivery_fee_krw=page_obs.get("delivery_fee_krw"),
        min_order_krw=page_obs.get("min_order_krw"),
    )


async def check_page(page, target: CheckTarget, *, base_url: str = BASE, timeout_ms: int = 8000):
    """열린 탭에서 식당 하나를 확인한다 (이 후보 기준으로 읽은 값)."""
    page_obs = await observe_page(
        page, target.restaurant_id, base_url=base_url, timeout_ms=timeout_ms
    )
    return observed_for(page_obs, target)


Checker = Callable[[CheckTarget], Awaitable[CheckResult]]


def browser_checker(context, *, base_url: str = BASE, timeout_ms: int = 8000) -> Checker:
    async def check(target: CheckTarget) -> CheckResult:
        started = time.monotonic()
        page = await context.new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
        page_obs = None
        try:
            page_obs = await observe_page(
                page, target.restaurant_id, base_url=base_url, timeout_ms=timeout_ms
            )
            obs = observed_for(page_obs, target)
            status, changes, reason = judge(target, obs)
            observed = asdict(obs)
        except Exception as exc:  # noqa: BLE001 — 한 곳의 오류가 전체를 멈추지 않게
            status, changes, reason, observed = "ERROR", [], f"{type(exc).__name__}: {exc}", None
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
        return CheckResult(
            target.restaurant_id,
            status,
            changes,
            reason,
            observed,
            time.monotonic() - started,
            page=page_obs,
        )

    return check


# ------------------------------------------ 여러 곳 확인 (동시 N탭, 조기 종료)
async def run_checks(
    targets: list[CheckTarget],
    checker: Checker,
    *,
    concurrency: int = 3,
    want: int = 3,
    on_result: Callable[[int, CheckResult], None] | None = None,
) -> list[CheckResult]:
    """후보 순서대로 확인. 결과 목록은 targets와 같은 순서."""
    results: list[CheckResult | None] = [None] * len(targets)
    sem = asyncio.Semaphore(max(1, concurrency))
    stop: dict = {"reason": None}
    passed = 0

    async def one(i: int, t: CheckTarget) -> None:
        nonlocal passed
        async with sem:  # 먼저 기다린 순서(후보 순위)대로 탭을 받는다
            if stop["reason"]:
                results[i] = CheckResult(t.restaurant_id, "SKIPPED", reason=stop["reason"])
            else:
                r = await checker(t)
                r.attempts = 1
                if r.status == "ERROR" and not stop["reason"]:  # 일시 오류는 한 번 더
                    first = r.seconds
                    r = await checker(t)
                    r.attempts, r.seconds = 2, r.seconds + first
                results[i] = r
                if r.status in PASSED:
                    passed += 1
                    if passed >= want and not stop["reason"]:
                        stop["reason"] = f"지금 주문 가능한 후보 {want}곳을 찾아서 확인을 멈췄어요"
                elif r.status == "BLOCKED":
                    stop["reason"] = "요기요가 접속을 막아서 확인을 멈췄어요"
        if on_result:
            on_result(i, results[i])

    await asyncio.gather(*(one(i, t) for i, t in enumerate(targets)))
    return [r for r in results if r is not None]


async def check_restaurants(
    targets: list[CheckTarget],
    *,
    concurrency: int = 3,
    want: int = 3,
    headless: bool = False,
    browser_channel: str | None = None,
    base_url: str = BASE,
    timeout_ms: int = 8000,
    location: dict | None = None,
    on_result: Callable[[int, CheckResult], None] | None = None,
) -> list[CheckResult]:
    """브라우저를 띄워 후보들을 확인하고 닫는다 (수동 스크립트·이슈 ①용)."""
    from playwright.async_api import async_playwright

    loc = location or json.loads(DEMO_LOCATION.read_text(encoding="utf-8"))
    async with async_playwright() as p:
        browser = await _launch(p, headless=headless, channel=browser_channel)
        try:
            context = await browser.new_context(
                locale="ko-KR", viewport={"width": 1280, "height": 900}
            )
            await context.add_init_script(_init_script(loc))
            checker = browser_checker(context, base_url=base_url, timeout_ms=timeout_ms)
            return await run_checks(
                targets, checker, concurrency=concurrency, want=want, on_result=on_result
            )
        finally:
            await browser.close()


def check_restaurants_sync(targets: list[CheckTarget], **kwargs) -> list[CheckResult]:
    return asyncio.run(check_restaurants(targets, **kwargs))
