"""점주 대시보드 시연 데이터 (이슈 #50). 실제 주문이 몇 건뿐이라 대시보드가 비어 보이지 않게 한다.

예시 요청을 지난 14일의 여러 시각(점심·저녁·밤)으로 A 검색에 돌려 진짜 후보·제외 사유를 쌓고,
화면 표시·선택·주문 완료(모의)를 흉내 내 O 기록도 남긴다. 요청 ID는 모두 DEMO- 로 시작하고,
점주 화면에는 "시연 데이터"라고 표시된다.

    uv run --env-file .env python scripts/seed_owner_demo.py            # 300건 (1~3분)
    uv run --env-file .env python scripts/seed_owner_demo.py --count 100
    uv run --env-file .env python scripts/seed_owner_demo.py --clear    # DEMO- 기록만 지움

- 서버 .env의 DATABASE_URL(DB)에 쓴다. 서버를 켜 둔 채 돌려도 된다.
- LLM은 쓰지 않는다 (규칙으로 조건 추출). 같은 --seed면 같은 데이터가 나온다.
"""

import argparse
import os
import random
import sys
from datetime import timedelta
from pathlib import Path

os.environ["LLM_ENABLED"] = "false"
os.environ.setdefault("DISCOVERY_OPENING_HOURS_FILTER", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ 를 import 경로에 추가

from sqlalchemy import delete, select  # noqa: E402

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.modules.discovery import loader  # noqa: E402
from app.modules.discovery import service as a_service  # noqa: E402
from app.modules.discovery.models import (  # noqa: E402
    Candidate,
    ConstraintsHistory,
    DiscoveryRequest,
    Exclusion,
    LlmCall,
)
from app.modules.discovery.schemas import CreateRequestBody  # noqa: E402
from app.modules.discovery.timeutil import now_kst  # noqa: E402
from app.modules.orchestrator.models import CandidateView, Run, RunEvent  # noqa: E402
from app.modules.owner.service import DEMO_PREFIX  # noqa: E402

ADDRESS = {"region_code": "11140", "lat": 37.5665868, "lng": 126.97819753}
FOODS = ["치킨", "피자", "떡볶이", "마라탕", "짜장면", "짬뽕", "국밥", "김치찌개", "초밥", "돈까스",
         "족발", "햄버거", "한식", "중식", "분식", "탕수육", "부대찌개", "쌀국수"]
TASTES = ["", "", "", "안 매운 ", "매운 ", "국물 있는 "]
# (시작 시, 끝 시, 비중): 점심·저녁이 많고 밤 야식도 있다
HOURS = [(11, 14, 30), (17, 21, 45), (21, 24, 20), (0, 2, 5)]


def pick_time(rng: random.Random, now):
    # 지난 14일: '지난주 대비'가 보이도록 최근 7일에 조금 더 많이
    day = now - timedelta(days=rng.randint(0, 6) if rng.random() < 0.6 else rng.randint(7, 13))
    start, end, _ = rng.choices(HOURS, weights=[w for *_, w in HOURS])[0]
    hour = rng.randrange(start, end)
    at = day.replace(hour=hour % 24, minute=rng.randrange(60), second=0, microsecond=0)
    return min(at, now - timedelta(minutes=5))


def message(rng: random.Random) -> str:
    people = rng.choice([1, 1, 2, 2, 2, 3, 4])
    per = rng.choice([9000, 10000, 12000, 13000, 15000, 18000, 20000])
    food = rng.choice(FOODS)
    who = {1: "혼자", 2: "둘이서", 3: "3명이", 4: "4명이"}[people]
    return f"{who} {won(people * per)} 이하로 {rng.choice(TASTES)}{food}"


def won(amount: int) -> str:
    """26000 → '2만6천원', 9000 → '9천원'."""
    man, cheon = divmod(amount // 1000, 10)
    return (f"{man}만" if man else "") + (f"{cheon}천" if cheon else "") + "원"


def clear(db) -> int:
    like = f"{DEMO_PREFIX}%"
    n = 0
    for model, col in [
        (CandidateView, CandidateView.request_id), (RunEvent, RunEvent.request_id), (Run, Run.request_id),
        (Exclusion, Exclusion.request_id), (Candidate, Candidate.request_id),
        (ConstraintsHistory, ConstraintsHistory.request_id), (LlmCall, LlmCall.request_id),
        (DiscoveryRequest, DiscoveryRequest.request_id),
    ]:
        n += db.execute(delete(model).where(col.like(like))).rowcount or 0
    db.commit()
    return n


def seed(count: int, seed_value: int) -> dict:
    rng = random.Random(seed_value)
    init_db()
    stats = {"requests": 0, "with_candidates": 0, "selected": 0, "completed": 0}
    with SessionLocal() as db:
        loader.load_if_empty(db)
        now = now_kst()
        stamp = now.strftime("%m%d%H%M")
        for i in range(count):
            at = pick_time(rng, now)
            rid = f"{DEMO_PREFIX}{stamp}-{i:04d}"
            # A가 보는 '지금'을 그 요청 시각으로 바꿔 진짜 검색을 돌린다 (영업시간 필터·기록 시각)
            a_service.now_kst = lambda at=at: at
            a_service.filter_now = lambda at=at: at
            try:
                a_service.create_request(
                    db,
                    CreateRequestBody(request_id=rid, user_id="DEMO", message=message(rng),
                                      delivery_address=ADDRESS),
                )
                found = a_service.search_request(db, rid, 6)
            except Exception as exc:  # noqa: BLE001 — 한 건 실패가 전체를 멈추지 않게
                db.rollback()
                print(f"  건너뜀 {rid}: {exc}")
                continue
            stats["requests"] += 1
            cands = found.get("candidates") or []
            if not cands:
                continue
            stats["with_candidates"] += 1
            # 화면 표시: 앞 3곳 (가끔 요기요에서 주문 불가로 보임)
            shown = [
                {
                    "candidate_id": c["candidate_id"],
                    "restaurant_id": c["restaurant"]["restaurant_id"],
                    "position": n + 1,
                    "collapsed": False,
                    "live_status": "UNAVAILABLE" if rng.random() < 0.08 else "OK",
                }
                for n, c in enumerate(cands[:3])
            ]
            selectable = [c for c, s in zip(cands[:3], shown, strict=False) if s["live_status"] == "OK"]
            chosen = None
            if selectable and rng.random() < 0.55:
                chosen = rng.choices(selectable, weights=[5, 3, 2][: len(selectable)])[0]
                a_service.select_candidate(db, rid, chosen["candidate_id"])
                stats["selected"] += 1
            done = chosen is not None and rng.random() < 0.7
            stats["completed"] += done
            run = Run(
                request_id=rid,
                user_id="DEMO",
                state="COMPLETED" if done else ("CANCELLED" if chosen else "CANDIDATES_READY"),
                state_version=1,
                row_version=1,
                message=f"(시연 데이터) {rid}",
                delegation={},
                context={
                    "candidates": [{**c, "index": n} for n, c in enumerate(cands)],
                    **({"selected_candidate": chosen} if chosen else {}),
                },
                event_seq=0,
                created_at=at,
                updated_at=at + timedelta(minutes=3),
            )
            db.add(run)
            db.flush()
            db.add(CandidateView(request_id=rid, shown=shown, created_at=at + timedelta(seconds=5)))
            db.commit()
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{count}")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="점주 대시보드 시연 데이터")
    ap.add_argument("--count", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--clear", action="store_true", help="DEMO- 로 시작하는 기록만 지운다")
    a = ap.parse_args()
    if a.clear:
        init_db()
        with SessionLocal() as db:
            print(f"지운 줄: {clear(db)}")
        return 0
    stats = seed(a.count, a.seed)
    print(
        f"끝: 요청 {stats['requests']}건 · 후보 있음 {stats['with_candidates']}건 · "
        f"선택 {stats['selected']}건 · 주문 완료(모의) {stats['completed']}건"
    )
    print("점주 화면(/owner)에서 가게를 골라 보세요. 지우려면 --clear")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
