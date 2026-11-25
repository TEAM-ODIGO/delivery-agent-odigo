"""운영 기록 시연 데이터 (이슈 #94). 오늘·어제 주문을 만들어 운영 개요의 문제 상황이 보이게 한다.

오늘(지금 시각까지)과 어제(하루 전체) 주문을 시간대별로 만들고, 살펴볼 주문이 생기도록
결제 확인 중 · 멈춤 · 차단 · 실패 · 같은 사용자 반복 실패와 요기요 금액 재계산 기록을 섞는다.
연결 상태가 '실패'로 보이도록 마지막 장바구니 담기·AI 호출 실패 기록도 하나씩 남긴다.
요청 ID는 모두 DEMO-OPS- 로 시작한다 (점주 화면에서도 '시연 데이터'로 표시).

    uv run --env-file .env python scripts/seed_admin_demo.py              # 만들기 (다시 돌리면 지우고 새로 만듦)
    uv run --env-file .env python scripts/seed_admin_demo.py --no-service-issues   # 연결 상태 실패 기록은 빼고
    uv run --env-file .env python scripts/seed_admin_demo.py --clear      # DEMO-OPS- 기록만 지움

- 서버 .env의 DATABASE_URL(DB)에 쓴다. 서버를 켜 둔 채 돌려도 된다 (운영 개요는 5초마다 새로 읽는다).
- 시각은 돌린 순간 기준이라, 시간이 지나면 '멈춤'·'결제 확인 중' 분 수가 늘어난다.
"""

import argparse
import random
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ 를 import 경로에 추가

from sqlalchemy import delete, select  # noqa: E402

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.modules.cart.models import CartJob  # noqa: E402
from app.modules.discovery import loader  # noqa: E402
from app.modules.discovery.catalog_models import Restaurant  # noqa: E402
from app.modules.discovery.models import LlmCall  # noqa: E402
from app.modules.discovery.timeutil import now_kst  # noqa: E402
from app.modules.orchestrator.models import Run, RunEvent  # noqa: E402

PREFIX = "DEMO-OPS-"
METHODS = [
    ("CARD003", "신한카드 Deep On Platinum+"),
    ("CARD001", "신한카드 A"),
    ("LOCAL001", "가상 지역화폐"),
    ("CARD002", "신한카드 B"),
]
REQUESTS = [
    ("치킨", ["후라이드"], [], "둘이서 3만원 이하로 치킨"),
    ("중식", ["짜장면", "탕수육"], [], "셋이서 짜장면이랑 탕수육 4만원"),
    ("분식", ["떡볶이"], ["매운"], "혼자 만원 이하로 매운 떡볶이"),
    ("피자", ["피자"], [], "넷이서 5만원 피자"),
    ("한식", ["김치찌개"], ["국물"], "혼자 1만2천원 국물 있는 거"),
    ("일식", ["초밥"], [], "둘이서 4만원 초밥"),
    ("치킨", ["양념치킨"], ["매운"], "매운 양념치킨 2만5천원"),
    ("분식", ["김밥"], [], "혼자 9천원 김밥"),
]
# 오늘: (상태, 사유) — 시각은 아래에서 정한다
TODAY = (
    [("COMPLETED", None)] * 14
    + [("CANCELLED", None)] * 3
    + [("BLOCKED", "BUDGET_EXCEEDED"), ("BLOCKED", "PAYMENT_METHOD_NOT_ALLOWED"), ("FAILED", "CANDIDATE_UNAVAILABLE")]
)
YESTERDAY = [("COMPLETED", None)] * 15 + [("CANCELLED", None)] * 3 + [("BLOCKED", "BUDGET_EXCEEDED"), ("FAILED", "NO_CANDIDATE")]
# 요청 음식 → 요기요 카테고리에 들어 있는 말 (가게를 요청에 맞게 고른다)
CATEGORY_WORD = {"중식": "중국", "일식": "일식", "한식": "한식", "치킨": "치킨", "피자": "피자", "분식": "분식"}
# 점심·저녁에 몰리게
HOURS = [11, 11, 12, 12, 12, 12, 13, 13, 14, 17, 18, 18, 18, 19, 19, 19, 20, 20, 21, 22]


class Seeder:
    def __init__(self, db, rng: random.Random, now: datetime):
        self.db, self.rng, self.now = db, rng, now
        self.n = 0
        self.restaurants = [
            r for r in db.scalars(select(Restaurant).where(Restaurant.rating >= 4.5)).all() if r.name
        ] or db.scalars(select(Restaurant)).all()

    def rid(self) -> str:
        self.n += 1
        return f"{PREFIX}{self.now:%m%d%H%M}-{self.n:03d}"

    def run(self, state: str, reason: str | None, created: datetime, updated: datetime, *,
            user: str | None = None, requote: bool = False) -> str:
        rng = self.rng
        rid = self.rid()
        user = user or f"DEMO-OPS-U{self.n:03d}"  # 따로 지정한 경우만 같은 사용자 (반복 실패)
        cuisine, keywords, tastes, msg = rng.choice(REQUESTS)
        word = CATEGORY_WORD.get(cuisine, cuisine)
        matching = [r for r in self.restaurants if any(word in c for c in (r.yogiyo_categories or []))]
        rest = rng.choice(matching or self.restaurants)
        cats = [c for c in rest.yogiyo_categories or [] if c not in ("포장", "프랜차이즈", "야식", "1인분주문")]
        price = rng.choice([9000, 12000, 15000, 18000, 21000, 24000])
        qty = rng.choice([1, 1, 2])
        subtotal = price * qty
        fee = rng.choice([0, 1000, 2000, 3000])
        method_id, method_name = rng.choice(METHODS)
        discount = rng.choice([0, 0, 1000, 1500, 2000, 3000]) if state == "COMPLETED" else 0
        charge = subtotal + fee - discount
        item = {"name": f"{keywords[0]} 세트", "quantity": qty, "unit_price_krw": price}
        selected = state not in ("CANDIDATES_READY",) and not (state == "FAILED" and reason == "NO_CANDIDATE")
        ctx: dict = {
            "constraints": {"cuisine": cuisine, "menu_keywords": keywords, "taste_preferences": tastes,
                            "budget_krw": charge + 3000, "people": qty},
        }
        if selected:
            ctx["selected_candidate"] = {
                "restaurant": {"restaurant_id": rest.restaurant_id, "name": rest.name,
                               "cuisine": cats[0] if cats else None},
                "items": [item],
                "estimated_total_krw": subtotal + fee,
            }
            ctx["target_quote"] = {"restaurant_id": rest.restaurant_id, "menu_subtotal_krw": subtotal,
                                   "delivery_fee_krw": fee, "payment_method_id": method_id,
                                   "instant_discount_krw": discount, "charge_krw": charge}
        if requote:
            ctx["requote"] = {"cart_job_id": None, "previous": {"charge_krw": charge - 1000},
                              "changes": [{"kind": "DELIVERY_FEE", "name": "배달비", "from_krw": fee, "to_krw": fee + 1000}]}
        result = None
        if state == "COMPLETED":
            result = {"payment_id": f"PAY{rid[-6:]}", "payment_status": "APPROVED", "charge_krw": charge,
                      "order": {"restaurant_name": rest.name, "items": [item], "menu_subtotal_krw": subtotal,
                                "delivery_fee_krw": fee, "instant_discount_krw": discount, "charge_krw": charge,
                                "payment_method": {"payment_method_id": method_id, "name": method_name}}}
        blocked = {"reason_codes": [reason], "details": {}} if state == "BLOCKED" else None
        error = {"code": reason, "message": "시연 데이터"} if state == "FAILED" else None
        run = Run(request_id=rid, user_id=user, state=state, state_version=1, row_version=1, message=msg,
                  delegation={}, context=ctx, blocked=blocked, error=error, result=result,
                  event_seq=0, created_at=created, updated_at=updated)
        self.db.add(run)
        self.db.flush()
        # 실시간 주문 흐름에 보이는 단계 기록
        steps = [("RECEIVED", created)]
        steps.append(("CANDIDATES_READY", created + timedelta(seconds=20)))
        if selected and state in ("COMPLETED", "PAYMENT_UNKNOWN", "AWAITING_APPROVAL", "VALIDATING", "CANCELLED"):
            steps.append(("AWAITING_APPROVAL", min(updated, created + timedelta(seconds=70))))
        if state not in ("CANDIDATES_READY", "AWAITING_APPROVAL"):
            steps.append((state, updated))
        seq = 0
        prev = None
        for to, at in steps:
            seq += 1
            self.db.add(RunEvent(event_id=str(uuid.uuid4()), request_id=rid, seq=seq, event_type="STATE_CHANGED",
                                 from_state=prev, to_state=to, detail={"demo": True}, created_at=at))
            prev = to
        if requote:
            seq += 1
            self.db.add(RunEvent(event_id=str(uuid.uuid4()), request_id=rid, seq=seq, event_type="USER_REPLY",
                                 detail={"type": "REQUOTE_FROM_CART"}, created_at=updated - timedelta(seconds=5)))
        run.event_seq = seq
        return rid


def clear(db) -> int:
    like = f"{PREFIX}%"
    n = 0
    for model, col in [(RunEvent, RunEvent.request_id), (CartJob, CartJob.request_id),
                       (LlmCall, LlmCall.request_id), (Run, Run.request_id)]:
        n += db.execute(delete(model).where(col.like(like))).rowcount or 0
    db.commit()
    return n


def at_hour(day: datetime, hour: int, rng: random.Random) -> datetime:
    return day.replace(hour=hour, minute=rng.randrange(60), second=rng.randrange(60), microsecond=0)


def seed(seed_value: int, service_issues: bool) -> dict:
    rng = random.Random(seed_value)
    init_db()
    with SessionLocal() as db:
        loader.load_if_empty(db)
        cleared = clear(db)
        now = now_kst().replace(microsecond=0)
        today0 = now.replace(hour=0, minute=0, second=0)
        s = Seeder(db, rng, now)
        made = {"today": 0, "yesterday": 0}

        # 어제: 하루 전체 (시간대별 그래프의 회색 선, 그중 지금 시각까지가 지표 비교 대상)
        yday0 = today0 - timedelta(days=1)
        for (state, reason), hour in zip(YESTERDAY, HOURS, strict=False):
            created = at_hour(yday0, hour, rng)
            s.run(state, reason, created, created + timedelta(minutes=rng.randint(2, 5)))
            made["yesterday"] += 1

        # 오늘: 지금 시각 전까지만
        for (state, reason), hour in zip(TODAY, HOURS, strict=False):
            created = at_hour(today0, hour, rng)
            if created > now - timedelta(minutes=20):
                continue
            s.run(state, reason, created, created + timedelta(minutes=rng.randint(2, 5)))
            made["today"] += 1

        # 지금 살펴볼 주문: 결제 확인 중 · 멈춤 · 같은 사용자 반복 실패
        s.run("PAYMENT_UNKNOWN", None, now - timedelta(minutes=6), now - timedelta(minutes=3))
        s.run("VALIDATING", None, now - timedelta(minutes=17), now - timedelta(minutes=15))
        s.run("BLOCKED", "BUDGET_EXCEEDED", now - timedelta(minutes=12), now - timedelta(minutes=11), user="DEMO-OPS-U9")
        failed = s.run("FAILED", "MOCK_DECLINED", now - timedelta(minutes=9), now - timedelta(minutes=8), user="DEMO-OPS-U9")
        # 진행 중: 금액 재계산 후 승인 대기 · 방금 들어온 주문
        s.run("AWAITING_APPROVAL", None, now - timedelta(minutes=3), now - timedelta(minutes=1), requote=True)
        s.run("CANDIDATES_READY", None, now - timedelta(seconds=40), now - timedelta(seconds=20))
        made["today"] += 6

        if service_issues:
            # 연결 상태: 마지막 장바구니 담기·AI 호출이 실패한 것처럼
            db.add(CartJob(job_id=f"CART-{PREFIX}{uuid.uuid4().hex[:8]}", request_id=failed, user_id="DEMO-OPS-U9",
                           status="FAILED", restaurant_id="0", expected={}, cart_items=[], mismatches=[],
                           error="시연 데이터: 요기요 페이지를 열지 못함", mode="real",
                           created_at=now - timedelta(minutes=8), finished_at=now - timedelta(minutes=8)))
            db.add(LlmCall(request_id=failed, purpose="EXTRACT", model="demo", success=False, attempt=1,
                           latency_ms=12000, error="시연 데이터: 시간 초과", created_at=now - timedelta(minutes=9)))
        db.commit()
        return {**made, "cleared": cleared}


def main() -> int:
    ap = argparse.ArgumentParser(description="운영 기록 시연 데이터")
    ap.add_argument("--seed", type=int, default=94)
    ap.add_argument("--clear", action="store_true", help="DEMO-OPS- 로 시작하는 기록만 지운다")
    ap.add_argument("--no-service-issues", action="store_true", help="연결 상태 실패 기록은 만들지 않는다")
    a = ap.parse_args()
    if a.clear:
        init_db()
        with SessionLocal() as db:
            print(f"지운 줄: {clear(db)}")
        return 0
    st = seed(a.seed, not a.no_service_issues)
    print(f"끝: 오늘 {st['today']}건 · 어제 {st['yesterday']}건 (이전 시연 기록 {st['cleared']}줄 지움)")
    print("운영 기록(/admin)을 열어 보세요. 지우려면 --clear")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
