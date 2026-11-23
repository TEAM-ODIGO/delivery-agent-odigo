"""규칙만 쓸 때와 LLM을 켰을 때 추천 결과 비교 (개발용).

같은 문장을 세 방식으로 조건을 뽑아 검색하고 나란히 보여 준다.
  1) 규칙      : 지금 LLM_ENABLED=false 일 때
  2) 규칙+LLM  : LLM_ENABLED=true 일 때 실제 서비스 동작
                (규칙이 비운 칸만, 문장에 근거가 있을 때만 LLM 값)
  3) LLM 우선  : 실험용. LLM이 낸 값을 그대로 믿으면 어떻게 되는지

준비: .env에 LLM_PROVIDER=openrouter, OPENROUTER_API_KEY (이슈 #62)
  (로컬 Ollama로 볼 때는 LLM_PROVIDER=ollama, ollama serve, ollama pull qwen2.5:7b)

실행 (backend 폴더):
    uv run --env-file .env python scripts/compare_llm.py                  # 기본 문장 20개
    uv run --env-file .env python scripts/compare_llm.py "마라탕 1명 2만원" "치킨이랑 떡볶이 3명 5만원"
    uv run --env-file .env python scripts/compare_llm.py --file my.txt     # 한 줄에 문장 하나
    uv run --env-file .env python scripts/compare_llm.py --model openai/gpt-5-mini  # 모델 바꿔 보기

모델 고르기 (OpenRouter 모델 여러 개를 같은 문장으로 돌려 조건 추출 결과·속도·실패 수를 나란히 본다):
    uv run --env-file .env python scripts/compare_llm.py --models openai/gpt-4.1-mini,openai/gpt-5-mini

- 서버 DB(dev.db)는 건드리지 않는다. data/ CSV로 임시 DB를 만들어 검색한다.
- 영업시간 필터는 끈다 (시각에 따라 결과가 바뀌지 않게). --now 를 주면 지금 시각 기준으로 거른다.
- 결과: 터미널 요약 + cart_runs/compare-llm-날짜.md
"""

import argparse
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

os.environ["LLM_ENABLED"] = "true"  # 설정을 읽기 전에 켠다
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ 를 import 경로에 추가

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.db import init_db, make_engine  # noqa: E402
from app.modules.discovery import llm, loader  # noqa: E402
from app.modules.discovery.extractor import extract_by_rules, normalize_cuisine  # noqa: E402
from app.modules.discovery.search import search  # noqa: E402
from app.modules.discovery.service import _merge  # noqa: E402
from app.modules.discovery.timeutil import now_kst  # noqa: E402

DEFAULT_MESSAGES = [
    "마라탕 1명 2만원",
    "마라탕 먹고 싶어 2명 3만원",
    "치킨 2명 4만원",
    "3명이 먹을 치킨 배달비 포함 4만원 이하",
    "피자 4명 6만원",
    "떡볶이 2명 3만원",
    "짜장면 탕수육 2명 3만원",
    "치킨이랑 떡볶이 3명 5만원",
    "피자랑 치킨 4명 7만원",
    "타코야끼 3명 3만원",
    "타코아끼 3명 3만원",
    "혼자 저녁 1만원",
    "4명 저녁 8만원",
    "둘이서 야식 3만원",
    "안 매운 걸로 아내랑 둘이 3만원",
    "국물 있는 거 3명 4만원",
    "땅콩 빼고 2명 3만원 중식",
    "인당 만오천원 3명 한식",
    "부모님 두 분 드실 담백한 저녁 4만원",
    "지역화폐로 2명 치킨 4만원",
]
KEYS = [
    "people",
    "budget_krw",
    "cuisine",
    "menu_keywords",
    "taste_preferences",
    "max_spice_level",
    "excluded_ingredients",
    "max_delivery_minutes",
    "payment_requirements",
]


def llm_first(rule: dict, extraction) -> dict:
    """실험용: LLM이 값을 냈으면 규칙보다 LLM을 믿는다."""
    if extraction is None:
        return rule
    out = dict(rule)
    for key in ("people", "budget_krw", "max_spice_level", "max_delivery_minutes"):
        if getattr(extraction, key) is not None:
            out[key] = getattr(extraction, key)
    cuisine = normalize_cuisine(extraction.cuisine)
    if cuisine:
        out["cuisine"] = cuisine
    if extraction.menu_keywords:
        out["menu_keywords"] = list(dict.fromkeys(extraction.menu_keywords))
    if extraction.taste_preferences:
        out["taste_preferences"] = list(dict.fromkeys(extraction.taste_preferences))
    if extraction.excluded_ingredients:
        out["excluded_ingredients"] = list(dict.fromkeys(extraction.excluded_ingredients))
    if extraction.local_currency_only:
        out["payment_requirements"] = {"local_currency_only": True}
    return out


def short(c: dict) -> str:
    parts = []
    for k in KEYS:
        v = c.get(k)
        if k == "payment_requirements":
            v = "지역화폐만" if (v or {}).get("local_currency_only") else None
        if v not in (None, [], {}):
            parts.append(f"{k}={v}")
    return ", ".join(parts) or "(없음)"


def top_lines(bodies: list[dict], n: int = 3) -> list[str]:
    out = []
    for b in bodies[:n]:
        items = " + ".join(f"{i['name'][:18]}×{i['quantity']}" for i in b["items"])
        flag = "" if b["meets_all_constraints"] else " (조건 일부 벗어남)"
        out.append(f"{b['restaurant']['name'][:16]} {b['estimated_total_krw']:,}원 · {items}{flag}")
    return out or ["(후보 없음)"]


def compare_models(models: list[str], msgs: list[str], out: Path) -> int:
    """OpenRouter 모델별 조건 추출 결과·시간·실패를 나란히 본다 (검색은 하지 않음)."""
    settings = llm.get_settings()
    settings.llm_provider = "openrouter"
    status = llm.check_llm()
    print(f"OpenRouter 상태: {status}")
    if status != "UP":
        print("키를 확인해 주세요 (.env의 OPENROUTER_API_KEY).")
        return 1
    tmp = Path(tempfile.mkdtemp()) / "models.db"
    engine = make_engine(f"sqlite:///{tmp}")
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    stats = {m: {"fail": 0, "times": [], "changed": 0} for m in models}
    md = [f"# OpenRouter 모델 비교 ({datetime.now():%Y-%m-%d %H:%M})", ""]
    with sessions() as s:
        for i, m in enumerate(msgs, 1):
            rule, notes = extract_by_rules(m)
            print(f"\n[{i}] {m}\n   규칙      : {short(rule)}")
            md += [f"## {i}. {m}", "", f"- 규칙: {short(rule)}"]
            for model in models:
                settings.openrouter_model = model
                started = time.monotonic()
                extraction = llm.extract_conditions(s, f"MDL{i:03d}", m)
                took = time.monotonic() - started
                st = stats[model]
                st["times"].append(took)
                if extraction is None:
                    st["fail"] += 1
                    s.flush()
                    last = s.query(llm.LlmCall).filter_by(request_id=f"MDL{i:03d}").order_by(
                        llm.LlmCall.id.desc()).first()
                    why = (last.error or "")[:120] if last else ""
                    st.setdefault("errors", []).append(why)
                    line = f"실패 (규칙만) — {why}"
                else:
                    merged, _, _ = _merge(rule, notes, extraction, m)
                    st["changed"] += short(merged) != short(rule)
                    line = short(merged)
                print(f"   {model}: {line}  ({took:.1f}초)")
                md.append(f"- {model} ({took:.1f}초): {line}")
            md.append("")
    lines = ["| 모델 | 실패 | 규칙과 달라진 문장 | 평균 | 최대 |", "|---|---:|---:|---:|---:|"]
    for model, st in stats.items():
        avg = sum(st["times"]) / len(st["times"])
        lines.append(f"| {model} | {st['fail']} | {st['changed']} | {avg:.1f}초 | {max(st['times']):.1f}초 |")
    print("\n" + "\n".join(lines))
    for model, st in stats.items():
        if st.get("errors"):
            print(f"{model} 실패 이유 (첫 번째): {st['errors'][0]}")
    md[2:2] = lines + [""]
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"compare-models-{datetime.now():%Y%m%d-%H%M}.md"
    path.write_text("\n".join(md), encoding="utf-8")
    print(f"기록: {path}  (비용은 OpenRouter 대시보드 Activity에서 확인)")
    engine.dispose()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="규칙 vs LLM 추천 비교")
    ap.add_argument("messages", nargs="*")
    ap.add_argument("--file", help="한 줄에 문장 하나")
    ap.add_argument("--now", action="store_true", help="지금 시각 기준으로 영업시간 필터 적용")
    ap.add_argument("--out", default="cart_runs")
    ap.add_argument("--model", help="이 모델로 (OPENROUTER_MODEL·OLLAMA_MODEL 대신)")
    ap.add_argument("--models", help="OpenRouter 모델 여러 개를 쉼표로 (예: openai/gpt-4.1-mini,openai/gpt-5-mini)")
    a = ap.parse_args()
    settings = llm.get_settings()
    if a.model:
        if settings.llm_provider == "openrouter":
            settings.openrouter_model = a.model
        else:
            settings.ollama_model = a.model
    msgs = a.messages or DEFAULT_MESSAGES
    if a.file:
        msgs = [
            x.strip() for x in Path(a.file).read_text(encoding="utf-8").splitlines() if x.strip()
        ]

    if a.models:
        models = [m.strip() for m in a.models.split(",") if m.strip()]
        bad = [m for m in models if "/" not in m or " " in m]
        if bad:
            raise SystemExit(f"모델 이름은 '회사/모델' 모양이어야 해요: {bad} (여러 개는 쉼표로)")
        return compare_models(models, msgs, Path(a.out))

    status = llm.check_llm()
    print(f"LLM 상태: {status} ({settings.llm_provider}:{settings.model})")
    if status != "UP":
        why = {
            "DOWN": "Ollama가 꺼져 있어요 (ollama serve)",
            "MODEL_MISSING": "모델이 없어요 (ollama pull qwen2.5:7b)",
            "NO_API_KEY": ".env에 OPENROUTER_API_KEY가 없어요",
            "INVALID_KEY": "OpenRouter 키가 틀렸어요",
        }
        print(f"{why.get(status, status)}. 이대로면 2·3번이 규칙과 같게 나와요.")

    tmp = Path(tempfile.mkdtemp()) / "compare.db"
    engine = make_engine(f"sqlite:///{tmp}")
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as s:
        loader.load_if_empty(s)
    now = now_kst() if a.now else None

    md = [
        f"# 규칙 vs LLM 추천 비교 ({datetime.now():%Y-%m-%d %H:%M})",
        "",
        f"- LLM 상태: {status} ({settings.llm_provider}:{settings.model})",
        "",
    ]
    changed = failed = 0
    times = []
    with sessions() as s:
        for i, m in enumerate(msgs, 1):
            rule, notes = extract_by_rules(m)
            started = time.monotonic()
            extraction = llm.extract_conditions(s, f"CMP{i:03d}", m)
            times.append(time.monotonic() - started)
            failed += extraction is None
            merged, _, _ = _merge(rule, notes, extraction, m)
            first = llm_first(rule, extraction)
            modes = [("규칙", rule), ("규칙+LLM", merged), ("LLM 우선", first)]
            changed += short(merged) != short(rule)

            print(f"\n[{i}] {m}  (LLM {times[-1]:.1f}초{'' if extraction else ', 실패'})")
            md += [
                f"## {i}. {m}",
                "",
                f"LLM {times[-1]:.1f}초{'' if extraction else ' · 실패(규칙만)'}",
                "",
            ]
            if extraction is not None:
                raw = {
                    k: v for k, v in extraction.model_dump().items() if v not in (None, [], False)
                }
                print(f"   LLM 원본: {raw}")
                md += [f"- LLM 원본: `{raw}`", ""]
            seen = {}
            for name, cons in modes:
                key = short(cons)
                if key in seen:
                    print(f"   {name:8}: {seen[key]} 결과와 같음")
                    md.append(f"- **{name}**: {seen[key]} 결과와 같음")
                    continue
                seen[key] = name
                bodies, _ = search(s, cons, 6, now=now)
                print(f"   {name:8}: {key}")
                md.append(f"- **{name}**: {key}")
                for line in top_lines(bodies):
                    print(f"              {line}")
                    md.append(f"    - {line}")
            md.append("")

    avg = sum(times) / len(times) if times else 0
    summary = (
        f"문장 {len(msgs)}개 · 규칙+LLM이 조건을 바꾼 문장 {changed}개 · LLM 실패 {failed}개 · "
        f"LLM 평균 {avg:.1f}초 (최대 {max(times, default=0):.1f}초)"
    )
    print("\n" + summary)
    md.insert(3, f"- {summary}")
    Path(a.out).mkdir(parents=True, exist_ok=True)
    path = Path(a.out) / f"compare-llm-{datetime.now():%Y%m%d-%H%M}.md"
    path.write_text("\n".join(md), encoding="utf-8")
    print(f"기록: {path}")
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
