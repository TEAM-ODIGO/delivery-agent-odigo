"""D 모듈의 유일한 입구.

- 오케스트레이터(O)는 같은 서버 안에서 이 클래스의 메서드를 직접 부른다 (HTTP 아님).
- router.py는 이 메서드를 HTTP로 감싸기만 한다 (E가 부르는 D-06, 테스트용 API).
- 판단(규칙)은 rules.py, 기록은 audit.py, 저장은 models.py. 여기서는 순서를 조립하고
  '상태 변경 + 감사 로그'를 한 트랜잭션으로 묶는 일만 한다.
"""

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import AppError
from app.modules.delegation import audit, rules
from app.modules.delegation.benefits import BenefitPort, BenefitUnavailable
from app.modules.delegation.constants import (
    ACTIVE_APPROVAL_STATUSES,
    FINAL_PAYMENT_STATUSES,
    Actor,
    ApprovalStatus,
    EventType,
    PaymentStatus,
    PolicyStatus,
    Reason,
    RevokeReason,
)
from app.modules.delegation.hashing import proposal_hash
from app.modules.delegation.models import Approval, AuditLog, Execution, Policy
from app.modules.delegation.schemas import (
    ApprovalOut,
    AuditLogOut,
    AuditLogPage,
    AuthorizationCheckRequest,
    AuthorizationCheckResult,
    ConfirmOut,
    ConfirmRequest,
    Constraints,
    Delegation,
    OrderValidationRequest,
    OrderValidationResult,
    PaymentResultOut,
    PaymentResultRequest,
    PolicyCreate,
    PolicyOut,
    RevokeOut,
    RevokeRequest,
)


class DelegationService:
    def __init__(
        self,
        session: Session,
        benefits: BenefitPort,
        *,
        now: Callable[[], datetime] = clock.now,
        approval_ttl_minutes: int = 10,
    ):
        self.session = session
        self.benefits = benefits
        self.now = now
        self.ttl = timedelta(minutes=approval_ttl_minutes)

    # ------------------------------------------------------------------ 공통 도구
    @contextmanager
    def _tx(self) -> Iterator[None]:
        """블록 안의 변경을 한 번에 저장한다. 중간에 예외가 나면 전부 취소(rollback)."""
        try:
            yield
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def _expire_if_due(self, approval: Approval, now: datetime) -> None:
        """만료는 따로 돌리는 작업 없이, 읽는 순간 시각을 비교해 판정한다(lazy expiry)."""
        if approval.status in ACTIVE_APPROVAL_STATUSES and now >= approval.expires_at:
            approval.status = ApprovalStatus.EXPIRED

    def _revoke(self, approval: Approval, reason: RevokeReason, now: datetime) -> None:
        approval.status = ApprovalStatus.REVOKED
        approval.revoke_reason = reason
        audit.record(
            self.session,
            now=now,
            event_type=EventType.APPROVAL_REVOKED,
            actor=Actor.ORCHESTRATOR,
            result="OK",
            request_id=approval.request_id,
            approval_id=approval.approval_id,
            details={"revoke_reason": reason},
        )

    def _active_approvals(self, request_id: str, now: datetime) -> list[Approval]:
        rows = self.session.scalars(
            select(Approval).where(
                Approval.request_id == request_id,
                Approval.status.in_(ACTIVE_APPROVAL_STATUSES),
            )
        ).all()
        for row in rows:
            self._expire_if_due(row, now)
        return [r for r in rows if r.status in ACTIVE_APPROVAL_STATUSES]

    @staticmethod
    def _policy_view(p: Policy) -> rules.PolicyView:
        return rules.PolicyView(
            policy_id=p.policy_id,
            version=p.version,
            is_active=p.status == PolicyStatus.ACTIVE,
            constraints_confirmed=p.constraints_confirmed,
            constraints=Constraints.model_validate(p.constraints),
            delegation=Delegation.model_validate(p.delegation),
        )

    def _get_approval(self, approval_id: str, *, lock: bool = False) -> Approval | None:
        stmt = select(Approval).where(Approval.approval_id == approval_id)
        if lock:
            stmt = stmt.with_for_update()  # PostgreSQL: 같은 승인을 동시에 처리하지 못하게 잠금
        return self.session.scalars(stmt).first()

    # ------------------------------------------------------------------ D-01
    def register_policy(self, data: PolicyCreate) -> PolicyOut:
        now = self.now()
        with self._tx():
            existing = self.session.scalars(
                select(Policy).where(Policy.request_id == data.request_id)
            ).all()
            if any(p.user_id != data.user_id for p in existing):
                raise AppError(
                    403, "FORBIDDEN_USER", "다른 사용자의 요청에 조건을 등록할 수 없습니다."
                )

            for p in existing:
                if p.status == PolicyStatus.ACTIVE:
                    p.status = PolicyStatus.SUPERSEDED

            # 조건이 바뀌면, 옛 조건으로 받은 승인은 더 이상 믿을 수 없다
            revoked = self._active_approvals(data.request_id, now)
            for a in revoked:
                self._revoke(a, RevokeReason.POLICY_CHANGED, now)

            policy = Policy(
                policy_id=str(uuid.uuid4()),
                request_id=data.request_id,
                user_id=data.user_id,
                version=max((p.version for p in existing), default=0) + 1,
                status=PolicyStatus.ACTIVE,
                original_message=data.original_message,
                constraints_confirmed=data.constraints_confirmed,
                constraints=data.constraints.model_dump(mode="json"),
                delegation=data.delegation.model_dump(mode="json"),
                created_at=now,
            )
            self.session.add(policy)
            audit.record(
                self.session,
                now=now,
                event_type=EventType.POLICY_REGISTERED,
                actor=Actor.ORCHESTRATOR,
                result="OK",
                request_id=data.request_id,
                details={
                    "policy_id": policy.policy_id,
                    "policy_version": policy.version,
                    "revoked_approval_ids": [a.approval_id for a in revoked],
                },
            )
        return PolicyOut(
            policy_id=policy.policy_id,
            request_id=policy.request_id,
            policy_version=policy.version,
            status=policy.status,
            revoked_approval_ids=[a.approval_id for a in revoked],
        )

    # ------------------------------------------------------------------ D-02
    def validate_order(self, req: OrderValidationRequest) -> OrderValidationResult:
        now = self.now()
        policy = self.session.get(Policy, req.policy_id)
        if policy is None:
            raise AppError(404, "POLICY_NOT_FOUND", "사용자 조건을 찾을 수 없습니다.")
        if policy.user_id != req.user_id or policy.request_id != req.request_id:
            raise AppError(403, "FORBIDDEN_USER", "다른 사용자의 조건으로 검증할 수 없습니다.")

        # C에게 할인·결제수단 유형을 다시 묻는다. 대답을 못 받으면 None → 규칙에서 차단
        try:
            expected_discount = self.benefits.expected_instant_discount(
                req.quote, request_id=req.request_id, user_id=req.user_id
            )
            method_type = self.benefits.payment_method_type(
                req.user_id, req.quote.payment_method_id
            )
        except BenefitUnavailable:
            expected_discount, method_type = None, None

        expected_local_charge, local_method_type = None, None
        if req.quote.local_currency_payment_method_id is not None:  # 분할결제(로컬 확장)
            try:
                expected_local_charge = self.benefits.expected_local_charge(
                    req.quote, request_id=req.request_id, user_id=req.user_id
                )
                local_method_type = self.benefits.payment_method_type(
                    req.user_id, req.quote.local_currency_payment_method_id
                )
            except BenefitUnavailable:
                expected_local_charge, local_method_type = None, None

        result = rules.check_order(
            self._policy_view(policy),
            req,
            now=now,
            expected_discount=expected_discount,
            payment_method_type=method_type,
            expected_local_charge=expected_local_charge,
            local_method_type=local_method_type,
        )
        phash = proposal_hash(req)
        log_details = {
            "policy_version": req.policy_version,
            "approval_target_id": req.approval_target_id,
            "order_version": req.order_version,
            "quote_id": req.quote.quote_id,
            "charge_krw": req.quote.charge_krw,
            "proposal_hash": phash,
            **result.details,
        }

        with self._tx():
            active = self._active_approvals(req.request_id, now)

            # 같은 주문안을 다시 검증 → 기존 승인 대기를 그대로 돌려준다 (멱등)
            same = next((a for a in active if a.proposal_hash == phash), None)
            if same is not None and result.ok:
                audit.record(
                    self.session,
                    now=now,
                    event_type=EventType.ORDER_VALIDATED,
                    actor=Actor.ORCHESTRATOR,
                    result="PASSED",
                    request_id=req.request_id,
                    approval_id=same.approval_id,
                    details={**log_details, "idempotent": True},
                )
                return self._validation_ok(same)

            # 다른 주문안이 들어왔다 → 이전 승인은 모두 철회 (통과 여부와 무관, fail-closed)
            for a in active:
                if a.proposal_hash != phash:
                    self._revoke(a, RevokeReason.QUOTE_CHANGED, now)

            if not result.ok:
                audit.record(
                    self.session,
                    now=now,
                    event_type=EventType.ORDER_VALIDATED,
                    actor=Actor.ORCHESTRATOR,
                    result="FAILED",
                    request_id=req.request_id,
                    reason_codes=result.reasons,
                    details=log_details,
                )
                return OrderValidationResult(
                    valid=False, reason_codes=list(result.reasons), details=result.details
                )

            approval = Approval(
                approval_id=str(uuid.uuid4()),
                request_id=req.request_id,
                user_id=req.user_id,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                approval_target_id=req.approval_target_id,
                order_version=req.order_version,
                quote_id=req.quote.quote_id,
                proposal_hash=phash,
                proposal_snapshot=req.model_dump(mode="json"),
                charge_krw=req.quote.charge_krw,
                payment_method_id=req.quote.payment_method_id,
                status=ApprovalStatus.PENDING,
                expires_at=min(req.quote.expires_at, now + self.ttl),
                created_at=now,
            )
            self.session.add(approval)
            audit.record(
                self.session,
                now=now,
                event_type=EventType.ORDER_VALIDATED,
                actor=Actor.ORCHESTRATOR,
                result="PASSED",
                request_id=req.request_id,
                approval_id=approval.approval_id,
                details=log_details,
            )
        return self._validation_ok(approval)

    @staticmethod
    def _validation_ok(a: Approval) -> OrderValidationResult:
        return OrderValidationResult(
            valid=True,
            reason_codes=[],
            approval_id=a.approval_id,
            approval_status=ApprovalStatus(a.status),
            proposal_hash=a.proposal_hash,
            expires_at=a.expires_at,
        )

    # ------------------------------------------------------------------ D-03
    def confirm(self, approval_id: str, body: ConfirmRequest) -> ConfirmOut:
        now = self.now()
        error: AppError | None = None
        with self._tx():
            a = self._get_approval(approval_id, lock=True)
            if a is None:
                raise AppError(404, "APPROVAL_NOT_FOUND", "승인 정보를 찾을 수 없습니다.")
            if a.user_id != body.user_id:
                raise AppError(403, "FORBIDDEN_USER", "본인의 주문만 승인할 수 있습니다.")
            self._expire_if_due(a, now)

            # 이미 같은 내용으로 승인했으면 같은 결과를 돌려준다 (버튼 두 번 클릭 등)
            if a.status == ApprovalStatus.APPROVED and a.proposal_hash == body.proposal_hash:
                return self._confirm_out(a)

            policy = self.session.get(Policy, a.policy_id)
            if a.status == ApprovalStatus.EXPIRED:
                error = AppError(
                    409,
                    "APPROVAL_EXPIRED",
                    "승인 유효시간이 지났습니다.",
                    {"expires_at": a.expires_at.isoformat()},
                )
            elif a.status == ApprovalStatus.REVOKED:
                error = AppError(
                    409,
                    "APPROVAL_REVOKED",
                    "철회된 승인입니다.",
                    {"revoke_reason": a.revoke_reason},
                )
            elif a.status != ApprovalStatus.PENDING:
                error = AppError(
                    409, "APPROVAL_NOT_PENDING", "승인 대기 상태가 아닙니다.", {"status": a.status}
                )
            elif a.proposal_hash != body.proposal_hash:
                error = AppError(
                    409, "PROPOSAL_HASH_MISMATCH", "화면의 주문안과 승인 대상이 다릅니다."
                )
            elif policy is None or policy.status != PolicyStatus.ACTIVE:
                error = AppError(409, "POLICY_CHANGED", "조건이 바뀌어 다시 검증해야 합니다.")

            if error is None:
                a.status = ApprovalStatus.APPROVED
                a.approved_at = now
                a.execution_id = "EXE-" + uuid.uuid4().hex[:16]

            audit.record(
                self.session,
                now=now,
                event_type=EventType.APPROVAL_CONFIRMED,
                actor=Actor.ORCHESTRATOR,
                result="REJECTED" if error else "OK",
                request_id=a.request_id,
                approval_id=a.approval_id,
                execution_id=a.execution_id,
                reason_codes=[error.code] if error else [],
                details={"status": a.status, "expires_at": a.expires_at.isoformat()},
            )
        # 거절도 기록은 남겨야 하므로, commit이 끝난 뒤에 에러를 던진다
        if error:
            raise error
        return self._confirm_out(a)

    @staticmethod
    def _confirm_out(a: Approval) -> ConfirmOut:
        return ConfirmOut(
            approval_id=a.approval_id,
            status=ApprovalStatus(a.status),
            approved_at=a.approved_at,
            execution_id=a.execution_id,
            expires_at=a.expires_at,
        )

    # ------------------------------------------------------------------ D-04
    def get_approval(self, approval_id: str) -> ApprovalOut:
        now = self.now()
        with self._tx():
            a = self._get_approval(approval_id)
            if a is None:
                raise AppError(404, "APPROVAL_NOT_FOUND", "승인 정보를 찾을 수 없습니다.")
            self._expire_if_due(a, now)
        return ApprovalOut(
            approval_id=a.approval_id,
            request_id=a.request_id,
            approval_target_id=a.approval_target_id,
            order_version=a.order_version,
            quote_id=a.quote_id,
            status=ApprovalStatus(a.status),
            execution_id=a.execution_id,
            approved_at=a.approved_at,
            expires_at=a.expires_at,
            revoke_reason=a.revoke_reason,
        )

    def get_approval_for_execution(self, approval_id: str) -> dict:
        """B-07 내부 호출에 필요한 승인 상태와 원본 해시를 D 경계에서 제공한다."""
        out = self.get_approval(approval_id).model_dump(mode="json")
        approval = self.session.get(Approval, approval_id)
        out["proposal_hash"] = approval.proposal_hash
        return out

    # ------------------------------------------------------------------ D-05
    def revoke(self, approval_id: str, body: RevokeRequest) -> RevokeOut:
        now = self.now()
        with self._tx():
            a = self._get_approval(approval_id, lock=True)
            if a is None:
                raise AppError(404, "APPROVAL_NOT_FOUND", "승인 정보를 찾을 수 없습니다.")
            self._expire_if_due(a, now)
            if a.status == ApprovalStatus.CONSUMED:
                raise AppError(
                    409,
                    "ALREADY_EXECUTED",
                    "이미 결제가 진행된 승인은 철회할 수 없습니다. 결제 취소 절차를 이용하세요.",
                )
            if a.status in ACTIVE_APPROVAL_STATUSES:
                self._revoke(a, body.reason_code, now)
            # 이미 REVOKED·EXPIRED면 아무것도 바꾸지 않고 현재 상태를 돌려준다 (멱등)
        return RevokeOut(approval_id=a.approval_id, status=ApprovalStatus(a.status))

    # ------------------------------------------------------------------ D-06
    def check_authorization(
        self, req: AuthorizationCheckRequest, *, request_id_hint: str | None = None
    ) -> AuthorizationCheckResult:
        """E가 결제하기 직전 매번 부른다. 통과하면 그 자리에서 승인을 '사용됨'으로 바꾼다.

        동시에 두 요청이 와도 하나만 통과하도록, 충돌이 나면 처음부터 한 번 다시 판단한다.
        두 번째도 실패하면 예외 → 503 → E는 결제하지 않는다 (fail-closed).
        """
        try:
            return self._check_authorization_once(req, request_id_hint)
        except IntegrityError, OperationalError:
            return self._check_authorization_once(req, request_id_hint)

    def _check_authorization_once(
        self, req: AuthorizationCheckRequest, request_id_hint: str | None
    ) -> AuthorizationCheckResult:
        now = self.now()
        with self._tx():
            a = self._get_approval(req.approval_id, lock=True)
            policy = self.session.get(Policy, a.policy_id) if a else None
            if a:
                self._expire_if_due(a, now)

            view = (
                rules.ApprovalView(
                    approval_id=a.approval_id,
                    status=ApprovalStatus(a.status),
                    execution_id=a.execution_id,
                    quote_id=a.quote_id,
                    proposal_hash=a.proposal_hash,
                    charge_krw=a.charge_krw,
                    payment_method_id=a.payment_method_id,
                    local_currency_payment_method_id=(
                        ((a.proposal_snapshot or {}).get("quote") or {}).get(
                            "local_currency_payment_method_id"
                        )
                    ),
                    local_currency_charge_krw=(
                        ((a.proposal_snapshot or {}).get("quote") or {}).get(
                            "local_currency_charge_krw"
                        )
                    ),
                )
                if a
                else None
            )
            result = rules.check_payment(view, self._policy_view(policy) if policy else None, req)

            replay = False
            if result.ok and a.status == ApprovalStatus.APPROVED:
                # 실행 권한 선점: 'APPROVED일 때만 CONSUMED로' 조건부 변경.
                # 그 사이 다른 요청이 먼저 바꿨다면 바뀐 행이 0개 → 이미 사용됨
                changed = self.session.execute(
                    update(Approval)
                    .where(
                        Approval.approval_id == a.approval_id,
                        Approval.status == ApprovalStatus.APPROVED,
                    )
                    .values(status=ApprovalStatus.CONSUMED)
                ).rowcount
                if changed != 1:
                    # 동시에 들어온 '같은 실행' 요청이 먼저 선점했다.
                    # 승인 1건의 실행 ID는 하나뿐이고 내용 일치도 확인했으므로 재응답과 같다.
                    self.session.refresh(a)
                    if a.status == ApprovalStatus.CONSUMED and a.execution_id == req.execution_id:
                        replay = True
                    else:
                        result.fail(Reason.ALREADY_EXECUTED)
                else:
                    self.session.add(
                        Execution(
                            execution_id=req.execution_id,
                            approval_id=a.approval_id,
                            proposal_hash=a.proposal_hash,
                            charge_krw=a.charge_krw,
                            payment_method_id=a.payment_method_id,
                            claimed_at=now,
                        )
                    )
            elif result.ok:
                replay = True  # 이미 CONSUMED + 같은 execution_id + 같은 내용 → 같은 답 (멱등)

            audit.record(
                self.session,
                now=now,
                event_type=EventType.PAYMENT_CHECK,
                actor=Actor.E,
                result="ALLOWED" if result.ok else "BLOCKED",
                request_id=a.request_id if a else (request_id_hint or "UNKNOWN"),
                approval_id=req.approval_id,
                execution_id=req.execution_id,
                reason_codes=result.reasons,
                details={
                    "approved_krw": a.charge_krw if a else None,
                    "requested_krw": req.charge_krw,
                    "approved_method": a.payment_method_id if a else None,
                    "requested_method": req.payment_method_id,
                    "replay": replay,
                    **result.details,
                },
            )
        return AuthorizationCheckResult(
            execution_id=req.execution_id, allowed=result.ok, reason_codes=list(result.reasons)
        )

    # ------------------------------------------------------------------ D-08 (MVP 2)
    def record_payment_result(self, req: PaymentResultRequest) -> PaymentResultOut:
        """O가 받은 E 결제 결과를 실행 기록에 남긴다. E는 이 API를 부르지 않는다.

        - 같은 결과를 다시 받으면 그대로 성공(멱등, 로그를 또 쓰지 않음).
        - 미확정(PENDING·UNKNOWN)은 나중에 확정(APPROVED·FAILED)으로 바뀔 수 있다 (E-03·E-10 결과).
        - 이미 확정된 결과와 다른 결과, 다른 payment_id는 409 RESULT_CONFLICT.
        - 승인 금액과 결제 금액이 다르면 기록은 하되 감사 로그에 PAYMENT_AMOUNT_MISMATCH로 남긴다
          (D-06에서 금액을 확인했으므로 생기면 E 쪽 문제 → 대시보드에서 바로 보이게).
        """
        now = self.now()
        with self._tx():
            ex = self.session.get(Execution, req.execution_id, with_for_update=True)
            if ex is None:
                raise AppError(404, "EXECUTION_NOT_FOUND", "결제 실행 기록을 찾을 수 없습니다.")
            current = PaymentStatus(ex.payment_status) if ex.payment_status else None

            if current == req.status and ex.payment_id == req.payment_id:
                return PaymentResultOut(execution_id=ex.execution_id, recorded=True, status=current)
            if ex.payment_id is not None and ex.payment_id != req.payment_id:
                raise AppError(
                    409,
                    "RESULT_CONFLICT",
                    "이미 다른 결제가 기록된 실행입니다.",
                    {"recorded_status": ex.payment_status},
                )
            if current in FINAL_PAYMENT_STATUSES:
                raise AppError(
                    409,
                    "RESULT_CONFLICT",
                    "이미 다른 결과가 기록된 실행입니다.",
                    {"recorded_status": ex.payment_status},
                )

            ex.payment_id = req.payment_id
            ex.payment_status = req.status
            ex.result_charge_krw = req.charge_krw
            ex.result_recorded_at = now
            approval = self.session.get(Approval, ex.approval_id)
            mismatch = req.charge_krw != ex.charge_krw
            audit.record(
                self.session,
                now=now,
                event_type=EventType.PAYMENT_RESULT,
                actor=Actor.ORCHESTRATOR,
                result="MISMATCH" if mismatch else "RECORDED",
                request_id=approval.request_id,
                approval_id=ex.approval_id,
                execution_id=ex.execution_id,
                reason_codes=[Reason.PAYMENT_AMOUNT_MISMATCH] if mismatch else [],
                details={
                    "payment_id": req.payment_id,
                    "status": req.status,
                    "previous_status": current,
                    "approved_krw": ex.charge_krw,
                    "charged_krw": req.charge_krw,
                },
            )
        return PaymentResultOut(execution_id=ex.execution_id, recorded=True, status=req.status)

    # ------------------------------------------------------------------ D-09 (MVP 2)
    def list_audit_logs(
        self,
        *,
        request_id: str | None = None,
        approval_id: str | None = None,
        execution_id: str | None = None,
        event_type: EventType | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> AuditLogPage:
        """감사 로그를 시간순으로 조회한다. O-04(승인 대시보드)가 부른다. 조회만 한다.

        로그는 기록할 때 이미 민감정보를 가렸으므로 그대로 돌려준다.
        """
        conds = []
        if request_id:
            conds.append(AuditLog.request_id == request_id)
        if approval_id:
            conds.append(AuditLog.approval_id == approval_id)
        if execution_id:
            conds.append(AuditLog.execution_id == execution_id)
        if event_type:
            conds.append(AuditLog.event_type == str(event_type))
        total = self.session.scalar(select(func.count()).select_from(AuditLog).where(*conds))
        rows = self.session.scalars(
            select(AuditLog)
            .where(*conds)
            .order_by(AuditLog.seq)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return AuditLogPage(
            count=total or 0,
            results=[
                AuditLogOut(
                    log_id=r.log_id,
                    event_type=EventType(r.event_type),
                    request_id=r.request_id,
                    approval_id=r.approval_id,
                    execution_id=r.execution_id,
                    actor=r.actor,
                    result=r.result,
                    reason_codes=list(r.reason_codes),
                    details=dict(r.details),
                    created_at=r.created_at,
                )
                for r in rows
            ],
        )
