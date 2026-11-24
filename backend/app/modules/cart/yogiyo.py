"""요기요 장바구니 담기 — 진짜 구현 (Playwright). 결제 직전(장바구니)에서 멈춘다.
주문하기·결제는 절대 누르지 않는다. 주문·결제 화면으로 넘어가면 즉시 중단한다 (_guard).

Playwright는 기본 의존성이라 uv sync로 설치된다. 크로미움 브라우저가 없으면 첫 담기 때
자동으로 받는다 (_launch, 1~2분). 설치된 Chrome을 쓰려면 YOGIYO_CART_BROWSER=chrome.

1일차 확인 결과 (2026-11-16, 내장 브라우저로 실측)
- 주소: sessionStorage 'ngStorage-location' 하나에 통째로 저장 → 페이지 스크립트보다 먼저 넣으면
  주소 입력 화면 없이 식당 페이지가 열린다 (없으면 첫 화면으로 돌려보냄)
- 식당 URL: https://www.yogiyo.co.kr/mobile/#/{식당id}/
  (수집 CSV의 식당id = 요기요 식당 ID)
- 메뉴: [ng-click^="select_menuitem"] 요소의 angular scope.item_id
  = 수집 CSV의 메뉴id (125/125 일치)
  같은 메뉴가 '요기서결제시 할인' 묶음에도 한 번 더 나온다 → 그쪽은 건너뛴다
- 메뉴 상세 창: 옵션 입력칸 id = sm_{섹션}_{옵션}, 담기 버튼 글자 = '주문표에 추가'
- 장바구니: sessionStorage 'ngStorage-cart'
  = {restaurant_id, items:[{id, name, price, amount, subchoices…}]}
- 배달비: 장바구니 영역 글자 '배달요금 1,000원' (수집 CSV의 구간 배달비와 같았음)
- 주의: 일회용품이 자동 추가되는 매장이 있다 (견적과 실제 금액이 달라질 수 있음)

새 브라우저 문맥으로 매번 시작하므로 다른 가게 장바구니가 남아 있지 않다 (교체 확인 팝업 없음).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("app.cart")

BASE = "https://www.yogiyo.co.kr/mobile/#/"
DEMO_LOCATION = Path(__file__).with_name("demo_location.json")  # 을지로입구역 (공공 주소, 이슈 #58)

# 페이지 안에서 실행하는 코드 (내장 브라우저에서 그대로 확인한 것)
_COUNT_MENUS = """() => document.querySelectorAll('[ng-click^="select_menuitem"]').length"""
_OPEN_MENU = """(menuId) => {
  const els = [...document.querySelectorAll('[ng-click^="select_menuitem"]')]
    .filter(e => String(angular.element(e).scope().item_id) === String(menuId));
  const el = els.find(e => !e.innerText.includes('요기서결제시')) || els[0];
  if (!el) return false;
  el.scrollIntoView({block: 'center'});
  el.click();
  return true;
}"""
_OPTION_STATE = """() => [...document.querySelectorAll('input[id^="sm_"]')]
  .filter(i => i.offsetParent !== null)
  .map(i => ({id: i.id, type: i.type, checked: i.checked}))"""
_READ_CART = """() => JSON.parse(sessionStorage.getItem('ngStorage-cart') || '{}')"""
_CART_TEXT = """() => (document.querySelector('.cart') || {innerText: ''}).innerText"""
# 요기요 앞단 보안(Akamai)이 창 없는(headless) 브라우저를 막을 때 나오는 화면. 우회하지 않는다.
_ACCESS_DENIED = """() => /Access Denied/i.test(document.title + ' ' + document.body.innerText)"""
BLOCKED_MESSAGE = (
    "요기요가 접속을 막았어요 (Access Denied). "
    "창 없는(headless) 모드라면 YOGIYO_CART_HEADLESS=0으로 실행하고, "
    "해외·클라우드 서버라면 접속 위치 때문이니 "
    "YOGIYO_CART_MODE=remote(한국 PC 작업기)를 써 주세요."
)
_NOT_SERVING = """() => /요기요\\s*서비스가\\s*제공되지/.test(document.body.innerText)"""
_VISIBLE_MODAL_TEXT = """() => [...document.querySelectorAll('.modal')]
  .filter(m => m.offsetParent !== null || getComputedStyle(m).display === 'block')
  .map(m => m.innerText.replace(/\\s+/g, ' ').slice(0, 200))"""


@dataclass
class CartItem:
    menu_id: str
    quantity: int = 1
    option_input_ids: list[str] = field(default_factory=list)  # 'sm_{섹션}_{옵션}' (없으면 기본값)
    name: str | None = None  # 견적의 메뉴 이름·단가 (표시·가짜 구현용, 담을 때는 menu_id만 씀)
    unit_price_krw: int | None = None


@dataclass
class CartOrder:
    restaurant_id: str
    items: list[CartItem]
    expected_menu_subtotal_krw: int | None = None  # B 견적의 메뉴 합계 (대조용)
    expected_delivery_fee_krw: int | None = None
    location: dict | None = None  # ngStorage-location 값. 없으면 을지로입구역


@dataclass
class CartResult:
    status: str  # CART_READY(견적과 같음) | CART_MISMATCH(다름) | FAILED | REACHABLE(열림 확인만)
    restaurant_id: str
    cart_items: list[dict] = field(default_factory=list)
    menu_subtotal_krw: int | None = None
    delivery_fee_krw: int | None = None
    mismatches: list[str] = field(default_factory=list)
    error: str | None = None
    screenshot: str | None = None
    url: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def _init_script(location: dict) -> str:
    # 요기요 페이지의 스크립트보다 먼저 실행: 주소를 세션 저장소에 넣는다 (요기요 도메인에서만)
    value = json.dumps(json.dumps(location, ensure_ascii=False), ensure_ascii=False)
    return (
        "if (location.hostname.endsWith('yogiyo.co.kr') && "
        "!sessionStorage.getItem('ngStorage-location')) "
        f"{{ sessionStorage.setItem('ngStorage-location', {value}); }}"
    )


def _guard(page) -> None:
    """결제 직전에서 멈추는 안전장치: 주문·결제 화면으로 넘어갔다면 즉시 중단."""
    if re.search(r"#/(checkout|order|payment)", page.url):
        raise RuntimeError(f"주문/결제 화면으로 이동함 — 중단: {page.url}")


def _won(text: str, label: str) -> int | None:
    m = re.search(label + r"\s*([\d,]+)\s*원", text)
    return int(m.group(1).replace(",", "")) if m else None


# ---------------------------------------------------------------- 브라우저 실행 (없으면 자동 설치)
_install_lock = threading.Lock()


def _browser_missing(exc: Exception) -> bool:
    msg = str(exc)
    return "Executable doesn't exist" in msg or "playwright install" in msg


def install_chromium() -> None:
    """Playwright용 크로미움을 받는다 (처음 한 번, 1~2분). 이미 있으면 바로 끝난다."""
    with _install_lock:
        log.warning("Playwright 크로미움이 없어 자동 설치합니다 (처음 한 번, 1~2분)")
        try:
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True,
                capture_output=True,
                text=True,
                timeout=600,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            out = f"{getattr(exc, 'stderr', None) or ''}\n{getattr(exc, 'output', None) or ''}"
            errors = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("Error")]
            detail = (errors[0] if errors else (out.strip() or str(exc)))[:300]
            raise RuntimeError(
                "크로미움 자동 설치 실패 (네트워크를 확인하거나 "
                f"YOGIYO_CART_BROWSER=chrome 으로 설치된 Chrome 사용): {detail}"
            ) from exc


async def _launch(pw, *, headless: bool, channel: str | None):
    """브라우저를 띄운다. Playwright 크로미움이 없으면 받아서 한 번 더 시도한다."""
    try:
        return await pw.chromium.launch(headless=headless, channel=channel)
    except Exception as exc:
        if channel or not _browser_missing(exc):
            raise  # 설치된 Chrome(channel)이 없거나 다른 오류면 그대로 알린다
    await asyncio.to_thread(install_chromium)
    return await pw.chromium.launch(headless=headless, channel=channel)


async def fill_cart(
    order: CartOrder,
    *,
    headless: bool = False,
    out_dir: str | Path = "cart_runs",
    timeout_ms: int = 20000,
    check_only: bool = False,
    base_url: str = BASE,
    browser_channel: str | None = None,
    keep_open_seconds: float = 0,
    name: str | None = None,
) -> CartResult:
    """브라우저를 새로 띄워 담고 닫는다 (수동 스크립트·창 없이 실행할 때).

    check_only면 식당 페이지가 열리는지만 본다.
    keep_open_seconds: 끝난 뒤 브라우저를 이만큼 열어 둔다 (headless=False일 때 의미 있음).
    요기요 장바구니는 그 브라우저 탭에만 남으므로 닫으면 사라진다.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        # browser_channel='chrome': 설치된 Chrome 사용 (playwright install 없이)
        browser = await _launch(p, headless=headless, channel=browser_channel)
        try:
            context = await _new_context(browser, order)
            result = await _fill(
                context,
                order,
                out_dir=out_dir,
                timeout_ms=timeout_ms,
                check_only=check_only,
                base_url=base_url,
                name=name,
            )
            if keep_open_seconds > 0 and not headless:
                await asyncio.sleep(keep_open_seconds)
        finally:
            await browser.close()
    return result


async def _new_context(browser, order: CartOrder):
    location = order.location or json.loads(DEMO_LOCATION.read_text(encoding="utf-8"))
    context = await browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 900})
    await context.add_init_script(_init_script(location))
    return context


async def _fill(
    context,
    order: CartOrder,
    *,
    out_dir: str | Path,
    timeout_ms: int,
    check_only: bool,
    base_url: str,
    name: str | None,
) -> CartResult:
    """열린 브라우저 창(context)에서 식당을 열고 메뉴를 담은 뒤 장바구니에서 멈춘다."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = CartResult(status="FAILED", restaurant_id=order.restaurant_id)
    page = await context.new_page()
    page.set_default_timeout(timeout_ms)
    # 예상 못 한 확인창은 '취소' (장바구니 비우기 방지)
    page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
    try:
        await page.goto(f"{base_url}{order.restaurant_id}/", wait_until="domcontentloaded")
        try:
            await page.wait_for_function(f"({_COUNT_MENUS})() > 0", timeout=timeout_ms)
        except Exception:
            if await page.evaluate(_ACCESS_DENIED):
                raise RuntimeError(BLOCKED_MESSAGE) from None
            raise
        result.url = page.url
        if f"/{order.restaurant_id}/" not in page.url:
            raise RuntimeError(f"식당 페이지가 아니라 {page.url} 로 이동함 (주소 설정 실패?)")
        if await page.evaluate(_NOT_SERVING):
            raise RuntimeError("이 가게는 지금 요기요 주문을 받지 않아요 (요기요 서비스 미제공)")
        if check_only:
            result.status = "REACHABLE"
            return result

        for it in order.items:
            for _ in range(it.quantity):  # 같은 메뉴를 다시 담으면 수량이 1 늘어난다
                if not await page.evaluate(_OPEN_MENU, it.menu_id):
                    raise RuntimeError(f"메뉴 {it.menu_id}를 화면에서 찾지 못함 (메뉴 변경?)")
                add = page.get_by_role("button", name="주문표에 추가")
                await add.wait_for(state="visible")
                await _apply_options(page, it.option_input_ids)
                await add.click()
                await page.wait_for_timeout(800)
                modal = await page.evaluate(_VISIBLE_MODAL_TEXT)
                if any("필수" in t or "선택해" in t for t in modal):
                    raise RuntimeError(f"필수 옵션 선택이 필요함: {modal}")
                _guard(page)

        cart = await page.evaluate(_READ_CART)
        try:  # 원본은 캡처 옆에 남긴다 (요기요 저장 형식이 바뀌었을 때 확인용)
            (out / f"{name or order.restaurant_id}-cart.json").write_text(
                json.dumps(cart, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass
        result.cart_items, cart_restaurant = _parse_cart(cart)
        if str(cart_restaurant) != str(order.restaurant_id):
            raise RuntimeError(f"장바구니 식당이 다름: {cart_restaurant}")
        result.menu_subtotal_krw = sum(
            (i["unit_price_krw"] or 0) * (i["quantity"] or 0) for i in result.cart_items
        )
        result.delivery_fee_krw = _won(await page.evaluate(_CART_TEXT), "배달요금")
        result.mismatches = _compare(order, result)
        result.status = "CART_MISMATCH" if result.mismatches else "CART_READY"
    except Exception as exc:  # noqa: BLE001 — 실패도 결과로 남긴다 (캡처 포함)
        result.status = "FAILED"
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        shot = out / f"{name or order.restaurant_id}-{result.status}.jpg"
        try:
            await page.screenshot(path=str(shot), full_page=False, type="jpeg", quality=70)
            result.screenshot = str(shot)
        except Exception:  # noqa: BLE001
            pass
    return result


def _option_names(v) -> list[str]:
    """선택 옵션 이름만 뽑는다. 요기요는 옵션을 문자열·객체·중첩 목록 등으로 저장한다."""
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        n = v.get("name") or v.get("title")
        if n:
            return [str(n)]
        return _option_names(v.get("subchoices") or v.get("items"))
    if isinstance(v, list | tuple):
        return [n for x in v for n in _option_names(x)]
    return [str(v)]


def _parse_cart(cart) -> tuple[list[dict], object]:
    """ngStorage-cart 값을 (담긴 메뉴 목록, 식당 id)로 바꾼다. 형식이 조금 달라도 버틴다."""
    for _ in range(2):  # JSON 문자열로 한 번 더 감싸져 있으면 푼다
        if isinstance(cart, str):
            try:
                cart = json.loads(cart)
            except ValueError:
                break
    if not isinstance(cart, dict):
        raise RuntimeError(f"요기요 장바구니 형식을 읽지 못함: {type(cart).__name__}")
    items = []
    for i in cart.get("items") or []:
        if not isinstance(i, dict):
            continue
        items.append(
            {
                "menu_id": str(i.get("id")),
                "name": i.get("name"),
                "unit_price_krw": i.get("price"),
                "quantity": i.get("amount"),
                "options": _option_names(i.get("subchoices")),
            }
        )
    return items, cart.get("restaurant_id")


async def _apply_options(page, wanted: list[str]) -> None:
    """원하는 옵션만 켠다. 이미 켜진 기본값은 그대로 둔다.

    (수집 데이터에 옵션이 없어 기본값 기준)
    """
    if not wanted:
        return
    state = {o["id"]: o for o in await page.evaluate(_OPTION_STATE)}
    for oid in wanted:
        if oid not in state:
            raise RuntimeError(f"옵션 {oid}가 메뉴 상세 창에 없음")
        if not state[oid]["checked"]:
            await page.locator(f"label[for='{oid}']").first.click()


def _compare(order: CartOrder, r: CartResult) -> list[str]:
    out = []
    want = {it.menu_id: it.quantity for it in order.items}
    got = {i["menu_id"]: i["quantity"] for i in r.cart_items}
    if want != got:
        out.append(f"담긴 메뉴·수량이 다름: 기대 {want}, 실제 {got}")
    if order.expected_menu_subtotal_krw is not None and (
        r.menu_subtotal_krw != order.expected_menu_subtotal_krw
    ):
        out.append(
            f"메뉴 합계가 견적과 다름: 견적 {order.expected_menu_subtotal_krw}원, "
            f"요기요 {r.menu_subtotal_krw}원"
        )
    if order.expected_delivery_fee_krw is not None and (
        r.delivery_fee_krw != order.expected_delivery_fee_krw
    ):
        out.append(
            f"배달비가 견적과 다름: 견적 {order.expected_delivery_fee_krw}원, "
            f"요기요 {r.delivery_fee_krw}원"
        )
    return out


def fill_cart_sync(order: CartOrder, **kwargs) -> CartResult:
    """작업 스레드에서 부르는 동기 입구 (스레드마다 새 이벤트 루프)."""
    return asyncio.run(fill_cart(order, **kwargs))


class BrowserKeeper:
    """브라우저 창을 다음 주문까지 열어 둔다 (시연용, 창을 띄울 때만).

    Playwright 객체는 만든 이벤트 루프·스레드에서만 써야 하므로, 반드시 같은 작업 스레드
    (service.default_executor, 작업자 1개)에서만 부른다.
    - 새 담기 작업: 이전 주문의 창(context)을 닫고 새 창에서 담는다 (다시 담기도 처음부터).
    - close(): 새 주문(O-01)이 들어오면 창을 닫는다.
    - 사용자가 창을 직접 닫았으면 다음 작업에서 브라우저를 다시 띄운다.
    """

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pw = None
        self.browser = None
        self.context = None

    def _run(self, coro):
        if self.loop is None or self.loop.is_closed():
            self.loop = asyncio.new_event_loop()
        return self.loop.run_until_complete(coro)

    async def _browser(self, headless: bool, channel: str | None):
        if self.browser is not None and self.browser.is_connected():
            return self.browser
        await self._shutdown()
        from playwright.async_api import async_playwright

        self.pw = await async_playwright().start()
        self.browser = await _launch(self.pw, headless=headless, channel=channel)
        return self.browser

    async def _close_context(self) -> None:
        if self.context is not None:
            try:
                await self.context.close()
            except Exception:  # noqa: BLE001 — 사용자가 이미 닫은 경우
                pass
            self.context = None

    async def _shutdown(self) -> None:
        await self._close_context()
        for obj, meth in ((self.browser, "close"), (self.pw, "stop")):
            if obj is not None:
                try:
                    await getattr(obj, meth)()
                except Exception:  # noqa: BLE001
                    pass
        self.browser = self.pw = None

    def fill(
        self,
        order: CartOrder,
        *,
        headless: bool = False,
        browser_channel: str | None = None,
        out_dir: str | Path = "cart_runs",
        timeout_ms: int = 20000,
        base_url: str = BASE,
        name: str | None = None,
    ) -> CartResult:
        async def go() -> CartResult:
            await self._close_context()  # 이전 주문 창 닫기
            browser = await self._browser(headless, browser_channel)
            self.context = await _new_context(browser, order)
            return await _fill(
                self.context,
                order,
                out_dir=out_dir,
                timeout_ms=timeout_ms,
                check_only=False,
                base_url=base_url,
                name=name,
            )

        return self._run(go())

    def with_browser(self, fn, *, headless: bool = False, browser_channel: str | None = None):
        """같은 브라우저로 다른 작업을 한다 (요기요 실시간 확인). fn(browser)는 코루틴을 돌려준다.

        브라우저를 하나만 띄워 요기요 창이 겹쳐 뜨지 않게 한다. 열어 둔 장바구니 창은 그대로 둔다.
        """

        async def go():
            browser = await self._browser(headless, browser_channel)
            return await fn(browser)

        return self._run(go())

    def close(self) -> None:
        """열어 둔 요기요 창을 닫는다 (브라우저는 다음 작업을 위해 남겨 둔다)."""
        if self.loop is not None and not self.loop.is_closed():
            self._run(self._close_context())
