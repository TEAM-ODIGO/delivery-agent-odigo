"""O · 오케스트레이터 서비스 (MVP 1, 직접 만든 상태 머신).

주문 1건을 정해진 순서로만 진행한다 (설계 문서 03 4·6절):

  O-01  A-01 → (조건 부족) [NEED_CLARIFICATION] → O-02 CLARIFICATION → A-04 → (확정되면 아래로)
             → (조건 확정) D-01 → A-05 → [CANDIDATES_READY]
  O-02 SELECT_CANDIDATE       B-01 → A-06 → C-01 → [PAYMENT_METHOD_SELECTION]
  O-02 SELECT_PAYMENT_METHOD  B-05 → B-06 → D-02 → [AWAITING_APPROVAL]
  O-02 APPROVE                D-03 → B-07 → (저장: PAYING) → E-01 → 결과 반영 → B-08
  O-02 CANCEL                 D-05 → [CANCELLED]

원칙 (조사 결과 'MVP1 적용 판단 자료'의 필수 항목)
- 순서는 코드로 고정. LLM이 다음 단계를 고르지 않으므로 D를 건너뛰는 길이 없다.
- O는 금액 계산·조건 검증·결제를 하지 않는다. 검증은 D(D-02·D-06), 계산은 B·C.
  단, 결제 직전 E에 보낼 값이 O가 알고 있는 승인 내용(신뢰 상태)과 같은지는 비교한다.
- 실패를 셋으로 나눈다: 일시 장애(재시도 2회 후 503, 상태는 그대로) / 업무 판단(BLOCKED·FAILED) /
  결제 결과 모름(PAYMENT_UNKNOWN, 재결제 금지, E-03으로 확인).
- 결제 요청 직전에 PAYING을 먼저 저장한다. 그 뒤에 서버가 멈춰도 reconcile이 E-03으로 이어 받는다.
- 모든 상태 변경·사용자 응답·하위 호출을 orchestrator_run_event에 남긴다 (소요 시간·오류 코드 포함).
"""

import copy
import hashlib
import json
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm.exc import StaleDataError

from app.core import clock
from app.core.errors import AppError
from app.modules.delegation import DelegationService
from app.modules.delegation.schemas import (
    ConfirmRequest,
    OrderValidationRequest,
    PolicyCreate,
    RevokeRequest,
)
from app.modules.orchestrator import admin
from app.modules.orchestrator.constants import (
    FINAL,
    PAYMENT_STARTED,
    STEPS,
    TOTAL_STEPS,
    WAITING,
    FailCode,
    ReplyType,
    RunEventType,
    RunState,
    SyncStatus,
)
from app.modules.orchestrator.models import CandidateView, IdempotencyRecord, Run, RunEvent
from app.modules.orchestrator.ports import (
    DiscoveryPort,
    OrderingPort,
    PaymentPort,
    PaymentResponseLost,
    RecommendationPort,
)
from app.modules.orchestrator.schemas import CardAdd, RefundCreate, RunCreate

CATALOG_VERSION = "CAT-V1"
TRANSIENT = (TimeoutError, ConnectionError, httpx.TransportError)


# 최소주문금액 미달 후보에 보여 주는 문구 (로컬 확장)
MIN_ORDER_MESSAGE = "최소주문금액이 맞지 않아서 주문이 불가합니다"


class DependencyFailed(Exception):
    """하위 모듈이 재시도 후에도 응답하지 않음 → 503 DEPENDENCY_UNAVAILABLE."""

    def __init__(self, service: str, api: str, code: str):
        super().__init__(f"{service} {api} {code}")
        self.service, self.api, self.code = service, api, code


def _hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _dump(obj: Any) -> Any:
    return obj.model_dump(mode="json") if isinstance(obj, BaseModel) else obj


class OrchestratorService:
    def __init__(
        self,
        session: Session,
        *,
        discovery: DiscoveryPort,
        ordering: OrderingPort,
        benefit: RecommendationPort,
        payment: PaymentPort,
        delegation: DelegationService,
        now: Callable[[], datetime] = clock.now,
        retries: int = 2,
        retry_delay: float = 0.0,
        live_check: Any = None,
    ):
        self.session = session
        # 요기요 실시간 식당 확인 (app.modules.livecheck). None이면 확인하지 않는다 (기존 흐름)
        self.live = live_check if live_check is not None and live_check.enabled else None
        self._after_commit: list[Callable[[], None]] = []
        self.a, self.b, self.c, self.e, self.d = discovery, ordering, benefit, payment, delegation
        self.now = now
        self.retries = retries
        self.retry_delay = retry_delay

    # ================================================================ 공개 API
    def start_run(self, user_id: str, body: RunCreate, idempotency_key: str | None) -> dict:
        """O-01. 새 주문을 만들고 첫 사용자 대기 지점(재질문 또는 후보 선택)까지 진행한다."""
        payload = body.model_dump(mode="json")
        if (cached := self._idem_get(user_id, "O-01", idempotency_key, payload)) is not None:
            return cached
        now = self.now()
        run = Run(
            request_id="REQ" + uuid.uuid4().hex[:12].upper(),
            user_id=user_id,
            state=RunState.RECEIVED,
            state_version=0,
            message=body.message,
            delegation=body.delegation.model_dump(mode="json"),
            context={},
            event_seq=0,
            created_at=now,
            updated_at=now,
        )
        self.session.add(run)
        self._event(run, RunEventType.STATE_CHANGED, to_state=RunState.RECEIVED)
        try:
            res = self._call(
                run,
                "A",
                "A-01",
                lambda: self.a.create_request(
                    {
                        "request_id": run.request_id,
                        "user_id": user_id,
                        "message": body.message,
                        "delivery_address": body.delivery_address.model_dump(mode="json"),
                    }
                ),
            )
            self._after_constraints(run, res)
        except DependencyFailed as exc:
            self._to(run, RunState.FAILED, error=self._dep_error(exc))
            self._commit()
            raise AppError(
                503,
                "DEPENDENCY_UNAVAILABLE",
                "주문 처리에 필요한 서비스가 응답하지 않습니다. 잠시 후 다시 시도해 주세요.",
                {"service": exc.service, "api": exc.api, "request_id": run.request_id},
            ) from exc
        view = self._view(run)
        self._idem_put(user_id, "O-01", idempotency_key, payload, 201, view)
        self._commit()
        return view

    def reply(self, user_id: str, request_id: str, body: BaseModel, idem_key: str | None) -> dict:
        """O-02. 사용자 응답을 받아 다음 대기 지점(또는 끝)까지 진행한다."""
        payload = body.model_dump(mode="json")
        scope = f"O-02:{request_id}"
        if (cached := self._idem_get(user_id, scope, idem_key, payload)) is not None:
            return cached
        run = self._load(request_id, user_id, lock=True)
        rtype = ReplyType(payload["type"])
        if payload["expected_state_version"] != run.state_version:
            raise self._state_conflict(
                run, "화면이 최신 상태가 아닙니다. 새로 고친 뒤 다시 선택해 주세요."
            )
        self._check_reply_allowed(run, rtype)

        snapshot = self._snapshot(run)
        self._event(
            run,
            RunEventType.USER_REPLY,
            run.state,
            run.state,
            # card_tap(NFC 토큰)은 기록에 남기지 않는다
            detail={
                k: v for k, v in payload.items() if k not in ("expected_state_version", "card_tap")
            },
        )
        handler = {
            ReplyType.CLARIFICATION: self._on_clarification,
            ReplyType.SELECT_CANDIDATE: self._on_select_candidate,
            ReplyType.SELECT_PAYMENT_METHOD: self._on_select_payment_method,
            ReplyType.APPROVE: self._on_approve,
            ReplyType.CANCEL: self._on_cancel,
            ReplyType.RECOMMEND_AGAIN: self._on_recommend_again,
            ReplyType.REQUOTE_FROM_CART: self._on_requote_from_cart,
        }[rtype]
        try:
            handler(run, payload)
        except DependencyFailed as exc:
            # 일시 장애: 사용자 화면 상태는 그대로 두고(같은 버전으로 다시 누를 수 있게) 503
            self._restore(run, snapshot, reason=exc.code)
            self._commit()
            raise AppError(
                503,
                "DEPENDENCY_UNAVAILABLE",
                "잠시 후 다시 시도해 주세요.",
                {
                    "service": exc.service,
                    "api": exc.api,
                    "state": run.state,
                    "state_version": run.state_version,
                },
            ) from exc
        except AppError:
            self._restore(run, snapshot, reason="REQUEST_REJECTED")
            self._commit()
            raise
        view = self._view(run)
        self._idem_put(user_id, scope, idem_key, payload, 200, view)
        self._commit()
        self._deliver_payment_results(run)  # 결제 결과가 생겼으면 커밋 뒤에 B-08 전달
        return view

    def get_run(self, user_id: str, request_id: str) -> dict:
        """O-03. 결제 결과를 모르는 상태면 이 때 E-03으로 한 번 더 확인한다."""
        run = self._load(request_id, user_id)
        if run.state in (RunState.PAYING, RunState.PAYMENT_UNKNOWN) or self._pending_sync(run):
            self.reconcile_run(run)
        return self._view(run)

    # ---- 화면 보조 조회 (와이어프레임 v0.3: 07 내 주문, 01-1 결제수단, 08 운영 기록) ----
    def list_runs(self, user_id: str | None, limit: int = 20, state: str | None = None) -> dict:
        """O-09(내 주문) / O-11(관리자: user_id=None이면 전체). 최근 순."""
        if not 1 <= limit <= 200:
            raise AppError(
                400,
                "VALIDATION_ERROR",
                "limit은 1~200이어야 합니다.",
                {"fields": [{"loc": "query.limit", "reason": "out_of_range"}]},
            )
        stmt = select(Run).order_by(Run.created_at.desc(), Run.request_id.desc()).limit(limit)
        if user_id is not None:
            stmt = stmt.where(Run.user_id == user_id)
        if state:
            stmt = stmt.where(Run.state == state)
        rows = self.session.scalars(stmt).all()
        results = [self._row(r, with_admin=user_id is None) for r in rows]
        return {"count": len(results), "results": results}

    @staticmethod
    def _row(run: Run, with_admin: bool) -> dict:
        current, label = STEPS[RunState(run.state)]
        ctx = run.context or {}
        quote = ctx.get("target_quote") or {}
        row = {
            "request_id": run.request_id,
            "message": run.message,
            "state": run.state,
            "step": {"current": current, "total": TOTAL_STEPS, "label": label},
            "restaurant_name": ((ctx.get("selected_candidate") or {}).get("restaurant") or {}).get(
                "name"
            ),
            "charge_krw": (run.result or {}).get("charge_krw") or quote.get("charge_krw"),
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
        }
        if with_admin:
            reasons = (run.blocked or {}).get("reason_codes") or (run.error or {}).get(
                "reason_codes"
            )
            row["user_id"] = run.user_id
            row["reason_codes"] = reasons or ([run.error["code"]] if run.error else [])
        return row

    # ---- 요기요 장바구니 (O-13·O-14). 담기 자체는 cart 모듈이 한다 ----
    CART_STATES = (
        RunState.AWAITING_APPROVAL,
        RunState.PAYING,
        RunState.PAYMENT_UNKNOWN,
        RunState.COMPLETED,
    )

    def cart_target(self, user_id: str, request_id: str) -> dict:
        """O-13: 요기요에 담을 대상 = 사용자가 승인하려는(승인한) B-06 견적. 그 전 단계면 409."""
        run = self._load(request_id, user_id)
        quote = (run.context or {}).get("target_quote")
        if run.state not in self.CART_STATES or not quote:
            raise AppError(
                409,
                "CART_NOT_AVAILABLE",
                "승인 단계부터 요기요 장바구니에 담을 수 있어요.",
                {"state": run.state},
            )
        return {
            "request_id": run.request_id,
            "user_id": run.user_id,
            "quote": quote,
            "message": run.message,
        }

    def check_owner(self, user_id: str, request_id: str) -> None:
        """O-14: 본인 주문인지만 확인 (남의 주문은 404)."""
        self._load(request_id, user_id)

    def list_payment_methods(self, user_id: str) -> dict:
        """O-10: 맡길 결제수단 목록 (C-02)."""
        res = self.c.list_methods(user_id)
        results = [
            {
                "payment_method_id": m["payment_method_id"],
                "name": m.get("name"),
                "type": m.get("type"),
                "status": m.get("status", "ACTIVE"),
                "balance_krw": m.get("balance_krw"),
                "monthly_benefit_remaining_krw": m.get("monthly_benefit_remaining_krw"),
            }
            for m in res.get("results", [])
        ]
        return {"count": len(results), "results": results}

    # ================================================================ 카드 혜택 화면 (로컬 확장, 팀 합의 전)
    def card_dashboard(self, user_id: str, card_tap: dict) -> dict:
        """결제 중이 아닐 때 NFC 카드를 대면: E로 어떤 가상카드인지 찾고 → C의 혜택 요약 + E의 최근 결제 3건.
        주문(Run)을 만들거나 바꾸지 않는다 (읽기 전용). 카드 토큰은 기록하지 않는다."""
        found = self._card_port("identify_card_tap")({"card_tap": card_tap})
        if not found.get("matched"):
            raise AppError(404, found.get("reason_code") or "NFC_CARD_NOT_REGISTERED",
                           found.get("message") or "등록되지 않은 NFC 카드예요.")
        try:
            return self.payment_method_dashboard(user_id, found["payment_method_id"])
        except AppError as exc:
            if exc.code == "PAYMENT_METHOD_NOT_FOUND":  # 다른 사람 카드: 존재 여부를 알려 주지 않는다
                raise AppError(403, "NFC_CARD_NOT_MINE", "내 결제수단에 등록된 카드가 아니에요.") from exc
            raise

    def payment_method_dashboard(self, user_id: str, payment_method_id: str) -> dict:
        """내 결제수단 하나의 혜택 화면 (로컬 확장): C의 혜택 요약 + E의 최근 결제 3건. 읽기 전용.
        NFC 태그(card_dashboard)와 설정 화면의 ⋮ 버튼이 같이 쓴다. 내 결제수단이 아니면 404."""
        summarize = getattr(self.c, "card_dashboard", None)
        if summarize is None:
            raise AppError(503, "CARD_DASHBOARD_UNAVAILABLE", "지금은 카드 혜택을 불러올 수 없어요.")
        summary = summarize(user_id, payment_method_id)
        payments = self._card_port("list_card_payments")(user_id, payment_method_id, 3)
        recent = [self._recent_payment(user_id, p) for p in payments.get("items", [])]
        return {**summary, "recent_payments": recent}

    def refund_payment(self, user_id: str, payment_id: str, body: RefundCreate,
                       idempotency_key: str | None) -> dict:
        """카드 혜택 화면의 환불 버튼 → E-05 (남은 금액 전부). 분할결제면 지역화폐 몫도 함께 돌려준다."""
        payments = self._card_port("list_card_payments")(user_id, body.payment_method_id, 20)
        target = next((p for p in payments.get("items", []) if p["payment_id"] == payment_id), None)
        if target is None:
            raise AppError(404, "PAYMENT_NOT_FOUND", "이 카드의 결제 내역을 찾을 수 없어요.")
        amount = target.get("refundable_krw") or 0
        if target.get("status") != "APPROVED" or amount <= 0:
            raise AppError(409, "NOT_REFUNDABLE", "환불할 수 있는 금액이 없어요.",
                           {"status": target.get("status")})
        key = idempotency_key or f"O-REFUND-{uuid.uuid4().hex}"
        refund = self._card_port("request_refund")(
            payment_id,
            {"user_id": user_id, "amount_krw": amount, "reason": body.reason or "카드 혜택 화면에서 환불"},
            idempotency_key=key,
        )
        if refund.get("status") == "PENDING":  # E-05는 접수 응답 → E-06으로 처리 결과를 한 번 확인한다
            try:
                refund = self._card_port("get_refund")(payment_id, refund["refund_id"])
            except AppError:
                pass  # 결과 확인만 실패: 접수된 상태(PENDING)로 돌려준다
        if refund.get("status") == "REFUNDED":  # 남은 금액 전부를 돌려줬으니 그 결제로 받은 혜택도 되돌린다
            reverse = getattr(self.c, "reverse_usage", None)
            if reverse is not None:
                try:
                    reverse(payment_id)
                except Exception:  # noqa: BLE001 - 환불 결과를 막지 않는다
                    pass
        return {"payment_id": payment_id, **refund}

    # ================================================================ 내 카드 추가 (로컬 확장, 팀 합의 전)
    def my_cards(self, user_id: str) -> dict:
        """내 결제수단 목록 + 카드마다 어떻게 들어왔는지(직접 추가·마이데이터)와 뺄 수 있는지.
        결제수단은 C-02, 등록 정보는 E. E 등록 정보를 못 읽어도 목록은 그대로 보여 준다."""
        res = self.c.list_methods(user_id)
        registered: dict[str, dict] = {}
        lookup = getattr(self.e, "list_card_registrations", None)
        if lookup is not None:
            try:
                registered = {r["payment_method_id"]: r for r in lookup(user_id).get("results", [])}
            except AppError:
                registered = {}
        results = []
        for m in res.get("results", []):
            reg = registered.get(m["payment_method_id"])
            results.append({
                "payment_method_id": m["payment_method_id"],
                "name": m.get("name"),
                "type": m.get("type"),
                "status": m.get("status", "ACTIVE"),
                "product_id": m.get("product_id"),
                "has_benefit_rule": m.get("has_benefit_rule", True),
                "balance_krw": m.get("balance_krw"),
                "monthly_performance_krw": m.get("monthly_performance_krw"),
                "monthly_benefit_remaining_krw": m.get("monthly_benefit_remaining_krw"),
                "source": reg["source"] if reg else None,
                "last4": reg.get("last4") if reg else None,
                "removable": reg is not None,
            })
        return {"count": len(results), "results": results, "can_add": hasattr(self.e, "register_card")}

    def my_card_catalog(self) -> dict:
        return self._my_cards_port("card_catalog")()

    def start_mydata(self, user_id: str) -> dict:
        """모의 마이데이터 정보제공 동의 (진짜 마이데이터가 아니다)."""
        return self._my_cards_port("create_mydata_consent")(user_id)

    def mydata_cards(self, user_id: str, consent_id: str) -> dict:
        return self._my_cards_port("list_mydata_cards")(user_id, consent_id)

    def add_card(self, user_id: str, body: CardAdd) -> dict:
        return self._my_cards_port("register_card")({"user_id": user_id, **body.model_dump(exclude_none=True)})

    def remove_card(self, user_id: str, payment_method_id: str) -> dict:
        return self._my_cards_port("remove_card")(user_id, payment_method_id)

    def _my_cards_port(self, name: str) -> Callable[..., dict]:
        fn = getattr(self.e, name, None)
        if fn is None:
            # 개발 참고: 결제 서버(E)를 연결해야 쓸 수 있다 (.env의 E_PAYMENT_URL·E_BASE_URL)
            raise AppError(503, "MY_CARDS_UNAVAILABLE", "지금은 카드를 추가할 수 없어요. 잠시 후 다시 시도해 주세요.")
        return fn

    def _card_port(self, name: str) -> Callable[..., dict]:
        fn = getattr(self.e, name, None)
        if fn is None:
            raise AppError(503, "PAYMENT_UNAVAILABLE", "결제 서버가 카드 혜택 화면을 지원하지 않아요.")
        return fn

    def _recent_payment(self, user_id: str, p: dict) -> dict:
        run = self.session.get(Run, p.get("request_id")) if p.get("request_id") else None
        ctx = (run.context or {}) if run is not None and run.user_id == user_id else {}
        restaurant = (ctx.get("selected_candidate") or {}).get("restaurant") or {}
        return {
            "payment_id": p["payment_id"],
            "request_id": p.get("request_id"),
            "status": p.get("status"),
            "charge_krw": p.get("charge_krw"),
            "share_krw": p.get("share_krw", p.get("charge_krw")),
            "refunded_krw": p.get("refunded_krw", 0),
            "refundable_krw": p.get("refundable_krw", 0),
            "split": bool(p.get("payments")),
            "approved_at": p.get("approved_at"),
            "created_at": p.get("created_at"),
            "restaurant_name": restaurant.get("name"),
            "message": run.message if ctx else None,
        }

    # ---- 관리자 대시보드 (이슈 #46, 읽기 전용) ----
    def admin_summary(self) -> dict:
        """O-17 오늘 운영 개요 + 조치할 주문."""
        return admin.summary(self.session, self.now())

    def admin_events(self, limit: int = admin.EVENT_LIMIT) -> dict:
        """(로컬 확장, 이슈 #94) 운영 개요의 최근 주문 흐름."""
        return admin.events(self.session, self.now(), max(1, min(limit, 100)))

    def _admin_load(self, request_id: str) -> Run:
        run = self.session.scalars(select(Run).where(Run.request_id == request_id)).first()
        if run is None:
            raise AppError(404, "RUN_NOT_FOUND", "요청을 찾을 수 없습니다.")
        return run

    def admin_run_detail(self, request_id: str) -> dict:
        """O-18 주문 상세 요약 (관리자)."""
        return admin.detail(self._admin_load(request_id))

    def admin_live_check(self, request_id: str) -> dict:
        """O-19 후보 요기요 실시간 확인 결과 (관리자). O-15와 같은 내용, 주인 확인만 없음."""
        run = self._admin_load(request_id)
        return self.live_check_view(run.user_id, request_id)

    def list_audit_logs(self, request_id: str) -> dict:
        """O-12: 이 주문에 대해 D가 남긴 감사 로그 (D-09)."""
        if self.session.get(Run, request_id) is None:
            raise AppError(404, "RUN_NOT_FOUND", "요청을 찾을 수 없습니다.")
        page = self.d.list_audit_logs(request_id=request_id, page_size=100)
        return page.model_dump(mode="json")

    def list_events(self, request_id: str) -> dict:
        """O-04 (1순위). MVP 1은 O 기록만. D-09·A-08을 합치는 것은 MVP 2."""
        if self.session.get(Run, request_id) is None:
            raise AppError(404, "RUN_NOT_FOUND", "요청을 찾을 수 없습니다.")
        rows = self.session.scalars(
            select(RunEvent).where(RunEvent.request_id == request_id).order_by(RunEvent.seq)
        ).all()
        return {
            "request_id": request_id,
            "count": len(rows),
            "events": [
                {
                    "seq": e.seq,
                    "event_type": e.event_type,
                    "from_state": e.from_state,
                    "to_state": e.to_state,
                    "service": e.service,
                    "detail": e.detail,
                    "sync_b08": e.sync_b08,
                    "created_at": e.created_at.isoformat(),
                }
                for e in rows
            ],
        }

    def reconcile_pending(self, limit: int = 50) -> dict:
        """서버 시작 시·주기적으로: 결제 결과를 모르는 주문과 B-08 전달 대기를 마저 처리한다."""
        unknown = self.session.scalars(
            select(Run)
            .where(Run.state.in_([RunState.PAYING, RunState.PAYMENT_UNKNOWN]))
            .limit(limit)
        ).all()
        pending_ids = set(
            self.session.scalars(
                select(RunEvent.request_id).where(RunEvent.sync_b08 == SyncStatus.PENDING)
            ).all()
        )
        runs = {r.request_id: r for r in unknown}
        for rid in pending_ids:
            if rid not in runs and (r := self.session.get(Run, rid)) is not None:
                runs[rid] = r
        for run in runs.values():
            self.reconcile_run(run)
        return {"checked": len(runs)}

    def reconcile_run(self, run: Run) -> None:
        if run.state in (RunState.PAYING, RunState.PAYMENT_UNKNOWN):
            ctx = run.context
            exe = ctx.get("execution_id")
            res: dict | None = None
            try:
                res = self.e.get_by_execution(exe)
                self._event(
                    run,
                    RunEventType.SERVICE_CALL,
                    service="E",
                    detail={"api": "E-03", "status": res.get("status")},
                )
            except AppError as exc:
                self._event(
                    run,
                    RunEventType.SERVICE_ERROR,
                    service="E",
                    detail={"api": "E-03", "code": exc.code, "status_code": exc.status},
                )
                if exc.status == 404:
                    res = self._resend_if_never_reached(run)
            except TRANSIENT as exc:
                self._event(
                    run,
                    RunEventType.SERVICE_ERROR,
                    service="E",
                    detail={"api": "E-03", "code": type(exc).__name__},
                )
            if res is not None:
                self._apply_payment_result(run, res)
            self._commit()
        self._deliver_payment_results(run)

    # ================================================================ 응답별 처리
    def _on_clarification(self, run: Run, p: dict) -> None:
        patch = {a["slot"]: a["value"] for a in p["answers"]}
        res = self._call(
            run,
            "A",
            "A-04",
            lambda: self.a.update_constraints(run.request_id, {"constraints": patch}),
        )
        self._after_constraints(run, res)

    def _on_select_candidate(self, run: Run, p: dict) -> None:
        ctx = run.context
        cand = self._candidate_at(run, p["candidate_index"])
        if cand.get("min_order"):  # 화면이 막아 두지만 서버도 거절한다
            raise AppError(
                400,
                "VALIDATION_ERROR",
                MIN_ORDER_MESSAGE,
                {
                    "fields": [{"loc": "body.candidate_index", "reason": "min_order_not_met"}],
                    **cand["min_order"],
                },
            )
        cid = cand["candidate_id"]
        rid = run.request_id
        self._check_live(run, cand)

        # 예산 초과 확인(로컬 확장): 사용자가 동의했고 A가 이 후보를 예산 초과로
        # 표시했을 때만 기록한다. 동의는 선택할 때마다 새로 정한다
        # (이전 후보에 대한 동의가 다음 후보로 넘어가지 않게).
        consent = bool(p.get("allow_over_budget")) and self._is_over_budget(cand)
        self._set_ctx(
            run,
            over_budget=(
                {"candidate_id": cid, "max_total_krw": cand["estimated_total_krw"]}
                if consent
                else None
            ),
        )

        # B가 품절로 거절할 수 있으므로 A의 선택을 확정하기 전에 주문 가능 여부를 확인한다.
        # B-01은 A의 선택 상태를 읽지 않고 후보의 식당·메뉴와 공용 카탈로그를 검증한다.
        plan_res = self._call(
            run,
            "B",
            "B-01",
            lambda: self.b.create_order_plan(
                {
                    "request_id": rid,
                    "user_id": run.user_id,
                    "candidate_id": cid,
                    "restaurant_id": cand["restaurant"]["restaurant_id"],
                    "items": [
                        {
                            "menu_id": i["menu_id"],
                            "option_ids": i.get("option_ids", []),
                            "quantity": i["quantity"],
                        }
                        for i in cand["items"]
                    ],
                    "constraints_version": cand.get("constraints_version")
                    or ctx["constraints_version"],
                    "constraints": ctx["constraints"],
                    "budget_basis": "POST_FINANCIAL",
                    "catalog_version": cand.get("catalog_version") or CATALOG_VERSION,
                    "policy_id": ctx["policy_id"],
                },
                idempotency_key=f"{rid}:b01:{cid}",
            ),
        )
        if not plan_res.get("order_plans"):
            # MVP 1: 재탐색(A-07)이 없으므로 남은 후보로 다시 고르게 한다
            tried = [*ctx.get("tried_candidate_ids", []), cid]
            self._set_ctx(run, tried_candidate_ids=tried)
            notice = {
                "code": "CANDIDATE_UNAVAILABLE",
                "message": "선택한 메뉴를 지금 주문할 수 없어요. 다른 후보를 골라 주세요.",
                "issues": plan_res.get("issues", []),
            }
            if self._remaining_candidates(run):
                self._to(
                    run,
                    RunState.CANDIDATES_READY,
                    prompt=self._candidate_prompt(run),
                    notice=notice,
                    service="B",
                    detail={"api": "B-01", "unavailable": cid},
                )
            else:
                self._to(
                    run,
                    RunState.FAILED,
                    notice=notice,
                    error={
                        "code": FailCode.NO_CANDIDATE,
                        "message": "주문할 수 있는 후보가 없어요. 조건을 바꿔 다시 주문해 주세요.",
                    },
                    service="B",
                    detail={"api": "B-01", "unavailable": cid},
                )
            return

        plan = plan_res["order_plans"][0]
        sel = self._call(run, "A", "A-06", lambda: self.a.select(rid, {"candidate_id": cid}))
        chosen = sel["selected_candidate"]
        self._set_ctx(
            run,
            selected_candidate=chosen,
            order_plan_id=plan["order_plan_id"],
            order_version=plan["order_version"],
            quote=plan["quote"],
        )
        self._to(
            run,
            RunState.ORDER_BUILT,
            service="B",
            detail={"api": "B-01", "order_plan_id": plan["order_plan_id"]},
        )

        rec = self._call(
            run,
            "C",
            "C-01",
            lambda: self.c.recommend(
                self._c01_body(run, plan["quote"]),
                idempotency_key=self._c01_key(run, plan["quote"]),
            ),
        )
        if not rec.get("options"):
            self._to(
                run,
                RunState.FAILED,
                service="C",
                error={
                    "code": FailCode.NO_PAYMENT_METHOD,
                    "message": "맡긴 결제수단 중 이 주문에 쓸 수 있는 것이 없어요.",
                    "excluded": rec.get("excluded", []),
                },
            )
            return
        self._set_ctx(
            run,
            recommendation={
                "recommendation_id": rec["recommendation_id"],
                "benefit_rule_version": rec["benefit_rule_version"],
                "expires_at": rec.get("expires_at"),
                "options": rec["options"],
                "excluded": rec.get("excluded", []),
            },
        )
        self._to(
            run,
            RunState.PAYMENT_METHOD_SELECTION,
            prompt=self._payment_prompt(run),
            service="C",
            detail={"api": "C-01", "options": len(rec["options"])},
        )

    def _c01_body(self, run: Run, q: dict, local_amount: int | None = None) -> dict:
        """C-01 요청 body. local_amount가 있으면 분할결제(로컬 확장): 지역화폐로 낼 금액."""
        body = {
            "request_id": run.request_id,
            "user_id": run.user_id,
            "restaurant_id": q["restaurant_id"],
            "menu_subtotal_krw": q["menu_subtotal_krw"],
            "delivery_fee_krw": q["delivery_fee_krw"],
            "catalog_version": q.get("catalog_version") or CATALOG_VERSION,
            "payment_requirements": run.context["constraints"].get("payment_requirements")
            or {"local_currency_only": False},
            "candidate_payment_method_ids": run.delegation.get("allowed_payment_method_ids") or [],
        }
        if local_amount:
            body["local_currency_amount_krw"] = local_amount
        return body

    @staticmethod
    def _c01_key(run: Run, quote: dict, local_amount: int | None = None) -> str:
        key = f"{run.request_id}:c01:{run.context['order_plan_id']}:{quote['quote_id']}"
        return f"{key}:split:{local_amount}" if local_amount is not None else key

    def _on_select_payment_method(self, run: Run, p: dict) -> None:
        ctx = run.context
        rec = ctx["recommendation"]
        pm = p["payment_method_id"]
        local_amount = p.get("local_currency_amount_krw")
        if local_amount:
            # 분할결제(로컬 확장): 고객이 정한 지역화폐 금액으로 C-01을 다시 불러 나머지 몫 추천을 받는다
            rec_res = self._call(
                run,
                "C",
                "C-01",
                lambda: self.c.recommend(
                    self._c01_body(run, ctx["quote"], local_amount),
                    idempotency_key=self._c01_key(run, ctx["quote"], local_amount),
                ),
            )
            if not any(o["payment_method_id"] == pm for o in rec_res.get("options", [])):
                raise AppError(
                    409,
                    "SPLIT_NOT_AVAILABLE",
                    "그 금액으로는 지역화폐와 나눠 결제할 수 없어요.",
                    {"excluded": rec_res.get("excluded", [])},
                )
            rec = {
                "recommendation_id": rec_res["recommendation_id"],
                "benefit_rule_version": rec_res["benefit_rule_version"],
                "expires_at": rec_res.get("expires_at"),
                "options": rec_res["options"],
                "excluded": rec_res.get("excluded", []),
            }
            self._set_ctx(run, recommendation=rec)
        if not any(o["payment_method_id"] == pm for o in rec["options"]):
            raise AppError(
                400,
                "VALIDATION_ERROR",
                "추천 목록에 없는 결제수단입니다.",
                {"fields": [{"loc": "body.payment_method_id", "reason": "not_recommended"}]},
            )
        rid, plan_id, ver = run.request_id, ctx["order_plan_id"], ctx["order_version"]
        quote_id = ctx["quote"]["quote_id"]
        b05 = {
            "base_order_version": ver,
            "quote_id": quote_id,
            "recommendation_id": rec["recommendation_id"],
            "payment_method_id": pm,
            "benefit_rule_version": rec["benefit_rule_version"],
        }
        b05_key = f"{rid}:b05:{ver}:{pm}"
        if local_amount:
            option = next(o for o in rec["options"] if o["payment_method_id"] == pm)
            if not option.get("split"):  # C가 분할 추천을 주지 못함 (분할 미지원 C)
                raise AppError(
                    409, "SPLIT_NOT_AVAILABLE", "지역화폐 분할결제를 쓸 수 없는 상태예요."
                )
            local_pm = option["split"]["local_currency"]["payment_method_id"]
            b05.update(
                local_currency_payment_method_id=local_pm, local_currency_amount_krw=local_amount
            )
            b05_key += f":{local_pm}:{local_amount}"
        try:
            bind = self._call(
                run,
                "B",
                "B-05",
                lambda: self.b.bind_benefit(plan_id, b05, idempotency_key=b05_key),
            )
            revision = bind["benefit_binding"]["benefit_revision"]
            target_res = self._call(
                run,
                "B",
                "B-06",
                lambda: self.b.create_approval_target(
                    plan_id,
                    {
                        "base_order_version": ver,
                        "quote_id": quote_id,
                        "expected_benefit_revision": revision,
                    },
                    idempotency_key=f"{rid}:b06:{ver}:{revision}",
                ),
            )
        except AppError as exc:
            if exc.status == 409:  # 혜택 불일치·버전 충돌 → MVP 1은 다시 주문 안내
                self._block(
                    run,
                    [exc.code],
                    {"message": "주문 정보가 바뀌었어요. 다시 주문해 주세요."},
                    service="B",
                )
                return
            raise
        target = target_res["approval_target"]
        self._set_ctx(
            run,
            payment_method_id=pm,
            benefit_revision=revision,
            approval_target_id=target["approval_target_id"],
            target_order_version=target["order_version"],
            target_quote=target["quote"],
        )
        self._to(
            run,
            RunState.QUOTE_CONFIRMED,
            service="B",
            detail={"api": "B-06", "approval_target_id": target["approval_target_id"]},
        )
        self._to(run, RunState.VALIDATING)

        self._relax_budget_if_consented(run, target["quote"])
        policy = self._policy(run)
        try:
            result = self._call(
                run,
                "D",
                "D-02",
                lambda: self.d.validate_order(
                    OrderValidationRequest.model_validate(
                        {
                            "request_id": rid,
                            "user_id": run.user_id,
                            "policy_id": policy["policy_id"],
                            "policy_version": policy["policy_version"],
                            "approval_target_id": target["approval_target_id"],
                            "order_version": target["order_version"],
                            "quote": target["quote"],  # B 견적 그대로 (O는 고치지 않음)
                        }
                    )
                ),
            )
        except AppError as exc:
            self._block(run, [exc.code], exc.details, service="D")
            return
        if not result["valid"]:
            self._block(run, result["reason_codes"], result.get("details") or {}, service="D")
            return
        self._set_ctx(
            run,
            approval_id=result["approval_id"],
            proposal_hash=result["proposal_hash"],
            approval_expires_at=result["expires_at"],
        )
        self._to(
            run,
            RunState.AWAITING_APPROVAL,
            prompt=self._approval_prompt(run),
            service="D",
            detail={"api": "D-02", "valid": True},
        )

    def _on_approve(self, run: Run, p: dict) -> None:
        ctx = run.context
        rid = run.request_id
        if p.get("card_tap") and not ctx.get("execution_id"):
            self._check_card_tap(run, p["card_tap"])  # 다른 카드면 여기서 멈춘다 (결제 시작 전)
        if not ctx.get("execution_id"):
            try:
                conf = self._call(
                    run,
                    "D",
                    "D-03",
                    lambda: self.d.confirm(
                        ctx["approval_id"],
                        ConfirmRequest(user_id=run.user_id, proposal_hash=ctx["proposal_hash"]),
                    ),
                )
            except AppError as exc:  # 만료·철회·조건 변경 → 결제하지 않음
                self._block(run, [exc.code], exc.details, service="D")
                return
            self._set_ctx(run, execution_id=conf["execution_id"])
        exe = run.context["execution_id"]
        target_quote = ctx["target_quote"]

        try:
            check = self._call(
                run,
                "B",
                "B-07",
                lambda: self.b.check_execution(
                    ctx["order_plan_id"],
                    {
                        "base_order_version": ctx["order_version"],
                        "quote_id": target_quote["quote_id"],
                        "approval_target_id": ctx["approval_target_id"],
                        "approval_id": ctx["approval_id"],
                        "execution_id": exe,
                        "stage": "MOCK_PAYMENT",
                    },
                ),
            )
        except AppError as exc:
            self._block(run, [exc.code], exc.details, service="B")
            return
        if not check.get("ready"):
            self._block(run, check.get("reasons") or ["NOT_READY"], {}, service="B")
            return

        # 신뢰 상태 비교: E에 보낼 값이 사용자가 승인한 내용(D-02·B-06)과 같은지
        ei = check.get("execution_input") or {}
        expected = {
            "execution_id": exe,
            "approval_id": ctx["approval_id"],
            "quote_id": target_quote["quote_id"],
            "payment_method_id": target_quote["payment_method_id"],
            "charge_krw": target_quote["charge_krw"],
        }
        if target_quote.get("local_currency_payment_method_id"):  # 분할결제(로컬 확장)
            expected["local_currency_payment_method_id"] = target_quote[
                "local_currency_payment_method_id"
            ]
            expected["local_currency_charge_krw"] = target_quote["local_currency_charge_krw"]
        mismatched = [k for k, v in expected.items() if ei.get(k) != v]
        if ei.get("proposal_hash") not in (None, ctx["proposal_hash"]):
            mismatched.append("proposal_hash")
        if mismatched:
            self._block(run, ["EXECUTION_INPUT_MISMATCH"], {"fields": mismatched}, service="O")
            return

        e01 = {
            "request_id": rid,
            "user_id": run.user_id,
            **expected,
            # 명세 공백: B-07 응답의 proposal_hash를 B가 얻을 길이 없음 → O가 D-02 값으로 채움
            "proposal_hash": ei.get("proposal_hash") or ctx["proposal_hash"],
        }
        if p.get("card_tap"):  # NFC 실물 카드(로컬 확장): E도 결제 직전에 한 번 더 확인한다
            e01["card_tap"] = p["card_tap"]
        self._set_ctx(run, payment_request=e01)
        self._to(run, RunState.PAYING, prompt=None, service="B", detail={"api": "B-07"})
        self._commit()  # ← 결제 요청 전에 저장: 여기서 멈춰도 reconcile이 E-03으로 이어 받는다

        t0 = time.perf_counter()
        try:
            res = self.e.request_payment(e01)  # 재시도 금지
            self._event(
                run,
                RunEventType.SERVICE_CALL,
                service="E",
                detail={"api": "E-01", "status": res.get("status"), "duration_ms": self._ms(t0)},
            )
        except (PaymentResponseLost, *TRANSIENT) as exc:
            self._event(
                run,
                RunEventType.SERVICE_ERROR,
                service="E",
                detail={
                    "api": "E-01",
                    "code": "RESPONSE_LOST",
                    "error": type(exc).__name__,
                    "duration_ms": self._ms(t0),
                },
            )
            res = None
        except AppError as exc:
            self._event(
                run,
                RunEventType.SERVICE_ERROR,
                service="E",
                detail={
                    "api": "E-01",
                    "code": exc.code,
                    "status_code": exc.status,
                    "duration_ms": self._ms(t0),
                },
            )
            if exc.status >= 500 or exc.status == 409:
                res = None  # 결과를 모름
            else:
                res = {
                    "payment_id": None,
                    "execution_id": exe,
                    "status": "REJECTED",
                    "reason_codes": [exc.code],
                }
        self._apply_payment_result(run, res)

    def _check_card_tap(self, run: Run, card_tap: dict) -> None:
        """NFC 실물 카드(로컬 확장): 고른 가상카드의 실물 카드를 태그했는지 결제 전에 E에 묻는다.
        다르면 결제를 시작하지 않고 오류를 돌려준다. 주문은 승인 화면에 그대로 있어 다시 태그할 수 있다."""
        payment_method_id = run.context["target_quote"]["payment_method_id"]
        body = {"payment_method_id": payment_method_id, "card_tap": card_tap}
        res = self._call(run, "E", "E-NFC", lambda: self.e.verify_card_tap(body))
        if not res.get("matched"):
            code = res.get("reason_code") or "NFC_CARD_MISMATCH"
            message = res.get("message") or "결제할 수 없는 카드예요. 다시 태그해 주세요."
            raise AppError(422, code, message, {"payment_method_id": payment_method_id})

    def _on_recommend_again(self, run: Run, p: dict) -> None:
        """후보 화면 '다시 추천' (로컬 확장, 이슈 #68).

        지금까지 추천한 가게를 모두 빼고 A-05로 다시 찾는다. 새 후보로 같은 후보 화면을 다시 보여 주고
        요기요 실시간 확인도 새 후보로 다시 한다. 더 찾을 가게가 없으면 지금 후보를 그대로 두고 409로 알린다.
        """
        ctx = run.context or {}
        shown = list(dict.fromkeys(
            [*ctx.get("recommended_restaurant_ids", []),
             *[c["restaurant"]["restaurant_id"] for c in ctx.get("candidates", [])]]
        ))
        top_n = self.live.cfg.top_n if self.live else 3
        rid = run.request_id
        found = self._call(
            run, "A", "A-05", lambda: self.a.search(rid, {"top_n": top_n, "exclude_restaurant_ids": shown})
        )
        if not found.get("candidates"):
            raise AppError(
                409,
                "NO_MORE_CANDIDATES",
                "더 추천할 가게가 없어요. 지금 후보 중에서 고르거나 조건을 바꿔 다시 주문해 주세요.",
                {"recommended": len(shown)},
            )
        cands = [{**c, "index": i} for i, c in enumerate(found["candidates"])]
        cands = self._mark_min_order(run, cands, ctx.get("constraints") or {})
        self._set_ctx(
            run,
            candidates=cands,
            tried_candidate_ids=[],
            search_notice=found.get("search_notice"),
            recommended_restaurant_ids=shown,
            recommend_round=int(ctx.get("recommend_round", 1)) + 1,
        )
        if self.live:
            self._start_live_check(run)
        self._to(
            run,
            RunState.CANDIDATES_READY,
            prompt=self._candidate_prompt(run),
            service="A",
            detail={"api": "A-05", "candidates": len(cands), "recommend_again": True, "excluded": len(shown)},
        )

    def _latest_cart_job(self, request_id: str):
        from app.modules.cart.models import CartJob

        # 읽기만 한다: 아직 쓰지 않은 주문 변경을 여기서 flush하면 D(다른 세션)의 쓰기와 잠금이 겹친다
        with self.session.no_autoflush:
            return self.session.scalars(
                select(CartJob)
                .where(CartJob.request_id == request_id)
                .order_by(CartJob.created_at.desc())
                .limit(1)
            ).first()

    def _on_requote_from_cart(self, run: Run, p: dict) -> None:
        """승인 화면 (로컬 확장, 이슈 #86): 요기요 장바구니에서 확인한 가격·배달비로 다시 견적을 낸다.

        화면 숫자만 바꾸면 결제 금액(승인 대상)과 달라지므로 B-10 재견적 → C-01 → B-05 → B-06 → D-02를
        다시 거친다. 메뉴·수량이 다르면(담기 자체가 다름) 금액만 바꿀 수 없어 거절한다. 결제수단은 그대로.
        """
        ctx = run.context
        job = self._latest_cart_job(run.request_id)
        if job is None or job.status != "CART_MISMATCH":
            raise AppError(
                409, "CART_NOT_MISMATCH", "요기요 장바구니 금액이 견적과 다를 때만 다시 계산할 수 있어요."
            )
        if ctx.get("requote", {}).get("cart_job_id") == job.job_id:
            raise AppError(409, "ALREADY_REQUOTED", "이미 요기요 금액으로 다시 계산했어요.")
        q = ctx["target_quote"]
        cart = {str(i.get("menu_id")): i for i in job.cart_items or []}
        if {str(i["menu_id"]): i["quantity"] for i in q["items"]} != {
            k: v.get("quantity") for k, v in cart.items()
        }:
            raise AppError(
                409,
                "CART_ITEMS_DIFFER",
                "요기요에 담긴 메뉴·수량이 주문안과 달라 금액만 바꿀 수 없어요. 다시 담거나 주문을 취소해 주세요.",
            )
        prices, changes = [], []
        for it in q["items"]:
            new = cart[str(it["menu_id"])].get("unit_price_krw")
            if new is not None and new != it["unit_price_krw"]:
                prices.append({"menu_id": str(it["menu_id"]), "unit_price_krw": new})
                changes.append(
                    {"kind": "MENU_PRICE", "name": it.get("name"), "from_krw": it["unit_price_krw"], "to_krw": new}
                )
        fee = job.delivery_fee_krw
        if fee is not None and fee != q["delivery_fee_krw"]:
            changes.append({"kind": "DELIVERY_FEE", "name": "배달비", "from_krw": q["delivery_fee_krw"], "to_krw": fee})
        else:
            fee = None
        if not changes:
            raise AppError(409, "NOTHING_TO_REQUOTE", "요기요 금액이 견적과 같아 다시 계산할 것이 없어요.")

        previous = {
            "charge_krw": q["charge_krw"],
            "menu_subtotal_krw": q["menu_subtotal_krw"],
            "delivery_fee_krw": q["delivery_fee_krw"],
            "instant_discount_krw": q.get("instant_discount_krw") or 0,
        }
        pm = ctx["payment_method_id"]
        local_amount = q.get("local_currency_amount_krw")
        rid, plan_id, ver = run.request_id, ctx["order_plan_id"], ctx["order_version"]
        try:
            res = self._call(
                run,
                "B",
                "B-10",
                lambda: self.b.requote(
                    plan_id,
                    {
                        "base_order_version": ver,
                        "quote_id": ctx["quote"]["quote_id"],
                        "source": "YOGIYO_CART",
                        "menu_prices": prices,
                        "delivery_fee_krw": fee,
                    },
                    idempotency_key=f"{rid}:b10:{ver}",
                ),
            )
            view = res["view"]
            self._set_ctx(
                run,
                order_version=view["order_version"],
                quote=view["quote"],
                requote={"cart_job_id": job.job_id, "previous": previous, "changes": changes},
            )
            rec = self._call(
                run,
                "C",
                "C-01",
                lambda: self.c.recommend(
                    self._c01_body(run, view["quote"]),
                    idempotency_key=self._c01_key(run, view["quote"]),
                ),
            )
            if not any(o["payment_method_id"] == pm for o in rec.get("options", [])):
                self._block(
                    run,
                    ["PAYMENT_METHOD_NOT_AVAILABLE"],
                    {"message": "요기요 금액으로 다시 계산하니 고른 결제수단을 쓸 수 없어요. 다시 주문해 주세요."},
                    service="C",
                )
                return
            self._set_ctx(
                run,
                recommendation={
                    "recommendation_id": rec["recommendation_id"],
                    "benefit_rule_version": rec["benefit_rule_version"],
                    "expires_at": rec.get("expires_at"),
                    "options": rec["options"],
                    "excluded": rec.get("excluded", []),
                },
            )
            self._on_select_payment_method(
                run,
                {"payment_method_id": pm, **({"local_currency_amount_krw": local_amount} if local_amount else {})},
            )
        except AppError as exc:
            # B 재견적은 이미 끝났을 수 있다 → 예전 승인 화면에 머물지 않고 멈춘다 (결제하지 않음)
            self._block(
                run,
                [exc.code],
                {"message": "요기요 금액으로 다시 계산하지 못했어요. 주문을 취소하고 다시 주문해 주세요."},
                service="B",
            )

    def _on_cancel(self, run: Run, p: dict) -> None:
        approval_id = run.context.get("approval_id")
        if approval_id:
            try:
                self._call(
                    run,
                    "D",
                    "D-05",
                    lambda: self.d.revoke(approval_id, RevokeRequest(reason_code="USER_CANCELLED")),
                )
            except AppError as exc:
                if exc.code == "ALREADY_EXECUTED":
                    raise self._state_conflict(
                        run, "이미 결제가 진행됐습니다. 환불을 이용해 주세요."
                    ) from exc
                raise
        self._to(run, RunState.CANCELLED, prompt=None, detail={"by": "USER"})

    # ================================================================ 단계 공통
    def _after_constraints(self, run: Run, a: dict) -> None:
        """A-01·A-04 결과 반영. 조건이 확정되면 D-01 등록(또는 재등록) → A-05 탐색."""
        self._set_ctx(
            run,
            constraints=a["constraints"],
            constraints_version=a.get("constraints_version"),
            constraints_confirmed=a.get("constraints_confirmed", False),
        )
        if a.get("status") == "NEED_CLARIFICATION" or not a.get("constraints_confirmed"):
            questions = [
                {k: q.get(k) for k in ("slot", "question", "input_type", "options", "required")}
                for q in a.get("clarification_questions", [])
            ]
            self._to(
                run,
                RunState.NEED_CLARIFICATION,
                service="A",
                prompt={"type": "CLARIFICATION", "questions": questions},
            )
            return

        rid = run.request_id
        policy = self._call(
            run,
            "D",
            "D-01",
            lambda: self.d.register_policy(
                PolicyCreate.model_validate(
                    {
                        "request_id": rid,
                        "user_id": run.user_id,
                        "original_message": run.message,
                        "constraints_confirmed": True,
                        "constraints": a["constraints"],
                        "delegation": run.delegation,
                    }
                )
            ),
        )
        self._set_ctx(run, policy_id=policy["policy_id"], policy_version=policy["policy_version"])

        top_n = self.live.cfg.top_n if self.live else 3
        found = self._call(run, "A", "A-05", lambda: self.a.search(rid, {"top_n": top_n}))
        cands = [{**c, "index": i} for i, c in enumerate(found.get("candidates", []))]
        cands = self._mark_min_order(run, cands, a["constraints"])
        # 요청한 메뉴 대신 비슷한 메뉴로 채웠을 때 A가 주는 이유 (이슈 #37, 후보 화면 위 안내)
        self._set_ctx(
            run, candidates=cands, tried_candidate_ids=[], search_notice=found.get("search_notice")
        )
        if self.live and cands:
            self._start_live_check(run)
        if not cands:
            self._to(
                run,
                RunState.FAILED,
                service="A",
                error={
                    "code": FailCode.NO_CANDIDATE,
                    "message": "조건에 맞는 가게를 찾지 못했어요. 조건을 바꿔 다시 주문해 주세요.",
                },
            )
            return
        self._to(
            run,
            RunState.CANDIDATES_READY,
            prompt=self._candidate_prompt(run),
            service="A",
            detail={"api": "A-05", "candidates": len(cands)},
        )

    def _apply_payment_result(self, run: Run, res: dict | None) -> None:
        """E 결과 → 주문 상태 + PAYMENT_RESULT 기록(B-08 전달 대기)을 같은 트랜잭션으로."""
        exe = run.context.get("execution_id")
        status = (res or {}).get("status") or "UNKNOWN"
        if status in ("APPROVED", "FAILED", "UNKNOWN", "PENDING") and (res or {}).get("payment_id"):
            self._record_payment_result(run, res)
        result = {
            "payment_id": (res or {}).get("payment_id"),
            "payment_status": status,
            "charge_krw": (res or {}).get("charge_krw"),
        }
        if (res or {}).get("payments"):  # 분할결제(로컬 확장): 수단별 결제 내역
            info = self._method_info(run)
            result["payments"] = [
                {**leg, "name": info.get(leg["payment_method_id"], {}).get("name")}
                for leg in res["payments"]
            ]
        result["order"] = self._order_view(run)
        if status == "APPROVED":
            self._record_benefit_usage(run, res)
            self._to(run, RunState.COMPLETED, prompt=None, result=result, service="E")
        elif status == "FAILED":
            self._to(
                run,
                RunState.FAILED,
                prompt=None,
                result=result,
                service="E",
                error={
                    "code": FailCode.PAYMENT_FAILED,
                    "reason_codes": res.get("reason_codes", []),
                    "message": "결제가 거절됐어요.",
                },
            )
        elif status == "BLOCKED":  # D-06 차단: 결제가 만들어지지 않음 → B-08에 보내지 않음
            self._block(run, res.get("reason_codes", []), {"stage": "D-06"}, service="E")
        elif status == "REJECTED":  # E가 요청 자체를 거절(4xx): 결제 없음
            self._to(
                run,
                RunState.FAILED,
                prompt=None,
                service="E",
                error={
                    "code": FailCode.REQUEST_REJECTED,
                    "reason_codes": res.get("reason_codes", []),
                },
            )
        elif run.state != RunState.PAYMENT_UNKNOWN:  # UNKNOWN·PENDING·응답 없음
            self._to(
                run,
                RunState.PAYMENT_UNKNOWN,
                prompt=None,
                result=result,
                service="E",
                detail={"execution_id": exe},
            )

    def _record_benefit_usage(self, run: Run, res: dict) -> None:
        """결제가 승인되면 받은 할인액을 C의 '이번 달 받은 혜택'에 더한다 (로컬 확장, 팀 합의 전).

        분할결제여도 할인은 카드 몫에만 붙는다(충전식 지역화폐는 할인이 없다) → 카드 기준으로 기록한다.
        같은 payment_id는 C가 한 번만 반영하므로 reconcile이 다시 불러도 안전하다.
        C가 이 기능을 모르거나(가짜 C) 실패해도 결제 결과는 그대로 둔다.
        """
        record = getattr(self.c, "record_usage", None)
        q = run.context.get("target_quote") or {}
        amount = q.get("instant_discount_krw") or 0
        if record is None or not res.get("payment_id") or amount <= 0:
            return
        try:
            record(res["payment_id"], q["payment_method_id"], amount)
        except Exception as exc:  # noqa: BLE001 - 남은 혜택 표시용 누적일 뿐
            self._event(
                run,
                RunEventType.SERVICE_ERROR,
                service="C",
                detail={"api": "C-USAGE", "code": getattr(exc, "code", type(exc).__name__)},
            )

    def _record_payment_result(self, run: Run, res: dict) -> None:
        exe = run.context.get("execution_id")
        event_id = f"{exe}:{res['status']}"  # B-08 event_id: 다시 보내도 B가 한 번만 반영
        if self._has_payment_event(run.request_id, event_id):
            return  # 같은 결과는 한 번만 기록 (reconcile이 같은 결과를 다시 받아도)
        self._event(
            run,
            RunEventType.PAYMENT_RESULT,
            run.state,
            run.state,
            service="E",
            detail={
                "event_id": event_id,
                "execution_id": exe,
                "payment_id": res["payment_id"],
                "status": res["status"],
                "reason_code": (res.get("reason_codes") or [None])[0],
                "observed_at": self.now().isoformat(),
            },
            sync_b08=SyncStatus.PENDING,
        )

    def _has_payment_event(self, request_id: str, event_id: str) -> bool:
        rows = self.session.scalars(
            select(RunEvent).where(
                RunEvent.request_id == request_id,
                RunEvent.event_type == RunEventType.PAYMENT_RESULT,
            )
        ).all()
        return any((r.detail or {}).get("event_id") == event_id for r in rows)

    def _deliver_payment_results(self, run: Run) -> None:
        """커밋 뒤에 B-08 전달. 실패하면 PENDING으로 남고 reconcile이 다시 보낸다."""
        rows = self.session.scalars(
            select(RunEvent)
            .where(RunEvent.request_id == run.request_id, RunEvent.sync_b08 == SyncStatus.PENDING)
            .order_by(RunEvent.seq)
        ).all()
        if not rows:
            return
        ctx = run.context
        for row in rows:
            d = row.detail
            body = {
                "event_id": d["event_id"],
                "approval_target_id": ctx.get("approval_target_id"),
                "quote_id": ctx.get("target_quote", {}).get("quote_id"),
                "execution_id": d["execution_id"],
                "payment_id": d["payment_id"],
                "status": d["status"],
                "observed_at": d["observed_at"],
                "reason_code": d.get("reason_code"),
            }
            t0 = time.perf_counter()
            try:
                self.b.record_payment_event(
                    ctx["order_plan_id"], body, idempotency_key=d["event_id"]
                )
            except (AppError, *TRANSIENT) as exc:
                self._event(
                    run,
                    RunEventType.SERVICE_ERROR,
                    service="B",
                    detail={
                        "api": "B-08",
                        "event_id": d["event_id"],
                        "code": getattr(exc, "code", type(exc).__name__),
                        "duration_ms": self._ms(t0),
                    },
                )
                continue
            row.sync_b08 = SyncStatus.DONE
            self._event(
                run,
                RunEventType.SERVICE_CALL,
                service="B",
                detail={"api": "B-08", "event_id": d["event_id"], "duration_ms": self._ms(t0)},
            )
        self._commit()

    def _resend_if_never_reached(self, run: Run) -> dict | None:
        """E-03 404 = E에 도달하지 못함. 승인이 아직 유효할 때만 같은 execution_id로 E-01 재요청."""
        ctx = run.context
        try:
            approval = _dump(self.d.get_approval(ctx["approval_id"]))
        except AppError:
            approval = {}
        status = approval.get("status")
        if status in ("CONSUMED", None):
            # D-06을 이미 통과했는데(또는 확인 불가) E에 기록이 없다 = 결제 여부를 알 수 없음.
            # 단정하지 않고 PAYMENT_UNKNOWN에 두고 사람 확인(E 대사)이 필요하다고 남긴다.
            self._event(
                run,
                RunEventType.SERVICE_ERROR,
                service="E",
                detail={
                    "api": "E-03",
                    "code": "PAYMENT_RECORD_MISSING",
                    "approval_status": status,
                    "needs_manual_check": True,
                },
            )
            if run.state != RunState.PAYMENT_UNKNOWN:
                self._to(run, RunState.PAYMENT_UNKNOWN, prompt=None, service="E")
            return None
        if status != "APPROVED" or not ctx.get("payment_request"):
            # 승인이 만료·철회돼 D-06을 통과할 수 없었다 = 결제는 시작되지 않음
            self._to(
                run,
                RunState.FAILED,
                prompt=None,
                service="E",
                error={
                    "code": FailCode.PAYMENT_NOT_STARTED,
                    "message": "결제가 시작되지 않았어요. 다시 주문해 주세요.",
                    "approval_status": approval.get("status"),
                },
            )
            return None
        try:
            res = self.e.request_payment(ctx["payment_request"])
            self._event(
                run,
                RunEventType.SERVICE_CALL,
                service="E",
                detail={"api": "E-01", "resend": True, "status": res.get("status")},
            )
            return res
        except (PaymentResponseLost, AppError, *TRANSIENT) as exc:
            self._event(
                run,
                RunEventType.SERVICE_ERROR,
                service="E",
                detail={
                    "api": "E-01",
                    "resend": True,
                    "code": getattr(exc, "code", type(exc).__name__),
                },
            )
            return None

    # ================================================================ 하위 호출
    def _call(self, run: Run, service: str, api: str, fn: Callable[[], Any]) -> Any:
        """하위 모듈 호출 1회 = 기록 1줄. 일시 장애만 재시도(최대 retries회), 업무 오류는 그대로."""
        attempts = 1 + self.retries
        for attempt in range(1, attempts + 1):
            t0 = time.perf_counter()
            try:
                out = _dump(fn())
            except AppError as exc:
                transient = exc.status >= 500
                self._event(
                    run,
                    RunEventType.SERVICE_ERROR,
                    service=service,
                    detail={
                        "api": api,
                        "code": exc.code,
                        "status_code": exc.status,
                        "attempt": attempt,
                        "duration_ms": self._ms(t0),
                    },
                )
                if not transient:
                    raise
                if attempt == attempts:
                    raise DependencyFailed(service, api, exc.code) from exc
            except TRANSIENT as exc:
                self._event(
                    run,
                    RunEventType.SERVICE_ERROR,
                    service=service,
                    detail={
                        "api": api,
                        "code": type(exc).__name__,
                        "attempt": attempt,
                        "duration_ms": self._ms(t0),
                    },
                )
                if attempt == attempts:
                    raise DependencyFailed(service, api, type(exc).__name__) from exc
            else:
                self._event(
                    run,
                    RunEventType.SERVICE_CALL,
                    service=service,
                    detail={"api": api, "attempt": attempt, "duration_ms": self._ms(t0)},
                )
                return out
            if self.retry_delay:
                time.sleep(self.retry_delay * attempt)
        raise AssertionError("unreachable")

    # ================================================================ 상태·기록 도구
    def _to(
        self,
        run: Run,
        state: RunState,
        *,
        prompt: dict | None = None,
        notice: dict | None = None,
        blocked: dict | None = None,
        error: dict | None = None,
        result: dict | None = None,
        service: str | None = None,
        detail: dict | None = None,
    ) -> None:
        frm = run.state
        run.state = state
        run.state_version += 1
        run.prompt, run.notice, run.blocked, run.error = prompt, notice, blocked, error
        if result is not None:
            run.result = result
        run.updated_at = self.now()
        self._event(run, RunEventType.STATE_CHANGED, frm, state, service=service, detail=detail)

    def _block(self, run: Run, reasons: list[str], details: dict, *, service: str) -> None:
        self._to(
            run,
            RunState.BLOCKED,
            prompt=None,
            service=service,
            blocked={"reason_codes": list(reasons), "details": details or {}},
        )

    def _event(
        self,
        run: Run,
        event_type: RunEventType,
        from_state=None,
        to_state=None,
        *,
        service: str | None = None,
        detail: dict | None = None,
        sync_b08: str | None = None,
    ) -> None:
        run.event_seq = (run.event_seq or 0) + 1
        self.session.add(
            RunEvent(
                event_id=str(uuid.uuid4()),
                request_id=run.request_id,
                seq=run.event_seq,
                event_type=event_type,
                from_state=from_state,
                to_state=to_state,
                service=service,
                detail=detail or {},
                sync_b08=sync_b08,
                created_at=self.now(),
            )
        )

    def _set_ctx(self, run: Run, **values: Any) -> None:
        ctx = copy.deepcopy(run.context or {})
        ctx.update(values)
        run.context = ctx
        flag_modified(run, "context")

    @staticmethod
    def _snapshot(run: Run) -> dict:
        return {
            k: copy.deepcopy(getattr(run, k))
            for k in ("state", "state_version", "prompt", "notice", "blocked", "result", "error")
        }

    def _restore(self, run: Run, snap: dict, *, reason: str) -> None:
        """화면 상태를 되돌린다. 하위 호출 기록과 context(받은 ID)는 남긴다 (같은 키로 재시도)."""
        if run.state == snap["state"] and run.state_version == snap["state_version"]:
            return
        frm = run.state
        for k, v in snap.items():
            setattr(run, k, v)
        self._event(
            run,
            RunEventType.STATE_CHANGED,
            frm,
            run.state,
            detail={"rolled_back": True, "reason": reason},
        )

    def _commit(self) -> None:
        try:
            self.session.commit()
        except StaleDataError as exc:  # 같은 주문을 다른 요청이 먼저 바꿈
            self.session.rollback()
            self._after_commit.clear()
            raise AppError(
                409,
                "INVALID_STATE_FOR_REPLY",
                "다른 요청이 먼저 처리됐습니다. 새로 고친 뒤 다시 시도해 주세요.",
            ) from exc
        callbacks, self._after_commit = self._after_commit, []
        for fn in callbacks:  # 저장이 끝난 뒤에 할 일 (예: 요기요 실시간 확인 시작)
            fn()

    def _load(self, request_id: str, user_id: str, *, lock: bool = False) -> Run:
        stmt = select(Run).where(Run.request_id == request_id)
        if lock and self.session.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update()
        run = self.session.scalars(stmt).first()
        if run is None or run.user_id != user_id:  # 남의 주문은 존재 여부도 숨김
            raise AppError(404, "RUN_NOT_FOUND", "요청을 찾을 수 없습니다.")
        return run

    def _check_reply_allowed(self, run: Run, rtype: ReplyType) -> None:
        state = RunState(run.state)
        if rtype == ReplyType.CANCEL:
            if state in PAYMENT_STARTED:
                raise self._state_conflict(
                    run, "결제가 시작된 뒤에는 취소할 수 없습니다. 환불을 이용해 주세요."
                )
            if state in FINAL:
                raise self._state_conflict(run, "이미 끝난 주문입니다.")
            return
        if rtype == ReplyType.REQUOTE_FROM_CART:  # 승인 화면, 결제 시작 전에만 (이슈 #86)
            if state != RunState.AWAITING_APPROVAL or (run.context or {}).get("execution_id"):
                raise self._state_conflict(run, "요기요 금액 반영은 결제 승인 전에만 할 수 있습니다.")
            return
        if rtype == ReplyType.RECOMMEND_AGAIN:  # 후보 화면에서만 (이슈 #68)
            if state != RunState.CANDIDATES_READY:
                raise self._state_conflict(run, "다른 가게 추천은 후보 화면에서만 받을 수 있습니다.")
            return
        if WAITING.get(state) != rtype:
            raise self._state_conflict(run, f"지금은 {rtype} 응답을 받을 수 없습니다.")

    @staticmethod
    def _state_conflict(run: Run, message: str) -> AppError:
        return AppError(
            409,
            "INVALID_STATE_FOR_REPLY",
            message,
            {"state": run.state, "state_version": run.state_version},
        )

    def _pending_sync(self, run: Run) -> bool:
        return (
            self.session.scalars(
                select(RunEvent.event_id).where(
                    RunEvent.request_id == run.request_id, RunEvent.sync_b08 == SyncStatus.PENDING
                )
            ).first()
            is not None
        )

    def _policy(self, run: Run) -> dict:
        return {
            "policy_id": run.context["policy_id"],
            "policy_version": run.context["policy_version"],
        }

    @staticmethod
    def _is_over_budget(cand: dict) -> bool:
        """A가 이 후보를 예산 초과(BUDGET_EXCEEDED)로 표시했는가.

        위반은 코드 문자열이거나 {code, ...}이다.
        """
        return any(
            (v if isinstance(v, str) else v.get("code")) == "BUDGET_EXCEEDED"
            for v in cand.get("violations", [])
        )

    def _relax_budget_if_consented(self, run: Run, quote: dict) -> None:
        """예산 초과 후보임을 알고 계속하겠다고 한 주문의 D 예산 한도를 이번 금액까지 올린다.

        D의 검사는 그대로 두고 D-01 재등록(정상 경로)으로 이 주문의 한도만 바꾼다.
        사용자의 원래 위임 설정(run.delegation)과 A의 조건은 건드리지 않는다.
        아래 조건을 모두 만족할 때만 올리고, 아니면 아무것도 하지 않아
        D가 평소처럼 BUDGET_EXCEEDED로 막는다.
        - 동의(allow_over_budget)가 있었고, 선택한 후보를 A가 예산 초과로 표시했다
        - 최종 결제금액이 후보 화면에서 사용자가 본 금액(estimated_total_krw)을 넘지 않는다
        """
        consent = run.context.get("over_budget")
        if not consent:
            return
        constraints = copy.deepcopy(run.context["constraints"])
        delegation = copy.deepcopy(run.delegation)
        limits = [
            v
            for v in (constraints.get("budget_krw"), delegation.get("max_charge_krw"))
            if v is not None
        ]
        cap = min(limits) if limits else None
        charge = quote["charge_krw"]
        if cap is None or charge <= cap or charge > consent["max_total_krw"]:
            return
        if constraints.get("budget_krw") is not None:
            constraints["budget_krw"] = max(constraints["budget_krw"], charge)
        if delegation.get("max_charge_krw") is not None:
            delegation["max_charge_krw"] = max(delegation["max_charge_krw"], charge)
        rid = run.request_id
        policy = self._call(
            run,
            "D",
            "D-01",
            lambda: self.d.register_policy(
                PolicyCreate.model_validate(
                    {
                        "request_id": rid,
                        "user_id": run.user_id,
                        "original_message": run.message,
                        "constraints_confirmed": True,
                        "constraints": constraints,
                        "delegation": delegation,
                    }
                )
            ),
        )
        self._set_ctx(
            run,
            policy_id=policy["policy_id"],
            policy_version=policy["policy_version"],
            over_budget={**consent, "limit_krw": cap, "charge_krw": charge},
        )

    @staticmethod
    def _dep_error(exc: DependencyFailed) -> dict:
        return {"code": FailCode.DEPENDENCY_UNAVAILABLE, "service": exc.service, "api": exc.api}

    @staticmethod
    def _ms(t0: float) -> int:
        return int((time.perf_counter() - t0) * 1000)

    # ---------------------------------------------------------------- 멱등 키
    def _idem_get(self, user_id: str, scope: str, key: str | None, payload: dict) -> dict | None:
        if not key:
            return None
        row = self.session.get(IdempotencyRecord, (user_id, f"{scope}:{key}"))
        if row is None:
            return None
        if row.request_hash != _hash(payload):
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "같은 Idempotency-Key에 다른 내용입니다.")
        return row.response

    def _idem_put(
        self,
        user_id: str,
        scope: str,
        key: str | None,
        payload: dict,
        status_code: int,
        response: dict,
    ) -> None:
        if not key:
            return
        self.session.add(
            IdempotencyRecord(
                user_id=user_id,
                idempotency_key=f"{scope}:{key}",
                request_hash=_hash(payload),
                status_code=status_code,
                response=response,
                created_at=self.now(),
            )
        )

    # ================================================================ 화면에 보낼 내용
    # ---------------------------------------------------------------- 요기요 실시간 확인
    def _start_live_check(self, run: Run, *, fresh_only: bool = False) -> None:
        """후보 확인을 시작한다. 주문이 저장된 뒤 작업 스레드에 맡긴다 (후보 화면은 안 기다림)."""
        started = self.now()
        self._set_ctx(run, live_check={"started_at": started.isoformat(), "fresh_only": fresh_only})
        rid, cands, message = run.request_id, list(run.context["candidates"]), run.message
        self._after_commit.append(
            lambda: self.live.start(rid, cands, message, started, fresh_only=fresh_only)
        )

    def _live_fresh_only(self, run: Run) -> bool:
        return bool((run.context or {}).get("live_check", {}).get("fresh_only"))

    def _live_started_at(self, run: Run) -> datetime | None:
        raw = (run.context or {}).get("live_check", {}).get("started_at")
        return datetime.fromisoformat(raw) if raw else None

    def _check_live(self, run: Run, cand: dict) -> None:
        """고른 후보가 요기요에서 지금 주문 불가로 확인됐으면 거절한다 (409 CANDIDATE_UNAVAILABLE).

        확인 중이면 그 식당 확인이 끝날 때까지 잠깐 기다린다. 그래도 확인 중이면 409로 막는다 (이슈 #85:
        화면은 확인이 끝나야 버튼을 켜고, 서버도 같은 규칙). 확인하지 못한 후보(오류·차단·건너뜀)는 막지 않는다.
        """
        if not self.live:
            return
        v = self.live.verdict(
            run.request_id,
            cand,
            self._live_started_at(run),
            fresh_only=self._live_fresh_only(run),
        )
        self._event(
            run,
            RunEventType.LIVE_CHECKED,
            run.state,
            run.state,
            service="LIVE",
            detail={
                "candidate_id": cand["candidate_id"],
                "restaurant_id": v["restaurant_id"],
                "status": v["status"],
                "changes": v["changes"],
                "reason": v["reason"],
                "checked_at": v["checked_at"],
            },
        )
        if v["status"] == "CHECKING":
            raise AppError(
                409,
                "LIVE_CHECK_IN_PROGRESS",
                "요기요에서 가게를 확인하고 있어요. 확인이 끝나면 골라 주세요.",
                {"candidate_index": cand["index"]},
            )
        if v["status"] == "UNAVAILABLE":
            raise AppError(
                409,
                "CANDIDATE_UNAVAILABLE",
                "방금 확인해 보니 지금 주문할 수 없는 가게예요. 다른 후보를 골라 주세요.",
                {"candidate_index": cand["index"], "reason": v["reason"]},
            )

    def record_candidate_views(self, user_id: str, request_id: str, shown: list[dict]) -> dict:
        """(로컬 확장, 이슈 #48) 후보 화면이 실제로 보여 준 후보를 남긴다.

        실시간 확인으로 숨거나 '더 보기'로 접힌 후보가 있어 '후보 포함'과 '화면 표시'가 다르다.
        후보 선택 단계에서만 남기고, 직전에 남긴 것과 같으면 다시 남기지 않는다 (화면이 여러 번 보내도 한 번).
        주문 행은 바꾸지 않는다 (같은 순간의 후보 선택과 부딪히지 않게).
        """
        run = self._load(request_id, user_id)
        if run.state != RunState.CANDIDATES_READY:
            return {"recorded": False, "reason": "NOT_SELECTING"}
        by_index = {c["index"]: c for c in (run.context or {}).get("candidates", [])}
        items = []
        for position, s in enumerate(shown, start=1):
            cand = by_index.get(s["candidate_index"])
            if cand is None:
                continue
            items.append(
                {
                    "candidate_id": cand.get("candidate_id"),
                    "restaurant_id": cand["restaurant"].get("restaurant_id"),
                    "position": position,
                    "collapsed": bool(s.get("collapsed")),
                    "live_status": s.get("live_status"),
                }
            )
        if not items:
            return {"recorded": False, "reason": "NO_CANDIDATE"}
        last = self.session.scalars(
            select(CandidateView)
            .where(CandidateView.request_id == request_id)
            .order_by(CandidateView.id.desc())
            .limit(1)
        ).first()
        if last is not None and last.shown == items:
            return {"recorded": False, "reason": "SAME_AS_BEFORE"}
        self.session.add(CandidateView(request_id=request_id, shown=items, created_at=self.now()))
        self.session.commit()
        return {"recorded": True, "count": len(items)}

    def live_check_view(self, user_id: str, request_id: str) -> dict:
        """O-15. 후보별 요기요 실시간 확인 결과 (화면이 진행 중에 폴링)."""
        run = self._load(request_id, user_id)
        if not self.live:
            return {"mode": "off", "state": "OFF", "total": 0, "done": 0, "results": []}
        cands = self._remaining_candidates(run) if run.context else []
        return self.live.view(
            run.request_id, cands, self._live_started_at(run), self._live_fresh_only(run)
        )

    def live_recheck(self, user_id: str, request_id: str) -> dict:
        """O-16. 후보를 다시 확인한다 (real 모드 캐시 무시). 후보 선택 단계에서만."""
        run = self._load(request_id, user_id, lock=True)
        if not self.live:
            raise AppError(503, "LIVE_CHECK_DISABLED", "요기요 실시간 확인이 꺼져 있습니다.")
        if run.state != RunState.CANDIDATES_READY:
            raise AppError(
                409, "INVALID_STATE_FOR_REPLY", "후보를 고르는 단계에서만 다시 확인할 수 있어요."
            )
        self._start_live_check(run, fresh_only=True)  # 다시 확인: 최근 확인(캐시)을 쓰지 않음
        self._commit()
        return self.live.view(
            run.request_id,
            self._remaining_candidates(run),
            self._live_started_at(run),
            fresh_only=True,
        )

    def _remaining_candidates(self, run: Run) -> list[dict]:
        tried = set(run.context.get("tried_candidate_ids", []))
        return [c for c in run.context.get("candidates", []) if c["candidate_id"] not in tried]

    def _candidate_at(self, run: Run, index: int) -> dict:
        for c in self._remaining_candidates(run):
            if c["index"] == index:
                return c
        raise AppError(
            400,
            "VALIDATION_ERROR",
            "선택할 수 없는 후보입니다.",
            {"fields": [{"loc": "body.candidate_index", "reason": "not_available"}]},
        )

    def _mark_min_order(self, run: Run, cands: list[dict], constraints: dict) -> list[dict]:
        """최소주문금액이 모자라 맡긴 결제수단을 쓸 수 없는 후보에 min_order를 붙인다 (로컬 확장).

        B-05는 이런 수단을 거절해 주문이 BLOCKED되므로, 후보 목록에서 미리 막는다.
        확인하지 못하면(C가 이 확인을 안 하거나 장애) 표시하지 않고 기존 흐름대로 간다.
        """
        check = getattr(self.c, "check_min_order", None)
        if check is None or not cands:
            return cands
        body = {
            "user_id": run.user_id,
            "request_id": run.request_id,
            "orders": [
                {
                    "restaurant_id": c["restaurant"]["restaurant_id"],
                    "order_krw": c["estimated_total_krw"],
                }
                for c in cands
            ],
            "candidate_payment_method_ids": run.delegation.get("allowed_payment_method_ids") or [],
            "local_currency_only": bool(
                (constraints.get("payment_requirements") or {}).get("local_currency_only")
            ),
        }
        try:
            res = self._call(run, "C", "C-MIN-ORDER", lambda: check(body))
        except AppError, DependencyFailed:
            return cands
        marked = []
        for c, r in zip(cands, res.get("results", []), strict=False):
            required = r.get("required_order_krw")
            if required:
                c = {**c, "min_order": {"required_krw": required, "order_krw": r["order_krw"]}}
            marked.append(c)
        return marked if len(marked) == len(cands) else cands

    def _candidate_prompt(self, run: Run) -> dict:
        return {
            "type": "SELECT_CANDIDATE",
            "search_notice": run.context.get("search_notice"),
            "candidates": [
                {
                    "index": c["index"],
                    "restaurant_name": c["restaurant"]["name"],
                    "summary": ", ".join(f"{i['name']} {i['quantity']}" for i in c["items"]),
                    "total_krw": c["estimated_total_krw"],
                    "reasons": c.get("reasons", []),
                    "meets_all_constraints": c.get("meets_all_constraints", True)
                    and not c.get("min_order"),
                    # 최소주문금액 미달이면 선택할 수 없다 (로컬 확장)
                    "orderable": not c.get("min_order"),
                    "min_order": c.get("min_order"),
                    # 비슷한 메뉴로 고른 후보 (이슈 #37): {"level", "label"}
                    "match": c.get("match"),
                    # 조건에 맞는 가게가 모자라 채운 근접 후보 (이슈 #37): 화면은 '더 보기'로 접는다
                    "near_miss_fill": bool(c.get("near_miss_fill")),
                    # 화면(와이어프레임 v0.3 03)이 보여 주는 값. A 후보에 있는 것만 그대로 전달한다
                    "restaurant": {
                        k: c["restaurant"].get(k)
                        for k in (
                            "rating",
                            "delivery_minutes",
                            "delivery_fee_krw",
                            "local_currency_merchant",
                            "local_currency_basis",
                            "cuisine",
                        )
                    },
                    "items": [
                        {
                            "name": i.get("name"),
                            "quantity": i["quantity"],
                            "unit_price_krw": i.get("unit_price_krw"),
                        }
                        for i in c["items"]
                    ],
                    "violations": [
                        # A가 코드 문자열만 줄 때도 화면 모양({code, message})으로 맞춘다
                        {"code": v, "message": None}
                        if isinstance(v, str)
                        else {"code": v.get("code"), "message": v.get("message")}
                        for v in c.get("violations", [])
                    ]
                    + (
                        [{"code": "MIN_ORDER_NOT_MET", "message": MIN_ORDER_MESSAGE}]
                        if c.get("min_order")
                        else []
                    ),
                }
                for c in self._remaining_candidates(run)
            ],
        }

    def _method_info(self, run: Run) -> dict[str, dict]:
        """결제수단 id → {name, type} (C-02). 못 가져와도 주문은 계속한다(이름 없이)."""
        cached = run.context.get("method_info")
        if cached is not None:
            return cached
        try:
            rows = self.c.list_methods(run.user_id).get("results", [])
            info = {
                m["payment_method_id"]: {"name": m.get("name"), "type": m.get("type")} for m in rows
            }
        except Exception:  # noqa: BLE001 - 이름은 화면 표시용일 뿐
            info = {}
        self._set_ctx(run, method_info=info)
        return info

    def _payment_prompt(self, run: Run) -> dict:
        rec = run.context["recommendation"]
        info = self._method_info(run)
        quote = run.context["quote"]
        total = quote["menu_subtotal_krw"] + quote["delivery_fee_krw"]
        return {
            "type": "SELECT_PAYMENT_METHOD",
            "options": [
                {
                    **{
                        k: o.get(k)
                        for k in (
                            "rank",
                            "payment_method_id",
                            "instant_discount_krw",
                            "charge_krw",
                            "reasons",
                            *(("split",) if o.get("split") else ()),
                        )
                    },
                    "name": info.get(o["payment_method_id"], {}).get("name")
                    or o["payment_method_id"],
                    "method_type": info.get(o["payment_method_id"], {}).get("type"),
                }
                for o in rec["options"]
            ],
            "excluded": [
                {
                    "payment_method_id": e.get("payment_method_id"),
                    "name": info.get(e.get("payment_method_id"), {}).get("name"),
                    "type": info.get(e.get("payment_method_id"), {}).get("type"),
                    "reason_codes": e.get("reason_codes") or [],
                }
                for e in rec.get("excluded", [])
            ],
            "order": {
                "restaurant_name": run.context["selected_candidate"]["restaurant"]["name"],
                "items": self._item_views(run.context["selected_candidate"]["items"]),
                "menu_subtotal_krw": quote["menu_subtotal_krw"],
                "delivery_fee_krw": quote["delivery_fee_krw"],
                "total_krw": total,
            },
            "expires_at": rec.get("expires_at"),
        }

    @staticmethod
    def _item_views(items: list[dict]) -> list[dict]:
        return [
            {
                "name": i.get("name") or i.get("menu_id"),
                "quantity": i["quantity"],
                "unit_price_krw": i.get("unit_price_krw"),
            }
            for i in items
        ]

    def _order_view(self, run: Run) -> dict | None:
        """승인·완료 화면이 보여 줄 주문 요약 (사용자가 승인한 B 견적 기준)."""
        ctx = run.context
        q, cand = ctx.get("target_quote"), ctx.get("selected_candidate")
        if not q or not cand:
            return None
        info = self._method_info(run)
        pm = q["payment_method_id"]
        return {
            "restaurant_name": cand["restaurant"]["name"],
            "delivery_minutes": cand["restaurant"].get("delivery_minutes"),
            "items": self._item_views(q["items"]),
            "menu_subtotal_krw": q["menu_subtotal_krw"],
            "delivery_fee_krw": q["delivery_fee_krw"],
            "instant_discount_krw": q.get("instant_discount_krw", 0),
            "charge_krw": q["charge_krw"],
            "payment_method": {
                "payment_method_id": pm,
                "name": info.get(pm, {}).get("name") or pm,
                "type": info.get(pm, {}).get("type"),
            },
        }

    def _approval_prompt(self, run: Run) -> dict:
        ctx = run.context
        q = ctx["target_quote"]
        items = ", ".join(f"{i.get('name') or i['menu_id']} {i['quantity']}" for i in q["items"])
        summary = (
            f"{ctx['selected_candidate']['restaurant']['name']} · {items} · {q['charge_krw']:,}원"
        )
        if q.get("instant_discount_krw"):
            summary += f" ({q['payment_method_id']} 즉시할인 {q['instant_discount_krw']:,}원)"
        split = None
        if q.get("local_currency_payment_method_id"):  # 분할결제(로컬 확장)
            local_charge = q["local_currency_charge_krw"]
            info = self._method_info(run)
            local_id, card_id = q["local_currency_payment_method_id"], q["payment_method_id"]
            split = {
                "local_currency": {
                    "payment_method_id": local_id,
                    "name": info.get(local_id, {}).get("name"),
                    "charge_krw": local_charge,
                },
                "remainder": {
                    "payment_method_id": card_id,
                    "name": info.get(card_id, {}).get("name"),
                    "charge_krw": q["charge_krw"] - local_charge,
                },
            }
            summary += f" · 지역화폐 {local_charge:,}원 + 카드 {q['charge_krw'] - local_charge:,}원"
        checks = []
        budget = ctx["constraints"].get("budget_krw")
        limit = run.delegation.get("max_charge_krw")
        cap = min(v for v in (budget, limit) if v) if (budget or limit) else None
        ob = ctx.get("over_budget") or {}
        over_budget = None
        if ob.get("charge_krw"):  # 사용자가 확인하고 예산을 넘겨 진행하는 주문
            over_budget = {
                "limit_krw": ob["limit_krw"],
                "charge_krw": ob["charge_krw"],
                "over_krw": ob["charge_krw"] - ob["limit_krw"],
            }
            over = over_budget["over_krw"]
            checks.append(f"예산 {ob['limit_krw']:,}원보다 {over:,}원 많음 (사용자 확인)")
        elif cap:
            checks.append(f"예산 {cap:,}원 이내")
        if run.delegation.get("allowed_payment_method_ids"):
            checks.append("허용 결제수단")
        return {
            "type": "APPROVAL",
            "summary": summary,
            "checks": checks,
            "unverified_conditions": q.get("unverified_conditions", []),
            "expires_at": ctx.get("approval_expires_at"),
            "order": self._order_view(run),
            **({"split": split} if split else {}),
            **({"over_budget": over_budget} if over_budget else {}),
            # 요기요 장바구니 금액으로 다시 계산했으면 이전 견적과 바뀐 항목 (이슈 #86)
            **({"requote": ctx["requote"]} if ctx.get("requote") else {}),
        }

    @staticmethod
    def _view(run: Run) -> dict:
        current, label = STEPS[RunState(run.state)]
        view = {
            "request_id": run.request_id,
            "state": run.state,
            "state_version": run.state_version,
            "step": {"current": current, "total": TOTAL_STEPS, "label": label},
            "prompt": run.prompt,
            "result": run.result,
            "blocked": run.blocked,
            "error": run.error,
        }
        if run.notice:
            view["notice"] = run.notice
        return view
