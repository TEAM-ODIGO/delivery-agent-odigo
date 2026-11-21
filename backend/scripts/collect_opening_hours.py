"""요기요 식당 영업시간 수집 (이슈 ⑤).

수집 CSV(data/1_식당.csv)의 식당마다 요기요 식당 페이지의 '정보' 탭을 읽어 영업시간을 모은다.
장바구니에는 담지 않고 페이지만 읽는다. 창 없는(headless) 브라우저는 요기요가 막아서 창을 띄운다.

원본 확인 (영업시간이 어떤 모양으로 나오는지 본다):
    uv run python scripts/collect_opening_hours.py --probe 3
    → cart_runs/hours-probe-날짜.json 에 정보 탭 글자와 페이지 데이터 중 시간 관련 값을 남긴다.

수집 (페이지 데이터의 open_time_description, 예: "17:00 ~ 03:00"):
    uv run python scripts/collect_opening_hours.py --collect            # 미리 보기만 (CSV 안 바꿈)
    uv run python scripts/collect_opening_hours.py --collect --write    # data/1_식당.csv에 씀
    → 탭 3개로 식당 전부를 연다 (158곳에 1~2분). 결과는 cart_runs/hours-날짜.json 에도 남는다.
    → 서버를 다시 켜면 CSV의 영업시간이 DB에 반영된다 (app.modules.discovery.hours.sync_from_csv).
"""

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ 를 import 경로에 추가

from app.modules.cart.yogiyo import BASE, DEMO_LOCATION, _init_script, _launch  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data" / "1_식당.csv"

# 정보 탭을 누른다: 글자가 정확히 '정보'인 탭
_CLICK_INFO_TAB = """() => {
  const els = [...document.querySelectorAll('a, li, button, span, div')]
    .filter(e => e.innerText && e.innerText.trim() === '정보' && e.offsetParent !== null);
  const el = els.find(e => e.tagName === 'A') || els[0];
  if (!el) return false;
  el.click();
  return true;
}"""
# 정보 탭 글자 중 시간과 관련된 부분
_INFO_TEXT = """() => {
  const body = document.body.innerText;
  const i = body.search(/영업시간|운영시간|휴무|브레이크/);
  return i < 0 ? body.slice(0, 1500) : body.slice(Math.max(0, i - 300), i + 900);
}"""
# 페이지 데이터(angular scope)에서 시간·영업 관련 값
_SCOPE_TIMES = """() => {
  const out = {};
  const seen = new Set();
  const want = /time|open|hour|close|holiday|break|biz|is_open|available|order/i;
  const els = [...document.querySelectorAll('.ng-scope')].slice(0, 400);
  for (const e of els) {
    let s = null;
    try { s = angular.element(e).scope(); } catch (err) {}
    if (!s || seen.has(s.$id)) continue;
    seen.add(s.$id);
    for (const k of Object.keys(s)) {
      if (k.startsWith('$')) continue;
      const v = s[k];
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        for (const kk of Object.keys(v)) {
          if (want.test(kk)) {
            const val = v[kk];
            const txt = typeof val === 'object' ? JSON.stringify(val) : String(val);
            out[`${k}.${kk}`] = txt.slice(0, 300);
          }
        }
      } else if (want.test(k) && typeof v !== 'function') {
        out[k] = String(v).slice(0, 300);
      }
    }
  }
  return out;
}"""


# 페이지 데이터의 식당 영업시간 (정보 탭을 누르지 않아도 식당 페이지에 있다)
_READ_HOURS = """() => {
  for (const e of [...document.querySelectorAll('.ng-scope')].slice(0, 400)) {
    let s = null;
    try { s = angular.element(e).scope(); } catch (err) {}
    const r = s && s.restaurant;
    if (r && r.open_time_description !== undefined) {
      return {hours: r.open_time_description || null,
              status: (r.open_status || {}).current_open_status || null};
    }
  }
  return null;
}"""


def restaurants(limit: int) -> list[dict]:
    with open(DATA, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: -int(r["리뷰수"] or 0))
    return rows[:limit]


async def probe(limit: int, out_dir: str, chrome: bool) -> Path:
    from playwright.async_api import async_playwright

    loc = json.loads(DEMO_LOCATION.read_text(encoding="utf-8"))
    results = []
    async with async_playwright() as p:
        browser = await _launch(p, headless=False, channel="chrome" if chrome else None)
        try:
            context = await browser.new_context(
                locale="ko-KR", viewport={"width": 1280, "height": 900}
            )
            await context.add_init_script(_init_script(loc))
            page = await context.new_page()
            for r in restaurants(limit):
                rid = r["식당id"]
                await page.goto(f"{BASE}{rid}/", wait_until="domcontentloaded")
                await page.wait_for_timeout(2500)
                scope = await page.evaluate(_SCOPE_TIMES)
                clicked = await page.evaluate(_CLICK_INFO_TAB)
                await page.wait_for_timeout(1500)
                info = await page.evaluate(_INFO_TEXT)
                results.append(
                    {
                        "restaurant_id": rid,
                        "name": r["식당명"],
                        "url": page.url,
                        "info_tab_clicked": clicked,
                        "info_text": info,
                        "scope_times": scope,
                    }
                )
                print(f"{r['식당명']} ({rid}): 정보 탭 {'열림' if clicked else '못 찾음'}")
        finally:
            await browser.close()
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = Path(out_dir) / f"hours-probe-{datetime.now():%Y%m%d-%H%M}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def collect(out_dir: str, chrome: bool, concurrency: int = 3) -> tuple[list[dict], Path]:
    from playwright.async_api import async_playwright

    loc = json.loads(DEMO_LOCATION.read_text(encoding="utf-8"))
    rows = restaurants(10_000)
    results: list[dict | None] = [None] * len(rows)
    sem = asyncio.Semaphore(concurrency)

    async def one(i: int, r: dict, context) -> None:
        rid = r["식당id"]
        async with sem:
            page = await context.new_page()
            got = None
            try:
                await page.goto(f"{BASE}{rid}/", wait_until="domcontentloaded")
                for _ in range(32):  # 최대 8초
                    got = await page.evaluate(_READ_HOURS)
                    if got or f"/{rid}/" not in page.url:
                        break
                    await page.wait_for_timeout(250)
            except Exception as exc:  # noqa: BLE001 — 한 곳 실패가 전체를 멈추지 않게
                got = {"error": f"{type(exc).__name__}: {exc}"}
            finally:
                await page.close()
        moved = got is None
        results[i] = {
            "restaurant_id": rid,
            "name": r["식당명"],
            "hours": (got or {}).get("hours"),
            "status_now": (got or {}).get("status"),
            "note": "식당 페이지가 열리지 않음" if moved else (got or {}).get("error"),
        }
        print(f"[{i + 1}/{len(rows)}] {r['식당명']} ({rid}): {results[i]['hours'] or '모름'}")

    async with async_playwright() as p:
        browser = await _launch(p, headless=False, channel="chrome" if chrome else None)
        try:
            context = await browser.new_context(
                locale="ko-KR", viewport={"width": 1280, "height": 900}
            )
            await context.add_init_script(_init_script(loc))
            await asyncio.gather(*(one(i, r, context) for i, r in enumerate(rows)))
        finally:
            await browser.close()
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = Path(out_dir) / f"hours-{datetime.now():%Y%m%d-%H%M}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return [r for r in results if r], path


def write_csv(found: list[dict]) -> int:
    """data/1_식당.csv에 '영업시간' 열을 넣거나 고친다. 이번에 못 읽은 식당은 기존 값을 둔다."""
    with open(DATA, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if "영업시간" not in fields:
        fields.append("영업시간")
    by_id = {r["restaurant_id"]: r["hours"] for r in found if r.get("hours")}
    changed = 0
    for r in rows:
        new = by_id.get(r["식당id"], r.get("영업시간") or "")
        changed += (r.get("영업시간") or "") != new
        r["영업시간"] = new
    with open(DATA, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description="요기요 식당 영업시간 수집")
    ap.add_argument("--probe", type=int, metavar="N", help="리뷰 많은 N곳의 정보 탭 원본만 저장")
    ap.add_argument("--collect", action="store_true", help="모든 식당의 영업시간을 모은다")
    ap.add_argument("--write", action="store_true", help="--collect 결과를 data/1_식당.csv에 쓴다")
    ap.add_argument("--chrome", action="store_true", help="설치된 Chrome으로 실행")
    ap.add_argument("--out", default="cart_runs")
    a = ap.parse_args()
    if a.probe:
        path = asyncio.run(probe(a.probe, a.out, a.chrome))
        print(f"기록: {path}")
        return 0
    if a.collect:
        found, path = asyncio.run(collect(a.out, a.chrome))
        known = sum(1 for r in found if r.get("hours"))
        print(f"끝: {len(found)}곳 중 {known}곳 영업시간 읽음 · 기록: {path}")
        if a.write:
            print(f"data/1_식당.csv '영업시간' 열에 썼어요 (바뀐 식당 {write_csv(found)}곳)")
        else:
            print("CSV는 바꾸지 않았어요. 쓰려면 --write 를 붙여 다시 실행하세요.")
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
