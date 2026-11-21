"""식당·메뉴 데이터 재수집 (이슈 #35). 시연 전에 손으로 돌린다.

요기요 식당 페이지의 데이터(angular scope의 restaurant)에서 메뉴·가격·품절·배달비·최소 주문 금액·
영업시간을 다시 읽어 data/1_식당.csv, data/2_메뉴.csv와 비교한다.
장바구니에는 담지 않고 페이지만 읽는다. 창 없는(headless) 브라우저는 요기요가 막아서 창을 띄운다.

    # 다시 읽고 바뀐 점만 보여 줌 (CSV 안 바꿈)
    uv run python scripts/recollect_catalog.py --collect
    # 다시 읽고 CSV에 씀
    uv run python scripts/recollect_catalog.py --collect --write
    # 방금 읽은 결과를 다시 읽지 않고 씀
    uv run python scripts/recollect_catalog.py --from latest --write
    # 페이지 데이터 원본 확인 (개발용)
    uv run python scripts/recollect_catalog.py --probe 2

쓰는 규칙
- 식당을 80% 미만 읽었으면 쓰지 않는다 (--min-ratio 로 바꿈).
  요기요가 막았거나 네트워크 문제일 수 있다.
- 못 읽은 식당은 기존 줄을 그대로 둔다.
- 기존 메뉴: 이름·설명·분류·가격·필수옵션·품절·주류·리뷰수만 고친다.
  유형(본메뉴·사이드…)·맵기·국물·인분·대표메뉴·인기는 따로 분류한 값이라 그대로 둔다.
- 요기요에서 사라진 메뉴: 줄을 지우지 않고 품절로 표시한다.
  같은 이름의 메뉴가 새 번호로 생겼으면 번호만 바꾼다 (분류 값은 유지).
- 새로 생긴 메뉴: 분류 값이 없어 추가하지 않고 보고서에만 남긴다.
- 쓰기 전에 원래 CSV를 cart_runs/catalog-backup-날짜/ 에 복사해 둔다.
- 바뀌지 않은 줄은 글자 그대로 둔다 (git diff에 바뀐 줄만 보이게).
- 서버를 다시 켜면 CSV가 DB에 반영된다 (app.modules.discovery.loader.sync_if_newer).
"""

import argparse
import asyncio
import csv
import io
import json
import re
import shutil
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ 를 import 경로에 추가

from app.modules.cart.yogiyo import BASE, DEMO_LOCATION, _init_script, _launch  # noqa: E402
from app.modules.discovery.timeutil import now_kst  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
RESTAURANT_CSV = DATA / "1_식당.csv"
MENU_CSV = DATA / "2_메뉴.csv"
MIN_RATIO = 0.8
SKIPPED = "--limit 로 건너뜀"

# ---------------------------------------------------------------- 페이지 읽기
# 식당 데이터와 메뉴 데이터. 메뉴가 아직 안 왔으면 null (다시 부른다)
_READ = """() => {
  for (const e of [...document.querySelectorAll('.ng-scope')].slice(0, 400)) {
    let s = null;
    try { s = angular.element(e).scope(); } catch (err) {}
    const r = s && s.restaurant;
    if (!r || !r.open_status) continue;
    const m = r.menu || {};
    const items = m.menu || {};
    if (!Object.keys(items).length) return null;
    const sections = (m.menu_sections || []).map(x => ({
      id: String(x.id), type: x.type, title: x.title, items: (x.items || []).map(String)}));
    const menus = Object.values(items).map(x => {
      const p = x.price || {};
      return {
        id: String(x.id), section_id: x.section_id == null ? null : String(x.section_id),
        name: x.name, description: x.description || '',
        soldout: !!x.soldout, liquor: !!x.liquor, review_count: x.review_count || 0,
        origin_price: p.origin_price, final_price: p.final_price,
        final_price_with_required_options: p.final_price_with_required_options,
        required: (x.option_sections || []).some(o => o.required)};
    });
    const rv = r.review || {};
    const tiers = (r.delivery_fee_by_order_amounts || [])
      .map(t => ({order_amount: t.order_amount, delivery_fee: t.delivery_fee}));
    return {
      min_order: r.min_order_amount, delivery_fee: r.delivery_fee, tiers,
      hours: r.open_time_description || null,
      status: (r.open_status || {}).current_open_status || null,
      rating: rv.average_rating, review_count: rv.count, voucher: r.is_allowed_livelihood_voucher,
      sections, menus};
  }
  return null;
}"""


def _read_rows(path: Path) -> tuple[list[str], list[dict]]:
    """CSV를 읽는다. 줄마다 원래 글자(__raw__)와 원래 값(__orig__)을 같이 둔다.

    안 바뀐 줄은 원래 글자 그대로 다시 쓴다 (_write_rows).
    """
    text = path.read_text(encoding="utf-8-sig")
    records, buf = [], ""
    for line in text.splitlines(keepends=True):
        buf += line
        if buf.count('"') % 2 == 0:  # 따옴표가 닫혔으면 한 줄(레코드) 끝
            records.append(buf)
            buf = ""
    if buf:
        records.append(buf)
    fields = next(csv.reader([records[0]]))
    rows = []
    for raw in records[1:]:
        values = next(csv.reader(io.StringIO(raw)), None)
        if not values:
            continue
        row = dict(zip(fields, values, strict=False))
        row["__raw__"] = raw if raw.endswith("\n") else raw + "\n"
        row["__orig__"] = tuple(row.get(f, "") for f in fields)
        rows.append(row)
    return fields, rows


def _write_rows(path: Path, fields: list[str], rows: list[dict]) -> None:
    out = io.StringIO()
    out.write(",".join(fields) + "\n")
    w = csv.writer(out, lineterminator="\n")
    for row in rows:
        values = tuple(row.get(f, "") for f in fields)
        if row.get("__raw__") and values == row.get("__orig__"):
            out.write(row["__raw__"])
        else:
            w.writerow(values)
    path.write_text(out.getvalue(), encoding="utf-8-sig", newline="")


def restaurant_rows() -> list[dict]:
    return _read_rows(RESTAURANT_CSV)[1]


async def collect(rows: list[dict], chrome: bool, concurrency: int = 3) -> dict[str, dict]:
    """식당마다 페이지를 열어 읽는다. {식당id: 읽은 값 또는 {"error": 이유}}"""
    from playwright.async_api import async_playwright

    loc = json.loads(DEMO_LOCATION.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    sem = asyncio.Semaphore(concurrency)

    async def one(i: int, r: dict, context) -> None:
        rid = r["식당id"]
        got: dict | None = None
        async with sem:
            page = await context.new_page()
            try:
                await page.goto(f"{BASE}{rid}/", wait_until="domcontentloaded")
                for _ in range(40):  # 최대 10초
                    if f"/{rid}/" not in page.url:
                        got = {"error": "식당 페이지가 열리지 않음 (요기요 첫 화면으로 이동)"}
                        break
                    got = await page.evaluate(_READ)
                    if got:
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
        out[rid] = got
        note = got.get("error") or f"메뉴 {len(got['menus'])}개"
        print(f"[{len(out)}/{len(rows)}] {r['식당명']} ({rid}): {note}")

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
    return out


# ---------------------------------------------------------------- 비교·합치기 (브라우저 없음)
def _int(v) -> int | None:
    try:
        return None if v in (None, "") else int(float(v))
    except TypeError, ValueError:
        return None


def _tiers_json(tiers: list[dict]) -> str:
    return json.dumps(
        [{"delivery_fee": t["delivery_fee"], "order_amount": t["order_amount"]} for t in tiers],
        ensure_ascii=False,
    )


def _tiers_key(text: str) -> set[tuple[int, int]]:
    try:
        return {(int(t["order_amount"]), int(t["delivery_fee"])) for t in json.loads(text or "[]")}
    except ValueError, KeyError, TypeError:
        return set()


def _clean(text: str | None) -> str:
    """처음 수집 때와 같은 모양으로: 전각 문자（）＋ → 반각 ()+ (NFKC), 공백·줄바꿈은 한 칸으로."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()


def _tf(v: bool) -> str:
    return "t" if v else "f"


def _section_titles(sections: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """(메뉴 번호 → 분류 이름, 묶음 번호 → 분류 이름).

    메뉴가 여러 묶음에 있으면 보통 묶음(LIST)을 먼저 쓴다.
    """
    by_item: dict[str, str] = {}
    for want_list in (True, False):
        for s in sections:
            if (s.get("type") == "LIST") != want_list:
                continue
            for item in s.get("items") or []:
                by_item.setdefault(str(item), s.get("title") or "")
    return by_item, {str(s["id"]): s.get("title") or "" for s in sections}


def _menu_values(m: dict, titles: tuple[dict[str, str], dict[str, str]]) -> dict[str, str]:
    """요기요 메뉴 하나 → CSV에서 고칠 열들."""
    by_item, by_section = titles
    category = _clean(by_item.get(m["id"]) or by_section.get(m.get("section_id") or "", ""))
    final = _int(m.get("final_price"))
    origin = _int(m.get("origin_price"))
    with_req = _int(m.get("final_price_with_required_options"))
    values = {
        "메뉴명": _clean(m.get("name")),
        "설명": _clean(m.get("description")),
        "정가": str(origin if origin is not None else final or 0),
        "판매가": str(final if final is not None else origin or 0),
        "필수옵션포함가": str(with_req if with_req is not None else final or 0),
        "필수옵션있음": _tf(m.get("required")),
        "품절": _tf(m.get("soldout")),
        "주류": _tf(m.get("liquor")),
        "메뉴리뷰수": str(_int(m.get("review_count")) or 0),
    }
    if category:
        values["메뉴분류"] = category
    return values


def _set(row: dict, changes: list[dict], col: str, new: str, show: bool) -> None:
    if (row.get(col) or "") != new:
        if show:
            changes.append({"field": col, "old": row.get(col) or "", "new": new})
        row[col] = new


def usable(old_menus: list[dict], obs: dict | None) -> str | None:
    """이 식당 결과를 써도 되는가. 안 되면 이유."""
    if not obs:
        return "결과 없음"
    if obs.get("error"):
        return obs["error"]
    menus = obs.get("menus") or []
    if not menus:
        return "메뉴가 하나도 없음"
    if len(old_menus) >= 5 and len(menus) < len(old_menus) * 0.5:
        return f"메뉴가 {len(old_menus)}개 → {len(menus)}개로 절반 넘게 줄어 덜 읽은 것으로 봄"
    return None


def merge(
    restaurants: list[dict], menus: list[dict], observed: dict[str, dict], collected_at: str
) -> dict:
    """읽은 값(observed)을 CSV 줄에 합친다. 줄(dict)을 그 자리에서 고치고 보고서를 돌려준다."""
    menus_by_rest: dict[str, list[dict]] = {}
    for row in menus:
        menus_by_rest.setdefault(row["식당id"], []).append(row)

    report = {
        "collected_at": collected_at,
        "total": len(restaurants),
        "read": 0,
        "unread": [],
        "restaurants": [],
    }
    for rest in restaurants:
        rid, name = rest["식당id"], rest["식당명"]
        old = menus_by_rest.get(rid, [])
        obs = observed.get(rid)
        why = usable(old, obs)
        if why:
            report["unread"].append({"restaurant_id": rid, "name": name, "reason": why})
            continue
        report["read"] += 1
        r = {
            "restaurant_id": rid,
            "name": name,
            "fields": [],
            "price": [],
            "soldout": [],
            "back": [],
            "removed": [],
            "new_id": [],
            "renamed": [],
            "new": [],
        }

        # 식당 값
        def change(col: str, new: str, *, show: bool = True, rest=rest, r=r) -> None:
            _set(rest, r["fields"], col, new, show)

        if _int(obs.get("min_order")) is not None:
            change("최소주문금액", str(_int(obs["min_order"])))
        if _int(obs.get("delivery_fee")) is not None:
            change("기본배달비", str(_int(obs["delivery_fee"])))
        if obs.get("tiers") and _tiers_key(rest.get("주문금액별배달비", "")) != _tiers_key(
            _tiers_json(obs["tiers"])
        ):
            change("주문금액별배달비", _tiers_json(obs["tiers"]))
        if obs.get("hours") and "영업시간" in rest:
            change("영업시간", obs["hours"])
        if obs.get("voucher") is not None:
            change("지역화폐가능", _tf(obs["voucher"]))
        if obs.get("rating") is not None:
            change("별점", f"{float(obs['rating']):.1f}", show=False)
        if obs.get("review_count") is not None:
            change("리뷰수", str(_int(obs["review_count"])), show=False)
        change("메뉴수", str(len(obs["menus"])), show=False)
        rest["수집시각"] = collected_at

        # 메뉴
        titles = _section_titles(obs.get("sections") or [])
        seen = {m["id"]: m for m in obs["menus"]}
        old_ids = {row["메뉴id"] for row in old}
        unmatched = {m["id"]: m for m in obs["menus"] if m["id"] not in old_ids}
        by_name: dict[str, list[dict]] = {}
        for m in unmatched.values():
            by_name.setdefault(_clean(m.get("name")), []).append(m)
        for row in old:
            m = seen.get(row["메뉴id"])
            if m is None:
                same = by_name.get(row["메뉴명"], [])
                if len(same) == 1 and same[0]["id"] in unmatched:  # 같은 이름이 새 번호로
                    m = same[0]
                    unmatched.pop(m["id"])
                    r["new_id"].append(
                        {"name": row["메뉴명"], "old_id": row["메뉴id"], "new_id": m["id"]}
                    )
                    row["메뉴id"] = m["id"]
                else:
                    if row.get("품절") != "t":
                        r["removed"].append({"menu_id": row["메뉴id"], "name": row["메뉴명"]})
                        row["품절"] = "t"
                    continue
            values = _menu_values(m, titles)
            if (
                row.get("판매가") != values["판매가"]
                or row.get("필수옵션포함가") != values["필수옵션포함가"]
            ):
                r["price"].append(
                    {
                        "menu_id": m["id"],
                        "name": values["메뉴명"],
                        "old": _int(row.get("필수옵션포함가")),
                        "new": _int(values["필수옵션포함가"]),
                    }
                )
            if row.get("품절") != values["품절"]:
                (r["soldout"] if values["품절"] == "t" else r["back"]).append(
                    {"menu_id": m["id"], "name": values["메뉴명"]}
                )
            if row.get("메뉴명") != values["메뉴명"]:
                r["renamed"].append(
                    {"menu_id": m["id"], "old": row.get("메뉴명"), "new": values["메뉴명"]}
                )
            row.update(values)
        for m in unmatched.values():
            r["new"].append(
                {
                    "menu_id": m["id"],
                    "name": _clean(m.get("name")),
                    "price": _int(m.get("final_price_with_required_options")),
                }
            )
        if any(r[k] for k in r if k not in ("restaurant_id", "name")):
            report["restaurants"].append(r)
    report["ratio"] = report["read"] / report["total"] if report["total"] else 0.0
    return report


# ---------------------------------------------------------------- 보고서
_KINDS = [
    ("fields", "식당 정보"),
    ("price", "가격 바뀜"),
    ("soldout", "품절됨"),
    ("back", "품절 풀림"),
    ("removed", "사라짐 → 품절 처리"),
    ("new_id", "메뉴 번호 바뀜"),
    ("renamed", "이름 바뀜"),
    ("new", "새 메뉴 (추가 안 함)"),
]


def _line(kind: str, x: dict) -> str:
    if kind == "fields":
        return f"{x['field']}: {x['old'] or '없음'} → {x['new']}"
    if kind == "price":
        return (
            f"{x['name']}: {x['old']:,}원 → {x['new']:,}원"
            if x["old"] is not None
            else f"{x['name']}: {x['new']:,}원"
        )
    if kind == "new_id":
        return f"{x['name']}: {x['old_id']} → {x['new_id']}"
    if kind == "renamed":
        return f"{x['old']} → {x['new']}"
    if kind == "new":
        return f"{x['name']} ({(x['price'] or 0):,}원)"
    return x["name"]


def summary(report: dict) -> str:
    totals = {k: sum(len(r[k]) for r in report["restaurants"]) for k, _ in _KINDS}
    lines = [f"읽은 식당 {report['read']}/{report['total']}곳 ({report['ratio']:.0%})"]
    lines.append(" · ".join(f"{label} {totals[k]}" for k, label in _KINDS))
    skipped = [u for u in report["unread"] if u["reason"] == SKIPPED]
    failed = [u for u in report["unread"] if u["reason"] != SKIPPED]
    if skipped:
        lines.append(f"--limit 로 건너뛴 식당 {len(skipped)}곳")
    if failed:
        lines.append(f"못 읽은 식당 {len(failed)}곳 (기존 값 유지):")
        lines += [f"  - {u['name']} ({u['restaurant_id']}): {u['reason']}" for u in failed[:10]]
        if len(failed) > 10:
            lines.append(f"  … 외 {len(failed) - 10}곳")
    return "\n".join(lines)


def markdown(report: dict) -> str:
    out = [f"# 식당·메뉴 재수집 결과 ({report['collected_at']})", "", summary(report), ""]
    for r in report["restaurants"]:
        out.append(f"## {r['name']} ({r['restaurant_id']})")
        for kind, label in _KINDS:
            if r[kind]:
                out.append(f"- **{label}**")
                out += [f"  - {_line(kind, x)}" for x in r[kind]]
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- 쓰기
def apply(
    observed: dict[str, dict], collected_at: str, out_dir: Path, write: bool, min_ratio: float
) -> int:
    rfields, rests = _read_rows(RESTAURANT_CSV)
    mfields, menus = _read_rows(MENU_CSV)
    report = merge(rests, menus, observed, collected_at)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    md = out_dir / f"recollect-{stamp}-report.md"
    md.write_text(markdown(report), encoding="utf-8")
    print()
    print(summary(report))
    print(f"자세한 보고서: {md}")
    if not write:
        print("CSV는 바꾸지 않았어요. 쓰려면 --write 를 붙이세요.")
        return 0
    if report["ratio"] < min_ratio:
        print(
            f"읽은 식당이 {min_ratio:.0%} 미만이라 CSV를 쓰지 않았어요. "
            "요기요 접속 상태를 확인하고 다시 실행하세요."
        )
        return 2
    backup = out_dir / f"catalog-backup-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(RESTAURANT_CSV, backup / RESTAURANT_CSV.name)
    shutil.copy2(MENU_CSV, backup / MENU_CSV.name)
    _write_rows(RESTAURANT_CSV, rfields, rests)
    _write_rows(MENU_CSV, mfields, menus)
    print(f"data/{RESTAURANT_CSV.name}, data/{MENU_CSV.name}에 썼어요. 원래 파일: {backup}/")
    print("서버를 다시 켜면 DB에 반영돼요.")
    return 0


# ---------------------------------------------------------------- 원본 확인 (개발용)
_PROBE = """() => {
  const sample = (v, d) => {
    if (v === null || typeof v !== 'object') return typeof v === 'string' ? v.slice(0, 120) : v;
    if (d > 5) return '…';
    if (Array.isArray(v)) return {_len: v.length, _first: v.slice(0, 2).map(x => sample(x, d + 1))};
    const r = {};
    for (const k of Object.keys(v)) { if (!k.startsWith('$')) r[k] = sample(v[k], d + 1); }
    return r;
  };
  for (const e of [...document.querySelectorAll('.ng-scope')].slice(0, 400)) {
    let s = null; try { s = angular.element(e).scope(); } catch (err) {}
    if (s && s.restaurant && s.restaurant.open_status) return sample(s.restaurant, 0);
  }
  return null;
}"""


async def probe(limit: int, out_dir: Path, chrome: bool) -> Path:
    from playwright.async_api import async_playwright

    loc = json.loads(DEMO_LOCATION.read_text(encoding="utf-8"))
    rows = sorted(restaurant_rows(), key=lambda r: -(_int(r["리뷰수"]) or 0))[:limit]
    out = []
    async with async_playwright() as p:
        browser = await _launch(p, headless=False, channel="chrome" if chrome else None)
        try:
            context = await browser.new_context(
                locale="ko-KR", viewport={"width": 1280, "height": 900}
            )
            await context.add_init_script(_init_script(loc))
            page = await context.new_page()
            for r in rows:
                await page.goto(f"{BASE}{r['식당id']}/", wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                out.append(
                    {
                        "restaurant_id": r["식당id"],
                        "name": r["식당명"],
                        "restaurant": await page.evaluate(_PROBE),
                    }
                )
                print(f"{r['식당명']} ({r['식당id']})")
        finally:
            await browser.close()
    path = out_dir / f"catalog-probe-{datetime.now():%Y%m%d-%H%M}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def find_saved(name: str, out_dir: Path) -> Path | None:
    """--from 값 → 결과 파일. latest 이면 가장 최근 것. 없으면 있는 파일을 보여 준다."""
    saved = sorted(out_dir.glob("recollect-*.json"))
    if name == "latest":
        if saved:
            return saved[-1]
    elif Path(name).exists():
        return Path(name)
    print(f"결과 파일을 찾지 못했어요: {name}")
    if saved:
        print("있는 파일 (--from latest 로 가장 최근 것을 쓸 수 있어요):")
        print("\n".join(f"  {p}" for p in saved[-5:]))
    else:
        print("먼저 --collect 로 읽어 주세요.")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="식당·메뉴 데이터 재수집 (시연 전에 손으로 실행)")
    ap.add_argument("--collect", action="store_true", help="요기요에서 식당 전부를 다시 읽는다")
    ap.add_argument(
        "--from",
        dest="source",
        metavar="JSON",
        help="--collect가 남긴 결과 파일 (latest = 가장 최근). 다시 안 읽음",
    )
    ap.add_argument("--write", action="store_true", help="결과를 data/ CSV에 쓴다")
    ap.add_argument(
        "--limit", type=int, help="리뷰 많은 N곳만 읽는다 (시험용, 이때는 --write 불가)"
    )
    ap.add_argument(
        "--min-ratio",
        type=float,
        default=MIN_RATIO,
        help="이 비율 미만으로 읽으면 쓰지 않음 (기본 0.8)",
    )
    ap.add_argument(
        "--probe", type=int, metavar="N", help="리뷰 많은 N곳의 페이지 데이터 원본만 저장 (개발용)"
    )
    ap.add_argument("--chrome", action="store_true", help="설치된 Chrome으로 실행")
    ap.add_argument("--out", default="cart_runs")
    a = ap.parse_args()
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if a.probe:
        print(f"기록: {asyncio.run(probe(a.probe, out_dir, a.chrome))}")
        return 0
    if a.source:
        source = find_saved(a.source, out_dir)
        if source is None:
            return 1
        print(f"읽은 원본: {source}")
        saved = json.loads(source.read_text(encoding="utf-8"))
        return apply(saved["observed"], saved["collected_at"], out_dir, a.write, a.min_ratio)
    if a.collect:
        if a.limit and a.write:
            print(
                "--limit 로 일부만 읽을 때는 --write 를 쓸 수 없어요 "
                "(나머지 식당을 못 읽은 것으로 봐요)."
            )
            return 1
        rows = restaurant_rows()
        if a.limit:
            rows = sorted(rows, key=lambda r: -(_int(r["리뷰수"]) or 0))[: a.limit]
        collected_at = now_kst().strftime("%Y-%m-%d %H:%M:%S") + "+09"
        observed = asyncio.run(collect(rows, a.chrome))
        raw = out_dir / f"recollect-{datetime.now():%Y%m%d-%H%M}.json"
        raw.write_text(
            json.dumps({"collected_at": collected_at, "observed": observed}, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"읽은 원본: {raw}")
        if a.limit:
            observed = {
                **{r["식당id"]: {"error": SKIPPED} for r in restaurant_rows()},
                **observed,
            }
        return apply(observed, collected_at, out_dir, a.write, a.min_ratio)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
