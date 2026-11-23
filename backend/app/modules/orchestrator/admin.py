"""관리자 대시보드 조회 (이슈 #46). 읽기 전용.

운영자가 먼저 알아야 할 것: 지금 개입해야 할 주문(조치 필요)과,
오늘 어느 단계에서 문제가 반복되는지.

조치 필요 규칙 (우선순위 순)
  1 PAYMENT_UNKNOWN : 결제 결과를 모르는 주문 (날짜와 상관없이 남아 있으면)
  2 STUCK           : 사용자를 기다리는 단계가 아닌데 STUCK_MINUTES분 넘게 상태가 그대로
  3 REPEATED_FAILURE: 오늘 같은 사용자가 실패·차단을 2번 이상
  4 FAILED / BLOCKED: 오늘 실패·차단된 주문
"""

from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.clock import KST
from app.modules.orchestrator.constants import STEPS, TOTAL_STEPS, RunState
from app.modules.orchestrator.models import Run

STUCK_MINUTES = 10
ACTION_LIMIT = 20

# 사용자가 답할 차례인 단계: 오래 머물러도 운영자가 개입할 일은 아니다
USER_WAITING = {
    RunState.NEED_CLARIFICATION,
    RunState.CANDIDATES_READY,
    RunState.PAYMENT_METHOD_SELECTION,
    RunState.AWAITING_APPROVAL,
}
TERMINAL = {RunState.COMPLETED, RunState.BLOCKED, RunState.FAILED, RunState.CANCELLED}
PRIORITY = {"PAYMENT_UNKNOWN": 1, "STUCK": 2, "REPEATED_FAILURE": 3, "FAILED": 4, "BLOCKED": 4}


def start_of_day(now: datetime) -> datetime:
    local = now.astimezone(KST)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def reason_codes(run: Run) -> list[str]:
    codes = (run.blocked or {}).get("reason_codes") or (run.error or {}).get("reason_codes")
    if codes:
        return list(codes)
    return [run.error["code"]] if run.error and run.error.get("code") else []


def row(run: Run) -> dict:
    current, label = STEPS[RunState(run.state)]
    ctx = run.context or {}
    quote = ctx.get("target_quote") or {}
    restaurant = (ctx.get("selected_candidate") or {}).get("restaurant") or {}
    return {
        "request_id": run.request_id,
        "user_id": run.user_id,
        "message": run.message,
        "state": run.state,
        "step": {"current": current, "total": TOTAL_STEPS, "label": label},
        "restaurant_name": restaurant.get("name"),
        "charge_krw": (run.result or {}).get("charge_krw") or quote.get("charge_krw"),
        "reason_codes": reason_codes(run),
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def summary(session: Session, now: datetime) -> dict:
    """O-17. 오늘 운영 개요 + 조치할 주문."""
    day = start_of_day(now)
    today = session.scalars(select(Run).where(Run.created_at >= day)).all()

    counts = Counter(RunState(r.state) for r in today)
    in_progress = sum(n for state, n in counts.items() if state not in TERMINAL)
    reasons = Counter(
        code
        for r in today
        if RunState(r.state) in (RunState.BLOCKED, RunState.FAILED)
        for code in reason_codes(r)
    )
    by_step = Counter(RunState(r.state) for r in today if RunState(r.state) not in TERMINAL)

    # 조치할 주문: 결제 확인 중·멈춘 주문은 날짜와 상관없이, 실패·차단은 오늘 것만
    stuck_before = now - timedelta(minutes=STUCK_MINUTES)
    open_runs = session.scalars(
        select(Run).where(
            or_(
                Run.state == RunState.PAYMENT_UNKNOWN,
                Run.state.not_in([s.value for s in USER_WAITING | TERMINAL]),
            )
        )
    ).all()
    failures_by_user = Counter(
        r.user_id for r in today if RunState(r.state) in (RunState.BLOCKED, RunState.FAILED)
    )
    actions: dict[str, dict] = {}
    for r in open_runs:
        state = RunState(r.state)
        if state == RunState.PAYMENT_UNKNOWN:
            actions[r.request_id] = {**row(r), "action": "PAYMENT_UNKNOWN"}
        elif r.updated_at <= stuck_before:
            minutes = int((now - r.updated_at).total_seconds() // 60)
            actions[r.request_id] = {**row(r), "action": "STUCK", "stuck_minutes": minutes}
    for r in today:
        state = RunState(r.state)
        if state not in (RunState.BLOCKED, RunState.FAILED) or r.request_id in actions:
            continue
        action = "REPEATED_FAILURE" if failures_by_user[r.user_id] >= 2 else state.value
        actions[r.request_id] = {**row(r), "action": action}
    # 우선순위 순, 같은 우선순위 안에서는 최근 것이 위로
    ordered = sorted(actions.values(), key=lambda a: a["updated_at"], reverse=True)
    ordered.sort(key=lambda a: PRIORITY[a["action"]])

    return {
        "date": day.date().isoformat(),
        "counts": {
            "started": len(today),
            "completed": counts[RunState.COMPLETED],
            "payment_unknown": counts[RunState.PAYMENT_UNKNOWN],
            "blocked": counts[RunState.BLOCKED],
            "failed": counts[RunState.FAILED],
            "cancelled": counts[RunState.CANCELLED],
            "in_progress": in_progress,
        },
        "top_reasons": [{"code": c, "count": n} for c, n in reasons.most_common(5)],
        "by_step": [
            {"state": s.value, "label": STEPS[s][1], "count": n}
            for s, n in sorted(by_step.items(), key=lambda x: STEPS[x[0]][0])
        ],
        "actions": ordered[:ACTION_LIMIT],
        "action_count": len(ordered),
        "stuck_minutes": STUCK_MINUTES,
    }


def detail(run: Run) -> dict:
    """O-18. 주문 상세 요약 (타임라인·감사 로그·실시간 확인·장바구니는 각 API로)."""
    ctx = run.context or {}
    selected = ctx.get("selected_candidate") or {}
    quote = ctx.get("target_quote") or ctx.get("quote") or {}
    items = [
        {
            "name": i.get("name"),
            "quantity": i.get("quantity"),
            "unit_price_krw": i.get("unit_price_krw"),
        }
        for i in selected.get("items") or []
    ]
    return {
        **row(run),
        "constraints": ctx.get("constraints"),
        "candidate_count": len(ctx.get("candidates") or []),
        "selected": {
            "restaurant_name": (selected.get("restaurant") or {}).get("name"),
            "items": items,
            "estimated_total_krw": selected.get("estimated_total_krw"),
        }
        if selected
        else None,
        "payment": {
            "payment_method_id": quote.get("payment_method_id"),
            "menu_subtotal_krw": quote.get("menu_subtotal_krw"),
            "delivery_fee_krw": quote.get("delivery_fee_krw"),
            "instant_discount_krw": quote.get("instant_discount_krw"),
            "charge_krw": quote.get("charge_krw"),
            "payment_id": (run.result or {}).get("payment_id"),
            "payment_status": (run.result or {}).get("payment_status"),
        }
        if quote or run.result
        else None,
        "blocked": run.blocked,
        "error": run.error,
        "notice": run.notice,
    }
