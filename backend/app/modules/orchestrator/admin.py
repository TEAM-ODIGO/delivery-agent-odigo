"""관리자 대시보드 조회 (이슈 #46). 읽기 전용.

운영자가 먼저 알아야 할 것: 지금 살펴볼 주문과, 오늘 어느 단계에서 문제가 반복되는지.
이슈 #94: 어제 같은 시각과 비교한 지표, 시간대별 주문, 오늘의 순위, 연결 상태, 최근 주문 흐름.

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
from app.modules.orchestrator.constants import STEPS, TOTAL_STEPS, RunEventType, RunState
from app.modules.orchestrator.models import Run, RunEvent

STUCK_MINUTES = 10
ACTION_LIMIT = 20

# 사용자가 답할 차례인 단계: 오래 머물러도 운영자가 개입할 일은 아니다
USER_WAITING = {
    RunState.NEED_CLARIFICATION,
    RunState.CANDIDATES_READY,
    RunState.PAYMENT_METHOD_SELECTION,
    RunState.AWAITING_APPROVAL,
}
TERMINAL = {
    RunState.COMPLETED, RunState.BLOCKED, RunState.FAILED, RunState.CANCELLED,
    RunState.RECOMMENDED, RunState.HANDED_OFF,
}
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

    # 어제 같은 시각까지 (지표 비교) · 어제 하루 전체 (시간대별 그래프)
    y_day = day - timedelta(days=1)
    yesterday = session.scalars(select(Run).where(Run.created_at >= y_day, Run.created_at < day)).all()
    y_until = now - timedelta(days=1)
    yesterday_so_far = [r for r in yesterday if r.created_at <= y_until]

    return {
        "date": day.date().isoformat(),
        "metrics": {"today": metrics(today), "yesterday": metrics(yesterday_so_far)},
        "hourly": {"today": hourly(today), "yesterday": hourly(yesterday)},
        "rankings": rankings(today),
        "services": services(session, now),
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


# ---------------------------------------------------------------- 이슈 #94
RANK_LIMIT = 5
EVENT_LIMIT = 30
# 최근 주문 흐름에 보여 줄 단계 (나머지 단계 이동·서비스 호출은 상세 타임라인에서)
FEED_STATES = {
    RunState.RECEIVED: "STARTED",
    RunState.AWAITING_APPROVAL: "AWAITING_APPROVAL",
    RunState.COMPLETED: "COMPLETED",
    RunState.PAYMENT_UNKNOWN: "PAYMENT_UNKNOWN",
    RunState.BLOCKED: "BLOCKED",
    RunState.FAILED: "FAILED",
    RunState.CANCELLED: "CANCELLED",
}


def _order(run: Run) -> dict:
    return (run.result or {}).get("order") or {}


def _discount(run: Run) -> int:
    v = _order(run).get("instant_discount_krw")
    if v is None:
        v = ((run.context or {}).get("target_quote") or {}).get("instant_discount_krw")
    return int(v or 0)


def metrics(runs: list[Run]) -> dict:
    """결제 완료율·평균 결제 금액·혜택으로 아낀 금액·평균 주문 시간(요청 → 결제 완료)."""
    done = [r for r in runs if RunState(r.state) == RunState.COMPLETED]
    charges = [c for c in ((r.result or {}).get("charge_krw") for r in done) if c]
    seconds = [(r.updated_at - r.created_at).total_seconds() for r in done]
    return {
        "started": len(runs),
        "completed": len(done),
        "completion_rate": round(len(done) * 100 / len(runs)) if runs else None,
        "avg_charge_krw": round(sum(charges) / len(charges)) if charges else None,
        "saved_krw": sum(_discount(r) for r in done),
        "avg_order_seconds": round(sum(seconds) / len(seconds)) if seconds else None,
    }


def hourly(runs: list[Run]) -> list[int]:
    """0~23시 시작한 주문 수."""
    out = [0] * 24
    for r in runs:
        out[r.created_at.astimezone(KST).hour] += 1
    return out


def _top(counter: Counter) -> list[dict]:
    return [{"name": k, "count": n} for k, n in counter.most_common(RANK_LIMIT)]


def rankings(runs: list[Run]) -> dict:
    """오늘의 순위: 고른 가게 · 찾은 음식·맛 · 결제수단(완료 기준)."""
    restaurants: Counter = Counter()
    categories: dict[str, str | None] = {}
    foods: Counter = Counter()
    methods: Counter = Counter()
    for r in runs:
        ctx = r.context or {}
        rest = (ctx.get("selected_candidate") or {}).get("restaurant") or {}
        if rest.get("name"):
            restaurants[rest["name"]] += 1
            categories.setdefault(rest["name"], rest.get("cuisine") or rest.get("category"))
        c = ctx.get("constraints") or {}
        words = [c.get("cuisine"), *(c.get("menu_keywords") or []), *(c.get("taste_preferences") or [])]
        for w in {w for w in words if isinstance(w, str) and w}:
            foods[w] += 1
        if RunState(r.state) == RunState.COMPLETED:
            pm = _order(r).get("payment_method") or {}
            name = pm.get("name") or pm.get("payment_method_id") or ((ctx.get("target_quote") or {}).get("payment_method_id"))
            if name:
                methods[name] += 1
    return {
        "restaurants": [{**x, "category": categories.get(x["name"])} for x in _top(restaurants)],
        "foods": _top(foods),
        "payment_methods": [{"name": k, "count": n} for k, n in methods.most_common()],
    }


def services(session: Session, now: datetime) -> list[dict]:
    """연결 상태: 요기요 실시간 확인 · 요기요 장바구니 · AI 요청 분석. 마지막으로 본 시각과 상태.

    status: OK(정상) · WARN(마지막이 실패) · DOWN(작업기 연결 끊김) · IDLE(오늘 기록 없음) · OFF(꺼 둠)
    """
    from app.modules.cart.models import CartJob
    from app.modules.cart.service import CartSettings
    from app.modules.discovery.models import LlmCall
    from app.modules.livecheck.models import RestaurantLiveStatus
    from app.modules.livecheck.service import LiveCheckSettings, worker_seen

    iso = lambda t: t.isoformat() if t else None  # noqa: E731
    out = []

    mode = LiveCheckSettings().mode
    last = session.scalars(
        select(RestaurantLiveStatus)
        .where(RestaurantLiveStatus.checked_at.is_not(None))
        .order_by(RestaurantLiveStatus.checked_at.desc())
        .limit(1)
    ).first()
    seen = worker_seen["at"]
    if mode == "off":
        status, note = "OFF", "꺼 둠"
    elif mode == "remote" and (seen is None or now - seen > timedelta(minutes=2)):
        status, note = "DOWN", "작업기가 연결되지 않았어요"
    elif last is None:
        status, note = "IDLE", "아직 확인 기록이 없어요"
    elif last.error:
        status, note = "WARN", "마지막 확인이 실패했어요"
    else:
        status, note = "OK", None
    out.append({"key": "LIVE_CHECK", "label": "요기요 실시간 확인", "mode": mode, "status": status,
                "note": note, "last_at": iso(last.checked_at if last else None),
                "worker_seen_at": iso(seen) if mode == "remote" else None})

    mode = CartSettings().mode
    job = session.scalars(select(CartJob).order_by(CartJob.created_at.desc()).limit(1)).first()
    if mode == "off":
        status, note = "OFF", "꺼 둠"
    elif job is None:
        status, note = "IDLE", "아직 담기 기록이 없어요"
    elif job.status == "FAILED":
        status, note = "WARN", "마지막 담기가 실패했어요"
    else:
        status, note = "OK", None
    out.append({"key": "YOGIYO_CART", "label": "요기요 장바구니 담기", "mode": mode, "status": status,
                "note": note, "last_at": iso((job.finished_at or job.created_at) if job else None)})

    call = session.scalars(select(LlmCall).order_by(LlmCall.created_at.desc()).limit(1)).first()
    if call is None:
        status, note = "IDLE", "아직 호출 기록이 없어요"
    elif not call.success:
        status, note = "WARN", "마지막 호출이 실패했어요"
    else:
        status, note = "OK", None
    out.append({"key": "AI", "label": "AI 요청 분석", "mode": None, "status": status, "note": note,
                "last_at": iso(call.created_at if call else None),
                "latency_ms": call.latency_ms if call else None})
    return out


def events(session: Session, now: datetime, limit: int = EVENT_LIMIT) -> dict:
    """최근 주문 흐름: 오늘 주문들의 주요 단계(시작·승인 대기·결제 완료·차단·실패 등)와 요기요 금액 재계산."""
    day = start_of_day(now)
    rows = session.scalars(
        select(RunEvent)
        .where(
            RunEvent.created_at >= day,
            RunEvent.event_type.in_([RunEventType.STATE_CHANGED.value, RunEventType.USER_REPLY.value]),
        )
        .order_by(RunEvent.created_at.desc(), RunEvent.seq.desc())
        .limit(limit * 10)
    ).all()
    picked = []
    for e in rows:
        if e.event_type == RunEventType.USER_REPLY:
            if (e.detail or {}).get("type") != "REQUOTE_FROM_CART":
                continue
            kind = "REQUOTED"
        else:
            try:
                kind = FEED_STATES.get(RunState(e.to_state))
            except ValueError:
                kind = None
            if kind is None:
                continue
        picked.append((e, kind))
        if len(picked) >= limit:
            break
    ids = {e.request_id for e, _ in picked}
    runs = {r.request_id: r for r in session.scalars(select(Run).where(Run.request_id.in_(ids))).all()} if ids else {}
    out = []
    for e, kind in picked:
        run = runs.get(e.request_id)
        base = row(run) if run else {}
        requote = ((run.context or {}).get("requote") if run else None) or {}
        out.append({
            "at": e.created_at.isoformat(),
            "request_id": e.request_id,
            "kind": kind,
            "restaurant_name": base.get("restaurant_name"),
            "message": base.get("message"),
            "charge_krw": base.get("charge_krw") if kind in ("COMPLETED", "PAYMENT_UNKNOWN", "AWAITING_APPROVAL") else None,
            "saved_krw": _discount(run) if run and kind == "COMPLETED" else None,
            "reason_codes": base.get("reason_codes") if kind in ("BLOCKED", "FAILED") else [],
            "changes": requote.get("changes") if kind == "REQUOTED" else None,
        })
    return {"count": len(out), "events": out}


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
