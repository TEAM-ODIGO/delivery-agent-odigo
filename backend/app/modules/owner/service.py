"""점주 대시보드 (이슈 #50): "우리 가게가 왜 추천되거나 선택되지 않았고, 이번 주 무엇을 고칠까?"

여러 모듈의 기록을 가게별로 묶는다 (모두 읽기만 한다).
- A: 후보 포함·선택·제외 사유 (discovery.tracking), 요청 조건 (a_request), 식당·메뉴 데이터
- O: 화면 표시·실시간 주문 불가로 보임·주문 완료 (orchestrator.tracking)

지표 이름은 정확히 쓴다: E는 모의결제라 '매출'이 아니라 '주문 완료(시연)'.
'이번 주 할 일'은 추천 로직에서 바로 나오는, 확인할 수 있는 이유와 고칠 행동만 제안한다 (노출 보장 없음).
"""

import re
import statistics
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.discovery import tracking as a_tracking
from app.modules.discovery.catalog_models import Menu, Restaurant
from app.modules.discovery.constants import CUISINES, TASTES
from app.modules.discovery.models import Candidate, DiscoveryRequest, Exclusion
from app.modules.discovery.search import GENERIC_CATEGORIES, matches_cuisine
from app.modules.discovery.timeutil import KST
from app.modules.orchestrator import tracking as o_tracking

DEMO_PREFIX = "DEMO-"  # scripts/seed_owner_demo.py 가 만든 요청
SERVINGS_IN_NAME = re.compile(r"\d\s*(?:~\s*\d\s*)?인분|\d\s*인\b|\d\s*인[ )]")


def primary_category(r: Restaurant) -> str | None:
    for c in r.yogiyo_categories or []:
        if c not in GENERIC_CATEGORIES:
            return c
    return None


def list_restaurants(db: Session, now: datetime, q: str = "", limit: int = 50) -> dict:
    """가게 고르기: 최근 7일 후보에 많이 든 순 (기록이 없으면 리뷰 많은 순)."""
    since = now - timedelta(days=7)
    activity = dict(
        db.execute(
            select(Candidate.restaurant_id, func.count())
            .where(Candidate.created_at >= since)
            .group_by(Candidate.restaurant_id)
        ).all()
    )
    rows = db.scalars(select(Restaurant)).all()
    if q:
        rows = [r for r in rows if q in r.name]
    rows.sort(key=lambda r: (-activity.get(r.restaurant_id, 0), -r.review_count))
    return {
        "results": [
            {
                "restaurant_id": r.restaurant_id,
                "name": r.name,
                "category": primary_category(r),
                "candidates_7d": activity.get(r.restaurant_id, 0),
            }
            for r in rows[:limit]
        ]
    }


def funnel(db: Session, restaurant_id: str, since: datetime, until: datetime) -> dict:
    a = a_tracking.restaurant_summary(db, restaurant_id, since, until)
    shown = o_tracking.shown_summary(db, restaurant_id, since, until)
    return {
        "candidates": a["candidates"],
        "shown": shown["shown"],
        "selected": a["selected"],
        "completed": o_tracking.completed_count(db, restaurant_id, since, until),
        "shown_unavailable": shown["shown_unavailable"],
        "exclusions": a["exclusions"],
    }


def menu_quality(db: Session, restaurant_id: str) -> dict:
    mains = db.scalars(
        select(Menu).where(
            Menu.restaurant_id == restaurant_id,
            Menu.menu_type.in_(("main", "set")),
            Menu.is_sold_out.is_(False),
        )
    ).all()
    return {
        "mains": len(mains),
        "no_description": sum(1 for m in mains if not (m.description or "").strip()),
        "no_servings": sum(
            1 for m in mains if m.servings is None and not SERVINGS_IN_NAME.search(m.name)
        ),
        "no_spice": sum(1 for m in mains if m.spice_level is None),
    }


def request_ids_for(db: Session, restaurant_id: str, since: datetime, until: datetime) -> set[str]:
    """이 가게가 후보에 들었거나 제외된(음식 종류는 맞았던) 요청."""
    a = db.scalars(
        select(Candidate.request_id).where(
            Candidate.restaurant_id == restaurant_id,
            Candidate.created_at >= since,
            Candidate.created_at < until,
        )
    ).all()
    b = db.scalars(
        select(Exclusion.request_id).where(
            Exclusion.restaurant_id == restaurant_id,
            Exclusion.created_at >= since,
            Exclusion.created_at < until,
        )
    ).all()
    return set(a) | set(b)


def requested(db: Session, request_ids: set[str]) -> dict:
    foods: Counter = Counter()
    tastes: Counter = Counter()
    budgets: list[int] = []
    for req in db.scalars(select(DiscoveryRequest).where(DiscoveryRequest.request_id.in_(request_ids))):
        c = req.constraints or {}
        for word in c.get("menu_keywords") or []:
            foods[word] += 1
        if not c.get("menu_keywords") and c.get("cuisine"):
            foods[c["cuisine"]] += 1
        for code in c.get("taste_preferences") or []:
            tastes[TASTES.get(code, {}).get("label", code)] += 1
        if c.get("budget_krw") and c.get("people"):
            budgets.append(int(c["budget_krw"] / c["people"]))
    return {
        "foods": [{"name": k, "count": n} for k, n in foods.most_common(6)],
        "tastes": [{"name": k, "count": n} for k, n in tastes.most_common(4)],
        "budget_per_person_median": int(statistics.median(budgets)) if budgets else None,
    }


def menu_reaction(db: Session, restaurant_id: str, since: datetime, until: datetime) -> dict:
    cands = db.scalars(
        select(Candidate).where(
            Candidate.restaurant_id == restaurant_id,
            Candidate.created_at >= since,
            Candidate.created_at < until,
        )
    ).all()
    selected_ids = {
        (r.request_id, r.selected_candidate_id)
        for r in db.scalars(
            select(DiscoveryRequest).where(
                DiscoveryRequest.request_id.in_({c.request_id for c in cands})
            )
        )
        if r.selected_candidate_id
    }
    recommended: Counter = Counter()
    chosen: Counter = Counter()
    for c in cands:
        for item in (c.body or {}).get("items", []):
            recommended[item["name"]] += 1
            if (c.request_id, c.candidate_id) in selected_ids:
                chosen[item["name"]] += 1
    return {
        "menus": [
            {"name": name, "recommended": n, "selected": chosen.get(name, 0)}
            for name, n in recommended.most_common(8)
        ]
    }


def closed_hours(db: Session, restaurant_id: str, since: datetime, until: datetime) -> list[int]:
    """영업시간 외로 빠진 요청이 많았던 시각 (많은 순, 최대 3개)."""
    times = db.scalars(
        select(Exclusion.created_at).where(
            Exclusion.restaurant_id == restaurant_id,
            Exclusion.reason == "CLOSED_NOW",
            Exclusion.created_at >= since,
            Exclusion.created_at < until,
        )
    ).all()
    hours = Counter(t.astimezone(KST).hour for t in times)
    return [h for h, _ in hours.most_common(3)]


def category_median_min_order(db: Session, category: str | None) -> int | None:
    if not category:
        return None
    values = [
        r.min_order_krw
        for r in db.scalars(select(Restaurant))
        if primary_category(r) == category and r.min_order_krw
    ]
    return int(statistics.median(values)) if values else None


def actions(
    r: Restaurant, now_f: dict, quality: dict, req: dict, hours: list[int], median_min: int | None,
    excl_detail: dict,
) -> list[dict]:
    """이번 주 할 일: 근거(횟수·값)가 있는 것만, 영향이 큰 순서로 최대 3개."""
    ex = now_f["exclusions"]
    out: list[tuple[int, dict]] = []

    closed = ex.get("CLOSED_NOW", 0)
    if closed >= 2:
        when = ", ".join(f"{h}시" for h in sorted(hours)) if hours else "영업시간 밖"
        out.append((closed, {
            "code": "OPENING_HOURS",
            "title": "영업시간을 확인해 보세요",
            "detail": f"{when}에 들어온 요청에서 영업시간({r.opening_hours or '미등록'}) 때문에 {closed}번 빠졌어요.",
            "count": closed,
        }))
    below = ex.get("BELOW_MIN_ORDER", 0)
    if below >= 2 or (median_min and r.min_order_krw > median_min * 1.2):
        detail = f"최소주문금액이 {r.min_order_krw:,}원이에요"
        if median_min:
            detail += f" (같은 업종 중간값 {median_min:,}원)"
        detail += f". 1인·소액 요청에서 {below}번 빠졌어요." if below else ". 1인 요청에서 빠지기 쉬워요."
        out.append((max(below, 1), {
            "code": "MIN_ORDER",
            "title": "1인 메뉴나 세트를 만들어 보세요",
            "detail": detail,
            "count": below,
        }))
    budget = ex.get("BUDGET_EXCEEDED", 0)
    if budget >= 2:
        limits = excl_detail.get("BUDGET_EXCEEDED") or []
        typical = f"{int(statistics.median(limits)):,}원 안팎의 " if limits else ""
        out.append((budget, {
            "code": "BUDGET",
            "title": "예산에 맞는 세트가 있으면 더 자주 추천돼요",
            "detail": f"{typical}예산 요청에서 금액을 넘어 {budget}번 빠졌어요.",
            "count": budget,
        }))
    missing_info = excl_detail.get("missing_foods") or {}
    missing = missing_info.get("count", 0)
    if missing >= 2:
        foods = missing_info.get("foods") or []
        top = "·".join(f"'{f}'" for f in foods[:2]) or "찾는 메뉴가 있는"
        out.append((missing, {
            "code": "MENU_NAME",
            "title": "찾는 메뉴가 이름에 보이게 해 보세요",
            "detail": f"{top} 요청이 {missing}번 있었지만 맞는 메뉴를 찾지 못해 빠졌어요. "
            "비슷한 메뉴가 있다면 이름에 음식 이름을 넣고, 없다면 메뉴 추가를 고려해 보세요.",
            "count": missing,
        }))
    if now_f["shown_unavailable"] >= 1:
        n = now_f["shown_unavailable"]
        out.append((n * 2, {
            "code": "LIVE_STATUS",
            "title": "요기요 영업·품절 정보를 최신으로 맞춰 주세요",
            "detail": f"고객 화면에 보였지만 요기요에서 주문할 수 없는 상태가 {n}번 있었어요 (임시휴무·준비중 등).",
            "count": n,
        }))
    mains = quality["mains"] or 1
    if quality["no_servings"] / mains >= 0.5:
        out.append((1, {
            "code": "SERVINGS",
            "title": "메뉴에 인분을 적어 보세요",
            "detail": f"메뉴 {quality['mains']}개 중 {quality['no_servings']}개에 인분 표기가 없어요. "
            "'2~3인분'처럼 적으면 인원에 맞춘 추천에 더 잘 들어가요.",
            "count": quality["no_servings"],
        }))
    if quality["no_description"] / mains >= 0.5:
        out.append((1, {
            "code": "DESCRIPTION",
            "title": "메뉴 설명에 맛을 적어 보세요",
            "detail": f"메뉴 {quality['mains']}개 중 {quality['no_description']}개에 설명이 없어요. "
            "'안 매운', '국물' 같은 요청에 걸리도록 맛·재료를 적어 보세요.",
            "count": quality["no_description"],
        }))
    out.sort(key=lambda x: -x[0])
    return [a for _, a in out[:3]]


def fits_store(restaurant: Restaurant, word: str) -> bool:
    """이 가게 업종에서 팔 법한 음식인지 ('짬뽕' → 중국집 O, '국밥' → 중국집 X). 모르는 음식이면 X."""
    return any(
        word in info["triggers"] and matches_cuisine(restaurant, cuisine) for cuisine, info in CUISINES.items()
    )


def missing_foods(db: Session, restaurant: Restaurant, since: datetime, until: datetime) -> dict:
    """'찾는 메뉴 없음'으로 빠진 요청 중 이 가게 업종에 맞는 음식만 센다.

    중국집에 '국밥'이 없는 건 고칠 일이 아니라서, 할 일 근거에서 뺀다.
    """
    restaurant_id = restaurant.restaurant_id
    rids = db.scalars(
        select(Exclusion.request_id).where(
            Exclusion.restaurant_id == restaurant_id,
            Exclusion.reason == "NO_REQUESTED_MENU",
            Exclusion.created_at >= since,
            Exclusion.created_at < until,
        )
    ).all()
    words: Counter = Counter()
    fitting = 0
    for req in db.scalars(select(DiscoveryRequest).where(DiscoveryRequest.request_id.in_(set(rids)))):
        hits = [w for w in (req.constraints or {}).get("menu_keywords") or [] if fits_store(restaurant, w)]
        words.update(hits)
        fitting += bool(hits)
    return {"foods": [w for w, _ in words.most_common(3)], "count": fitting}


def exclusion_values(db: Session, restaurant_id: str, since: datetime, until: datetime) -> dict:
    limits = [
        e.detail.get("limit")
        for e in db.scalars(
            select(Exclusion).where(
                Exclusion.restaurant_id == restaurant_id,
                Exclusion.reason == "BUDGET_EXCEEDED",
                Exclusion.created_at >= since,
                Exclusion.created_at < until,
            )
        )
        if (e.detail or {}).get("limit")
    ]
    return {"BUDGET_EXCEEDED": limits}


def dashboard(db: Session, restaurant_id: str, now: datetime, days: int = 7) -> dict:
    r = db.get(Restaurant, restaurant_id)
    if r is None:
        raise AppError(404, "RESTAURANT_NOT_FOUND", "가게를 찾을 수 없습니다.")
    if not 1 <= days <= 90:
        raise AppError(400, "VALIDATION_ERROR", "기간은 1~90일이어야 합니다.")
    since, until = now - timedelta(days=days), now
    current = funnel(db, restaurant_id, since, until)
    previous = funnel(db, restaurant_id, since - timedelta(days=days), since)
    request_ids = request_ids_for(db, restaurant_id, since, until)
    req = requested(db, request_ids)
    quality = menu_quality(db, restaurant_id)
    category = primary_category(r)
    hours = closed_hours(db, restaurant_id, since, until)
    return {
        "restaurant": {
            "restaurant_id": r.restaurant_id,
            "name": r.name,
            "category": category,
            "rating": float(r.rating),
            "review_count": r.review_count,
            "min_order_krw": r.min_order_krw,
            "base_delivery_fee_krw": r.base_delivery_fee_krw,
            "opening_hours": r.opening_hours,
        },
        "period": {"since": since.isoformat(), "until": until.isoformat(), "days": days},
        "funnel": {k: current[k] for k in ("candidates", "shown", "selected", "completed")},
        "previous": {k: previous[k] for k in ("candidates", "shown", "selected", "completed")},
        "shown_unavailable": current["shown_unavailable"],
        "exclusions": [{"reason": k, "count": n} for k, n in current["exclusions"].items()],
        "closed_hours": hours,
        "requested": req,
        "menus": menu_reaction(db, restaurant_id, since, until)["menus"],
        "menu_quality": quality,
        "actions": actions(
            r, current, quality, req, hours, category_median_min_order(db, category),
            {
                **exclusion_values(db, restaurant_id, since, until),
                "missing_foods": missing_foods(db, r, since, until),
            },
        ),
        "demo_data": any(rid.startswith(DEMO_PREFIX) for rid in request_ids),
    }
