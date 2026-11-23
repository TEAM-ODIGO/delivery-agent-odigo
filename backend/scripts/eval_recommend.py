"""추천 품질 평가 (이슈 #37). 같은 문장 세트로 개선 전후를 숫자로 비교한다.

    uv run python scripts/eval_recommend.py                       # 표 + 요약
    uv run python scripts/eval_recommend.py --save before.json    # 결과 저장
    uv run python scripts/eval_recommend.py --compare before.json # 저장한 결과와 비교

- 조건은 규칙으로 뽑는다 (LLM 없이, 매번 같은 결과).
- data/ CSV로 임시 DB를 만든다. 서버 DB(dev.db)는 건드리지 않는다.
- 문장마다 주문 시각을 정해 영업시간 필터를 건다 (밤에 닫힌 가게 상황도 재현).

지표 (상위 3개 후보 기준)
- 1위 관련: 1위 후보에 요청한 메뉴나 비슷한 메뉴가 있는 비율 (말한 메뉴가 맨 앞에 오는지)
- 관련 메뉴: 요청한 메뉴나 비슷한 메뉴(문장마다 정한 단어)가 장바구니에 있는 후보 비율
- 2종 이상: 2명 이상 요청에서 서로 다른 메뉴를 2개 이상 담은 후보 비율
- 과하게 반복: 한 메뉴를 인원보다 많이 담은 후보 비율 (2명에 짜장면 3개 등) — 낮을수록 좋음
- 예산 안: 예산 안에 든 후보 비율
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ["LLM_ENABLED"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ 를 import 경로에 추가

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.db import init_db, make_engine  # noqa: E402
from app.modules.discovery import loader  # noqa: E402
from app.modules.discovery.extractor import extract_by_rules  # noqa: E402
from app.modules.discovery.search import search  # noqa: E402
from app.modules.discovery.timeutil import KST  # noqa: E402

TOP = 3
# (문장, 주문 시각 HH:MM, 관련 메뉴로 볼 단어들 — 비어 있으면 '관련 메뉴'를 세지 않음)
CASES = [
    ("마라탕 먹고 싶어 2명 3만원", "12:30", ["마라"]),
    ("마라탕 먹고 싶어 2명 3만원", "23:00", ["마라", "짬뽕", "얼큰", "매운"]),
    ("마라탕 1명 2만원", "12:30", ["마라"]),
    ("짬뽕 2명 3만원", "12:30", ["짬뽕"]),
    ("짜장면 탕수육 2명 3만원", "12:30", ["짜장", "탕수육"]),
    ("탕수육 3명 4만원", "12:30", ["탕수육", "꿔바로우"]),
    ("떡볶이 2명 3만원", "12:30", ["떡볶이"]),
    ("떡볶이 2명 3만원", "02:00", ["떡볶이", "매운"]),
    ("족발 3명 5만원", "19:00", ["족발", "보쌈"]),
    ("초밥 2명 5만원", "12:30", ["초밥", "스시"]),
    ("돈까스 1명 1만5천원", "12:30", ["까스", "카츠"]),
    ("국밥 1명 1만5천원", "12:30", ["국밥"]),
    ("김치찌개 2명 3만원", "12:30", ["김치찌개", "찌개"]),
    ("부대찌개 3명 4만원", "19:00", ["부대", "찌개"]),
    ("쌀국수 1명 2만원", "12:30", ["쌀국수"]),
    ("타코야끼 3명 3만원", "19:00", ["타코야끼"]),
    ("치킨이랑 떡볶이 3명 5만원", "19:00", ["떡볶이"]),
    ("치킨 2명 4만원", "19:00", []),
    ("3명이 먹을 치킨 배달비 포함 4만원 이하", "19:00", []),
    ("피자 4명 6만원", "19:00", []),
    ("한식 2명 3만원", "12:30", []),
    ("중식 3명 4만원", "12:30", []),
    ("4명 저녁 8만원", "19:00", []),
    ("혼자 저녁 1만원", "19:00", []),
    ("둘이서 야식 3만원", "23:00", []),
    ("안 매운 걸로 둘이 3만원", "19:00", []),
    ("매운 거 2명 3만원", "19:00", ["매운", "마라", "불닭", "얼큰", "떡볶이", "짬뽕"]),
    ("국물 있는 거 3명 4만원", "19:00", ["국", "탕", "찌개", "짬뽕", "라면", "칼국수", "전골"]),
    ("부모님 두 분 드실 담백한 저녁 4만원", "18:00", []),
    ("햄버거 2명 2만원", "12:30", ["버거"]),
]


def at(hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(2026, 11, 20, h, m, tzinfo=KST)


def measure(bodies: list[dict], constraints: dict, words: list[str]) -> dict:
    top = bodies[:TOP]
    people = constraints.get("people") or 1
    budget = constraints.get("budget_krw")
    out = {"count": len(bodies), "related": None, "multi": None, "overfill": 0, "in_budget": 0}
    if not top:
        return out
    if words:
        out["related"] = sum(
            any(any(w in i["name"] for w in words) for i in b["items"]) for b in top
        ) / len(top)
    if people >= 2:
        out["multi"] = sum(len(b["items"]) >= 2 for b in top) / len(top)
    out["overfill"] = sum(max(i["quantity"] for i in b["items"]) > people for b in top) / len(top)
    out["in_budget"] = sum(budget is None or b["estimated_total_krw"] <= budget for b in top) / len(
        top
    )
    return out


def avg(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def top1_related(rows: list[dict]) -> float | None:
    """저장된 1위 후보 글자로 계산한다 (예전 결과 파일과도 비교할 수 있게)."""
    words_of = {(m, t): w for m, t, w in CASES}
    hits = []
    for r in rows:
        words = words_of.get((r["message"], r["at"]))
        if not words:
            continue
        top = r["top"][0] if r["top"] else ""
        menus = top.split(": ", 1)[-1]
        hits.append(any(w in menus for w in words))
    return sum(hits) / len(hits) if hits else None


def summary(rows: list[dict]) -> dict:
    return {
        "top1": top1_related(rows),
        "related": avg([r["m"]["related"] for r in rows]),
        "multi": avg([r["m"]["multi"] for r in rows]),
        "overfill": avg([r["m"]["overfill"] for r in rows if r["m"]["count"]]),
        "in_budget": avg([r["m"]["in_budget"] for r in rows if r["m"]["count"]]),
        "no_candidate": sum(1 for r in rows if not r["m"]["count"]),
    }


def pct(v: float | None) -> str:
    return "  - " if v is None else f"{v * 100:3.0f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description="추천 품질 평가")
    ap.add_argument("--save", help="결과를 JSON으로 저장")
    ap.add_argument("--compare", help="저장한 JSON과 비교")
    ap.add_argument("--quiet", action="store_true", help="문장별 결과 생략")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp()) / "eval.db"
    engine = make_engine(f"sqlite:///{tmp}")
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as s:
        loader.load_if_empty(s)

    rows = []
    with sessions() as s:
        for message, hhmm, words in CASES:
            constraints, _ = extract_by_rules(message)
            bodies, _ = search(s, constraints, 6, now=at(hhmm))
            m = measure(bodies, constraints, words)
            tops = [
                f"{b['restaurant']['name'][:12]}: "
                + " + ".join(f"{i['name'][:14]}×{i['quantity']}" for i in b["items"])
                for b in bodies[:TOP]
            ]
            rows.append({"message": message, "at": hhmm, "m": m, "top": tops})
            if not a.quiet:
                print(
                    f"\n{message} ({hhmm})  관련 {pct(m['related'])} · 2종 {pct(m['multi'])} · 반복 {pct(m['overfill'])}"
                )
                for t in tops or ["(후보 없음)"]:
                    print(f"    {t}")
    engine.dispose()

    s_now = summary(rows)
    print("\n요약 (상위 3개 후보 평균)")
    print(
        f"  1위 관련 {pct(s_now['top1'])} · 관련 메뉴 {pct(s_now['related'])} · 2종 이상 {pct(s_now['multi'])} · "
        f"과하게 반복 {pct(s_now['overfill'])} · 예산 안 {pct(s_now['in_budget'])} · 후보 없음 {s_now['no_candidate']}건"
    )

    if a.compare:
        before = json.loads(Path(a.compare).read_text(encoding="utf-8"))
        s_b = before["summary"]
        s_b["top1"] = top1_related(before["rows"])
        print(f"\n비교 ({a.compare} → 지금)")
        for key, label in [
            ("top1", "1위 관련"),
            ("related", "관련 메뉴"),
            ("multi", "2종 이상"),
            ("overfill", "과하게 반복"),
            ("in_budget", "예산 안"),
        ]:
            print(f"  {label}: {pct(s_b[key])} → {pct(s_now[key])}")
        print(f"  후보 없음: {s_b['no_candidate']}건 → {s_now['no_candidate']}건")
        old = {(r["message"], r["at"]): r for r in before["rows"]}
        for r in rows:
            o = old.get((r["message"], r["at"]))
            if o and o["top"] != r["top"]:
                print(f"\n  바뀜: {r['message']} ({r['at']})")
                print("    전: " + (" | ".join(o["top"]) or "(없음)"))
                print("    후: " + (" | ".join(r["top"]) or "(없음)"))
    if a.save:
        Path(a.save).write_text(
            json.dumps({"summary": s_now, "rows": rows}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"\n저장: {a.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
