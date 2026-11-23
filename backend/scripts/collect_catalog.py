"""식당 종류 다양화 수집 (이슈 #58). 시연 전에 손으로 돌린다.

요기요 카테고리 목록(을지로입구역 기준)을 읽어, 지금 데이터에 적은 음식 종류(족발/보쌈·버거·중국집·아시안…)를
채울 새 식당을 고르고, 식당 페이지에서 메뉴·가격·배달비·영업시간을 읽어 data/1_식당.csv·2_메뉴.csv에 줄을 더한다.
장바구니에는 담지 않고 페이지만 읽는다. 창 없는(headless) 브라우저는 요기요가 막아서 창을 띄운다.

    # 1) 목록 읽기 + 고를 식당 보기 (CSV 안 바꿈, 2~4분)
    uv run python scripts/collect_catalog.py --list
    # 2) 방금 읽은 목록으로 계획만 다시 보기 (브라우저 없음, --target 바꿔 보기)
    uv run python scripts/collect_catalog.py --plan --target 20
    # 종류가 차 있어도 꼭 넣고 싶은 가게 (이름 단어): --plan / --collect 에 붙인다
    uv run python scripts/collect_catalog.py --collect --add 배스킨라빈스 설빙
    # 종류별 목표를 따로 (디저트처럼 가게 유형이 넓은 종류)
    uv run python scripts/collect_catalog.py --plan --target-for 카페/디저트=40
    # 3) 고른 식당 페이지 읽기 + 메뉴 자동 분류 (CSV 안 바꿈)
    uv run python scripts/collect_catalog.py --collect
    # (분류 규칙을 고친 뒤) 요기요를 다시 읽지 않고 메뉴 분류만 다시 붙이기
    uv run python scripts/collect_catalog.py --reclassify
    # 4) 방금 읽은 결과를 CSV에 더하기
    uv run python scripts/collect_catalog.py --write
    # 분류 규칙을 기존 메뉴(이미 분류된 9천여 개)로 맞춰 보기
    uv run python scripts/collect_catalog.py --check-classifier

고르는 규칙 (--target 기본 20, --min-reviews 기본 10)
- 음식 종류는 요기요 세부 카테고리 16가지 (치킨·피자/양식·버거·중국집·일식/돈까스·회/초밥·한식·분식·찜/탕·
  족발/보쌈·고기/구이·도시락/죽·샐러드·샌드위치·아시안·카페/디저트). 한 가게가 여러 종류에 들 수 있다.
- 지금 가장 적은 종류부터 하나씩, 그 종류의 요기요 랭킹 순으로 고른다. 종류가 --target 곳이 되면 그만 고른다.
  이미 많은 종류(피자·한식 등)는 일부러 고르지 않는다.
- 이미 있는 식당, 리뷰가 --min-reviews 개 미만인 곳, 지금 배달이 안 되는 곳, 음식점이 아닌 곳(편의점)은 뺀다.

쓰는 규칙
- 기존 줄은 글자 그대로 두고 새 식당 줄만 더한다 (git diff에 새 줄만 보이게).
- 새 메뉴의 분류 값(유형·맵기·국물·인분·대표메뉴·인기)은 scripts/catalog_classify.py 규칙으로 붙인다.
  분류 보고서(cart_runs/catalog-new-*-report.md)에서 확인할 수 있다.
- 쓰기 전에 원래 CSV를 cart_runs/catalog-backup-날짜/ 에 복사해 둔다.
- 서버를 다시 켜면 새 식당이 DB에 들어간다 (app.modules.discovery.loader.sync_if_newer).
"""

import argparse
import asyncio
import json
import math
import shutil
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ 를 import 경로에 추가

from app.modules.cart.yogiyo import BASE, DEMO_LOCATION, _init_script, _launch  # noqa: E402
from app.modules.discovery.timeutil import now_kst  # noqa: E402
from scripts import recollect_catalog as rc  # noqa: E402
from scripts.catalog_classify import classify_restaurant, menu_type  # noqa: E402

RUNS = Path(__file__).resolve().parents[1] / "cart_runs"
TYPES = ["치킨", "피자/양식", "버거", "중국집", "일식/돈까스", "회/초밥", "한식", "분식", "찜/탕", "족발/보쌈",
         "고기/구이", "도시락/죽", "샐러드", "샌드위치", "아시안", "카페/디저트"]
# 요기요 웹의 카테고리 목록 (세부 종류는 목록에 없어서, 이 목록들을 모아 가게마다 붙은 세부 종류로 나눈다)
SLUGS = ["chicken", "pizza", "chinese_dish", "korean_dish", "japanese_dish", "pig_trotter", "late_night_snack",
         "korean_snack", "cafe_dessert", "one_dish", "franchise"]
# 보정별점 = (별점×리뷰수 + 4.78×49) / (리뷰수 + 49): 기존 158곳의 값에 맞춘 식
PRIOR_RATING, PRIOR_COUNT = 4.78, 49

# 목록 화면의 가게들 (요기요 기본 정렬 = 랭킹 순)
_LIST = """() => {
  for (const e of [...document.querySelectorAll('.ng-scope')].slice(0, 800)) {
    let s = null;
    try { s = angular.element(e).scope(); } catch (err) {}
    if (!s || !s.section_list) continue;
    return (s.section_list.default || []).map(r => {
      const vd = (r.serving_type || {}).vd || {};
      const eta = vd.estimated_delivery_minute || {};
      return {
        id: String(r.id), name: r.name, vertical_type: r.vertical_type,
        categories: r.vendor_categories || [], rating: (r.review || {}).average_rating,
        review_count: (r.review || {}).count || 0,
        lat: r.location ? Number(r.location.lat) : null, lng: r.location ? Number(r.location.lng) : null,
        available: vd.available !== false, eta_min: eta.minimum, eta_max: eta.maximum};
    });
  }
  return null;
}"""
# 식당 페이지에서 recollect_catalog._READ 에 없는 값
_EXTRA = """() => {
  for (const e of [...document.querySelectorAll('.ng-scope')].slice(0, 400)) {
    let s = null;
    try { s = angular.element(e).scope(); } catch (err) {}
    const r = s && s.restaurant;
    if (!r || !r.open_status) continue;
    return {address: r.address || '', lat: Number(r.lat), lng: Number(r.lng),
            categories: r.vendor_categories || [], name: r.name};
  }
  return null;
}"""


async def _eval(page, script: str):
    """페이지에서 읽기. 요기요가 화면을 옮기는 중이면(실행 맥락이 사라짐) None을 돌려 다시 읽게 한다."""
    try:
        return await page.evaluate(script)
    except Exception as exc:  # noqa: BLE001
        if "context was destroyed" in str(exc) or "navigation" in str(exc).lower():
            await page.wait_for_timeout(500)
            return None
        raise


def _demo_location() -> dict:
    return json.loads(DEMO_LOCATION.read_text(encoding="utf-8"))


def _latest(prefix: str) -> Path:
    found = sorted(RUNS.glob(f"{prefix}-*.json"))
    if not found:
        raise SystemExit(f"cart_runs/{prefix}-*.json 이 없어요. 앞 단계를 먼저 돌려 주세요.")
    return found[-1]


def _stamp() -> str:
    return now_kst().strftime("%Y%m%d-%H%M")


# ---------------------------------------------------------------- 1) 목록 읽기 (브라우저)
async def read_lists(chrome: bool, slugs: list[str] = SLUGS) -> dict[str, list[dict]]:
    """카테고리마다 목록을 끝까지 내려 읽는다. {slug: [가게…]}"""
    from playwright.async_api import async_playwright

    loc = _demo_location()
    root = f"{BASE}{quote(loc['sido'])}/{loc['zipcode']}/"
    out: dict[str, list[dict]] = {}
    async with async_playwright() as p:
        browser = await _launch(p, headless=False, channel="chrome" if chrome else None)
        try:
            context = await browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 900})
            await context.add_init_script(_init_script(loc))
            page = await context.new_page()
            for slug in slugs:
                url = f"{root}{slug}/"
                items: list[dict] = []
                for attempt in range(3):  # 요기요가 첫 화면으로 돌려보내면 다시 연다
                    await page.goto(url, wait_until="domcontentloaded")
                    for _ in range(60):  # 첫 목록 최대 15초
                        items = await _eval(page, _LIST) or []
                        if items and f"/{slug}/" in page.url:
                            break
                        await page.wait_for_timeout(250)
                    if items and f"/{slug}/" in page.url:
                        break
                    print(f"  {slug}: 목록이 안 보여 다시 열어요 ({attempt + 1}/3, 지금 주소 {page.url})")
                same = 0
                for _ in range(30):  # 아래로 내려 더 불러오기, 두 번 연속 그대로면 끝
                    await _eval(page, "window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(1200)
                    more = await _eval(page, _LIST)
                    if more is None:
                        continue
                    same = same + 1 if len(more) == len(items) else 0
                    items = more
                    if same >= 2:
                        break
                out[slug] = items
                print(f"  {slug}: {len(items)}곳")
        finally:
            await browser.close()
    return out


# ---------------------------------------------------------------- 2) 고르기 (브라우저 없음)
def type_counts(rows: list[dict]) -> Counter:
    """기존 CSV 줄 → 음식 종류별 식당 수."""
    counts: Counter = Counter()
    for r in rows:
        for c in {x.strip() for x in (r.get("요기요카테고리") or "").split(",")}:
            if c in TYPES:
                counts[c] += 1
    return counts


def candidates(lists: dict[str, list[dict]], existing_ids: set[str], min_reviews: int) -> list[dict]:
    """목록들을 합쳐 고를 수 있는 가게만 남긴다. rank = 목록 안 순서 중 가장 앞."""
    found: dict[str, dict] = {}
    for slug, items in lists.items():
        for rank, item in enumerate(items):
            rid = item["id"]
            if rid in found:
                found[rid]["rank"] = min(found[rid]["rank"], rank)
                found[rid]["slugs"].append(slug)
                continue
            found[rid] = {**item, "rank": rank, "slugs": [slug]}
    out = []
    for item in found.values():
        if item["id"] in existing_ids or item.get("vertical_type", "FOOD") != "FOOD":
            continue
        if not item.get("available", True) or (item.get("review_count") or 0) < min_reviews:
            continue
        if not any(c in TYPES for c in item.get("categories") or []):
            continue
        out.append(item)
    return sorted(out, key=lambda x: (x["rank"], -(x.get("review_count") or 0)))


def _brand(name: str) -> str:
    return name.split("-")[0].strip()


def plan(
    existing: Counter, pool: list[dict], target: int, max_new: int, forced: list[dict] | None = None,
    targets: dict[str, int] | None = None, existing_brands: set[str] | None = None,
) -> tuple[list[dict], Counter]:
    """가장 적은 종류부터 하나씩 채운다. (고른 가게 [{…, 'for': 종류}], 고른 뒤 종류별 수)

    forced: --add 로 꼭 넣을 가게 (종류가 이미 차 있어도 먼저 넣는다).
    targets: 종류별 목표를 따로 정할 때 (--target-for 카페/디저트=40).
    같은 종류 안에서는 아직 없는 브랜드를 먼저 고른다 (뚜레쥬르·바스버거 지점만 여러 곳 들어오지 않게).
    """
    goal = {t: (targets or {}).get(t, target) for t in TYPES}
    brands = set(existing_brands or set())
    counts = Counter({t: existing.get(t, 0) for t in TYPES})
    chosen: list[dict] = []
    forced_ids = set()
    for pick in forced or []:
        chosen.append({**pick, "for": "직접 추가"})
        forced_ids.add(pick["id"])
        brands.add(_brand(pick["name"]))
        for c in set(pick["categories"]):
            if c in TYPES:
                counts[c] += 1
    left = [c for c in pool if c["id"] not in forced_ids]
    done: set[str] = set()  # 더 고를 가게가 없는 종류
    while len(chosen) < max_new:
        needy = [t for t in TYPES if counts[t] < goal[t] and t not in done]
        if not needy:
            break
        t = min(needy, key=lambda x: (counts[x] / max(goal[x], 1), TYPES.index(x)))
        of_type = [c for c in left if t in c["categories"]]
        pick = next((c for c in of_type if _brand(c["name"]) not in brands), of_type[0] if of_type else None)
        if pick is None:
            done.add(t)
            continue
        left.remove(pick)
        chosen.append({**pick, "for": t})
        brands.add(_brand(pick["name"]))
        for c in set(pick["categories"]):
            if c in TYPES:
                counts[c] += 1
    return chosen, counts


def plan_table(before: Counter, after: Counter, target: int, targets: dict[str, int] | None = None) -> str:
    lines = ["| 종류 | 지금 | 수집 후 | |", "|---|---:|---:|---|"]
    for t in sorted(TYPES, key=lambda x: before.get(x, 0)):
        note = "목표 미달 (요기요에 더 없음)" if after[t] < (targets or {}).get(t, target) else ""
        lines.append(f"| {t} | {before.get(t, 0)} | {after[t]} | {note} |")
    return "\n".join(lines)


def forced_picks(lists: dict, existing_ids: set[str], words: list[str]) -> list[dict]:
    """--add 단어가 이름에 든 가게 (리뷰 수와 상관없이, 이미 있는 가게는 빼고)."""
    if not words:
        return []
    return [c for c in candidates(lists, existing_ids, 0) if any(w in c["name"] for w in words)]


def show_plan(
    lists: dict, target: int, min_reviews: int, max_new: int, add: list[str] | None = None,
    targets: dict[str, int] | None = None,
) -> list[dict]:
    rows = rc.restaurant_rows()
    before = type_counts(rows)
    existing_ids = {r["식당id"] for r in rows}
    pool = candidates(lists, existing_ids, min_reviews)
    forced = forced_picks(lists, existing_ids, add or [])
    if add and not forced:
        print(f"--add {' '.join(add)}: 목록에서 찾지 못했거나 이미 있는 가게예요.")
    chosen, after = plan(before, pool, target, max_new, forced, targets,
                         {_brand(r["식당명"]) for r in rows})
    print(f"\n목록에서 찾은 가게 {len({i['id'] for v in lists.values() for i in v})}곳 · "
          f"고를 수 있는 새 가게 {len(pool)}곳 · 고른 가게 {len(chosen)}곳 (목표 종류별 {target}곳)\n")
    print(plan_table(before, after, target, targets))
    print("\n고른 가게 (채우려는 종류: 가게):")
    for c in chosen:
        print(f"  [{c['for']}] {c['name']} ({c['id']}) 리뷰 {c['review_count']} · {'/'.join(c['categories'])}")
    return chosen


# ---------------------------------------------------------------- 3) 식당 페이지 읽기 (브라우저)
async def read_pages(chosen: list[dict], chrome: bool, concurrency: int = 3) -> dict[str, dict]:
    from playwright.async_api import async_playwright

    loc = _demo_location()
    out: dict[str, dict] = {}
    sem = asyncio.Semaphore(concurrency)

    async def one(c: dict, context) -> None:
        rid = c["id"]
        got: dict | None = None
        async with sem:
            page = await context.new_page()
            try:
                await page.goto(f"{BASE}{rid}/", wait_until="domcontentloaded")
                for _ in range(40):
                    if f"/{rid}/" not in page.url:
                        got = {"error": "식당 페이지가 열리지 않음 (요기요 첫 화면으로 이동)"}
                        break
                    got = await _eval(page, rc._READ)
                    if got:
                        got.update(await _eval(page, _EXTRA) or {})
                        break
                    await page.wait_for_timeout(250)
                if not got:
                    got = {"error": "10초 안에 메뉴 데이터가 오지 않음"}
                if await page.locator("text=Access Denied").count():
                    got = {"error": "요기요가 접속을 막음 (Access Denied)"}
            except Exception as exc:  # noqa: BLE001 — 한 곳 실패가 전체를 멈추지 않게
                got = {"error": f"{type(exc).__name__}: {exc}"}
            finally:
                await page.close()
                await asyncio.sleep(0.8)  # 요기요에 부담 주지 않게
        out[rid] = got
        note = got.get("error") or f"메뉴 {len(got['menus'])}개"
        print(f"[{len(out)}/{len(chosen)}] {c['name']} ({rid}): {note}")

    async with async_playwright() as p:
        browser = await _launch(p, headless=False, channel="chrome" if chrome else None)
        try:
            context = await browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 900})
            await context.add_init_script(_init_script(loc))
            await asyncio.gather(*(one(c, context) for c in chosen))
        finally:
            await browser.close()
    return out


# ---------------------------------------------------------------- 4) CSV 줄 만들기 (브라우저 없음)
def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)))


def adjusted_rating(rating: float, count: int) -> float:
    return (rating * count + PRIOR_RATING * PRIOR_COUNT) / (count + PRIOR_COUNT)


def restaurant_row(item: dict, page: dict, origin: dict, collected_at: str) -> dict:
    rating = float(page.get("rating") if page.get("rating") is not None else item.get("rating") or 0)
    count = int(page.get("review_count") if page.get("review_count") is not None else item.get("review_count") or 0)
    lat, lng = page.get("lat") or item.get("lat"), page.get("lng") or item.get("lng")
    eta = f"{item['eta_min']}~{item['eta_max']}" if item.get("eta_min") and item.get("eta_max") else ""
    return {
        "식당id": item["id"],
        "식당명": rc._clean(page.get("name") or item["name"]),
        "수집카테고리": ",".join(item.get("slugs") or []),
        "요기요카테고리": ",".join(page.get("categories") or item.get("categories") or []),
        "별점": f"{rating:.1f}",
        "리뷰수": str(count),
        "보정별점": f"{adjusted_rating(rating, count):.3f}",
        "최소주문금액": str(rc._int(page.get("min_order")) or 0),
        "기본배달비": str(rc._int(page.get("delivery_fee")) or 0),
        "주문금액별배달비": rc._tiers_json(page.get("tiers") or []),
        "예상배달분": eta,
        "거리m": str(distance_m(origin["lat"], origin["lng"], float(lat), float(lng))) if lat and lng else "",
        "지역화폐가능": rc._tf(page.get("voucher")),
        "메뉴수": str(len(page.get("menus") or [])),
        "주소": rc._clean(page.get("address")),
        "요기요링크": f"https://www.yogiyo.co.kr/mobile/#/{item['id']}/",
        "수집시각": collected_at,
        "영업시간": page.get("hours") or "",
    }


def menu_rows(rid: str, name: str, page: dict) -> list[dict]:
    sections = page.get("sections") or []
    titles = rc._section_titles(sections)
    signature = {str(i) for s in sections if "대표" in (s.get("title") or "") for i in s.get("items") or []}
    base = [rc._menu_values(m, titles) for m in page.get("menus") or []]
    labels = classify_restaurant([
        {
            "name": v["메뉴명"], "description": v["설명"], "section": v.get("메뉴분류", ""),
            "liquor": v["주류"] == "t", "price": int(v["판매가"] or 0),
            "review_count": int(v["메뉴리뷰수"] or 0), "signature": m["id"] in signature,
        }
        for m, v in zip(page.get("menus") or [], base, strict=True)
    ])
    rows = []
    for m, v, label in zip(page.get("menus") or [], base, labels, strict=True):
        rows.append({"식당id": rid, "식당명": name, "메뉴분류": v.get("메뉴분류", ""), "메뉴id": m["id"],
                     **{k: v[k] for k in ("메뉴명", "설명", "정가", "판매가", "필수옵션포함가", "필수옵션있음")},
                     **label, "품절": v["품절"], "주류": v["주류"], "메뉴리뷰수": v["메뉴리뷰수"]})
    return rows


def build(chosen: list[dict], pages: dict[str, dict], collected_at: str) -> dict:
    origin = _demo_location()["point"]
    out = {"collected_at": collected_at, "restaurants": [], "menus": [], "unread": []}
    for c in chosen:
        page = pages.get(c["id"]) or {"error": "결과 없음"}
        if page.get("error") or not page.get("menus"):
            out["unread"].append({"restaurant_id": c["id"], "name": c["name"],
                                  "reason": page.get("error") or "메뉴가 하나도 없음"})
            continue
        row = restaurant_row(c, page, origin, collected_at)
        out["restaurants"].append({**row, "__for__": c["for"]})
        out["menus"].extend(menu_rows(row["식당id"], row["식당명"], page))
    return out


def report(new: dict, before: Counter) -> str:
    after = before + type_counts(new["restaurants"])
    kinds = Counter(m["유형"] for m in new["menus"])
    lines = [f"# 새 식당 수집 보고서 ({new['collected_at']})", "",
             f"- 새 식당 {len(new['restaurants'])}곳 · 새 메뉴 {len(new['menus'])}개 · 못 읽은 곳 {len(new['unread'])}곳",
             "- 메뉴 유형: " + ", ".join(f"{k} {n}" for k, n in kinds.most_common()), "",
             "## 종류별 식당 수", "", plan_table(before, after, 0), ""]
    if new["unread"]:
        lines += ["## 못 읽은 곳", ""] + [f"- {u['name']} ({u['restaurant_id']}): {u['reason']}" for u in new["unread"]]
        lines.append("")
    lines += ["## 식당별 메뉴 분류 (확인용)", ""]
    for r in new["restaurants"]:
        lines.append(f"### {r['식당명']} ({r['식당id']}) · {r['요기요카테고리']} · {r['영업시간']}")
        for m in [m for m in new["menus"] if m["식당id"] == r["식당id"]][:40]:
            extra = " ".join(x for x in (
                f"맵기{m['맵기']}" if m["맵기"] else "", "국물" if m["국물"] == "t" else "",
                f"{m['인분']}인분" if m["인분"] else "", "대표" if m["대표메뉴"] == "t" else "",
                "인기" if m["인기"] == "t" else "") if x)
            lines.append(f"- [{m['유형']}] {m['메뉴명']} {int(m['판매가']):,}원 ({m['메뉴분류']}) {extra}".rstrip())
        lines.append("")
    return "\n".join(lines)


def write(new: dict) -> tuple[int, int]:
    """기존 줄은 그대로 두고 새 줄만 더한다. 이미 있는 ID는 건너뛴다."""
    backup = RUNS / f"catalog-backup-{_stamp()}"
    backup.mkdir(parents=True, exist_ok=True)
    for path in (rc.RESTAURANT_CSV, rc.MENU_CSV):
        shutil.copy2(path, backup / path.name)
    r_fields, r_rows = rc._read_rows(rc.RESTAURANT_CSV)
    m_fields, m_rows = rc._read_rows(rc.MENU_CSV)
    have_r = {r["식당id"] for r in r_rows}
    have_m = {m["메뉴id"] for m in m_rows}
    add_r = [r for r in new["restaurants"] if r["식당id"] not in have_r]
    ids = {r["식당id"] for r in add_r}
    add_m = [m for m in new["menus"] if m["식당id"] in ids and m["메뉴id"] not in have_m]
    rc._write_rows(rc.RESTAURANT_CSV, r_fields, r_rows + [{f: r.get(f, "") for f in r_fields} for r in add_r])
    rc._write_rows(rc.MENU_CSV, m_fields, m_rows + [{f: m.get(f, "") for f in m_fields} for m in add_m])
    return len(add_r), len(add_m)


def reclassify(new: dict) -> dict:
    """저장된 --collect 결과의 메뉴 분류만 지금 규칙으로 다시 붙인다 (요기요를 다시 읽지 않음)."""
    by_rest: dict[str, list[dict]] = {}
    for m in new["menus"]:
        by_rest.setdefault(m["식당id"], []).append(m)
    for menus in by_rest.values():
        labels = classify_restaurant([
            {"name": m["메뉴명"], "description": m["설명"], "section": m["메뉴분류"], "liquor": m["주류"] == "t",
             "price": int(m["판매가"] or 0), "review_count": int(m["메뉴리뷰수"] or 0),
             "signature": m["대표메뉴"] == "t"}
            for m in menus
        ])
        for m, label in zip(menus, labels, strict=True):
            m.update(label)
    return new


# ---------------------------------------------------------------- 분류 규칙 점검
def check_classifier() -> None:
    _, rows = rc._read_rows(rc.MENU_CSV)
    ok = sum(menu_type(m["메뉴명"], m["메뉴분류"], m["주류"] == "t", int(m["판매가"] or 0)) == m["유형"] for m in rows)
    print(f"기존 메뉴 {len(rows)}개 중 유형이 같은 것: {ok}개 ({ok / len(rows) * 100:.1f}%)")


def main() -> int:
    ap = argparse.ArgumentParser(description="식당 종류 다양화 수집 (이슈 #58)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="요기요 카테고리 목록을 읽고 고를 식당을 보여 준다")
    mode.add_argument("--plan", action="store_true", help="마지막으로 읽은 목록으로 계획만 다시 본다")
    mode.add_argument("--collect", action="store_true", help="고른 식당 페이지를 읽고 메뉴를 분류한다")
    mode.add_argument("--reclassify", action="store_true", help="마지막 --collect 결과의 메뉴 분류만 다시 붙인다")
    mode.add_argument("--write", action="store_true", help="마지막 --collect 결과를 CSV에 더한다")
    mode.add_argument("--check-classifier", action="store_true", help="분류 규칙을 기존 메뉴로 맞춰 본다")
    ap.add_argument("--target", type=int, default=20, help="종류별 목표 식당 수 (기본 20)")
    ap.add_argument("--min-reviews", type=int, default=10)
    ap.add_argument("--max-new", type=int, default=150, help="새로 더할 식당 수 상한")
    ap.add_argument("--target-for", nargs="+", default=[], metavar="종류=수",
                    help="종류별 목표를 따로 (예: --target-for 카페/디저트=40 버거=25)")
    ap.add_argument("--add", nargs="+", default=[], metavar="이름",
                    help="종류가 차 있어도 꼭 넣을 가게 이름 단어 (예: --add 배스킨라빈스 설빙)")
    ap.add_argument("--chrome", action="store_true", help="설치된 Chrome으로 띄우기")
    a = ap.parse_args()
    RUNS.mkdir(exist_ok=True)
    targets = {}
    for item in a.target_for:
        kind, _, num = item.partition("=")
        if kind not in TYPES or not num.isdigit():
            raise SystemExit(f"--target-for 형식: 종류=수 (종류는 {', '.join(TYPES)})")
        targets[kind] = int(num)

    if a.check_classifier:
        check_classifier()
        return 0
    if a.list:
        print("요기요 카테고리 목록을 읽어요 (창이 뜹니다, 2~4분)…")
        lists = asyncio.run(read_lists(a.chrome))
        path = RUNS / f"catalog-list-{_stamp()}.json"
        path.write_text(json.dumps(lists, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"목록 저장: {path.relative_to(RUNS.parent)}")
        show_plan(lists, a.target, a.min_reviews, a.max_new, a.add, targets)
        print("\n다음: --collect 로 고른 식당 페이지를 읽어요.")
        return 0
    if a.plan:
        show_plan(json.loads(_latest("catalog-list").read_text(encoding="utf-8")), a.target, a.min_reviews, a.max_new,
                  a.add, targets)
        return 0
    if a.collect:
        lists = json.loads(_latest("catalog-list").read_text(encoding="utf-8"))
        chosen = show_plan(lists, a.target, a.min_reviews, a.max_new, a.add, targets)
        if not chosen:
            print("더할 식당이 없어요.")
            return 0
        print(f"\n고른 {len(chosen)}곳의 식당 페이지를 읽어요…")
        pages = asyncio.run(read_pages(chosen, a.chrome))
        collected_at = now_kst().strftime("%Y-%m-%d %H:%M:%S+09")
        new = build(chosen, pages, collected_at)
        stamp = _stamp()
        path = RUNS / f"catalog-new-{stamp}.json"
        path.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
        rep = RUNS / f"catalog-new-{stamp}-report.md"
        rep.write_text(report(new, type_counts(rc.restaurant_rows())), encoding="utf-8")
        print(f"\n새 식당 {len(new['restaurants'])}곳 · 메뉴 {len(new['menus'])}개 · 못 읽은 곳 {len(new['unread'])}곳")
        print(f"결과: {path.relative_to(RUNS.parent)}\n보고서(분류 확인): {rep.relative_to(RUNS.parent)}")
        print("다음: --write 로 CSV에 더해요.")
        return 0
    if a.reclassify:
        path = _latest("catalog-new")
        new = reclassify(json.loads(path.read_text(encoding="utf-8")))
        path.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
        rep = path.with_name(path.stem + "-report.md")
        rep.write_text(report(new, type_counts(rc.restaurant_rows())), encoding="utf-8")
        kinds = Counter(m["유형"] for m in new["menus"])
        print("메뉴 분류를 다시 붙였어요: " + ", ".join(f"{k} {n}" for k, n in kinds.most_common()))
        print(f"보고서: {rep.relative_to(RUNS.parent)}\n다음: --write 로 CSV에 더해요.")
        return 0
    new = json.loads(_latest("catalog-new").read_text(encoding="utf-8"))
    n_r, n_m = write(new)
    print(f"data/1_식당.csv 에 {n_r}곳, data/2_메뉴.csv 에 메뉴 {n_m}개를 더했어요 (백업: cart_runs/catalog-backup-*).")
    print("서버를 다시 켜면 DB에 들어가요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
