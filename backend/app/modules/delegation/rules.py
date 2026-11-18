"""검증 규칙 — D의 두뇌.

이 파일의 함수들은 DB도, 네트워크도, 현재 시각도 직접 건드리지 않는다.
필요한 값은 전부 인자로 받고, 결과(사유 코드 목록 + 비교값)만 돌려준다.
그래서 같은 입력이면 항상 같은 결과가 나오고, 테스트가 쉽다.

규칙은 하나에서 멈추지 않고 '걸리는 것 전부'를 모은다.
대시보드와 로그에서 무엇이 왜 막혔는지 한 번에 보이게 하기 위해서다.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.modules.delegation.constants import (
    Action,
    ApprovalStatus,
    PaymentMethodType,
    Reason,
)
from app.modules.delegation.schemas import (
    AuthorizationCheckRequest,
    Constraints,
    Delegation,
    OrderValidationRequest,
    Quote,
)


@dataclass
class RuleResult:
    reasons: list[Reason] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def fail(self, reason: Reason, **details: Any) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)
        self.details.update(details)

    @property
    def ok(self) -> bool:
        return not self.reasons


@dataclass(frozen=True)
class PolicyView:
    """검증에 필요한 조건 정보만 담은 읽기 전용 묶음 (DB 모델과 분리)."""

    policy_id: str
    version: int
    is_active: bool
    constraints_confirmed: bool
    constraints: Constraints
    delegation: Delegation


@dataclass(frozen=True)
class ApprovalView:
    """D-06 검증에 필요한 승인 정보."""

    approval_id: str
    status: ApprovalStatus  # 만료 판정까지 끝난 '현재' 상태
    execution_id: str | None
    quote_id: str
    proposal_hash: str
    charge_krw: int
    payment_method_id: str
    # 분할결제(로컬 확장): 승인한 지역화폐 몫. 일반 결제면 None
    local_currency_payment_method_id: str | None = None
    local_currency_charge_krw: int | None = None


# ---------------------------------------------------------------------------
# 금액
# ---------------------------------------------------------------------------
def recompute_subtotal(quote: Quote) -> int:
    """menu_subtotal = Σ (단가 + Σ 옵션가) × 수량"""
    return sum(
        (item.unit_price_krw + sum(o.price_krw for o in item.options)) * item.quantity
        for item in quote.items
    )


def spending_limit(constraints: Constraints, delegation: Delegation) -> int | None:
    """예산과 위임 상한 중 더 작은 값. 둘 다 없으면 상한 없음."""
    limits = [v for v in (constraints.budget_krw, delegation.max_charge_krw) if v is not None]
    return min(limits) if limits else None


# ---------------------------------------------------------------------------
# D-02 주문 검증
# ---------------------------------------------------------------------------
def check_order(
    policy: PolicyView,
    req: OrderValidationRequest,
    *,
    now: datetime,
    expected_discount: int | None,
    payment_method_type: PaymentMethodType | None,
    expected_local_charge: int | None = None,
    local_method_type: PaymentMethodType | None = None,
) -> RuleResult:
    """B가 확정한 견적이 사용자 조건과 위임 범위를 지키는지 검사한다.

    expected_discount, payment_method_type: C에게 받은 값. C 장애면 None (→ 통과 불가).
    """
    r = RuleResult()
    q = req.quote

    # 1) 조건 자체가 유효한가
    if (
        not policy.is_active
        or policy.policy_id != req.policy_id
        or policy.version != req.policy_version
    ):
        r.fail(Reason.POLICY_VERSION_MISMATCH, policy_version_expected=policy.version)
    if not policy.constraints_confirmed:
        r.fail(Reason.CONSTRAINTS_NOT_CONFIRMED)

    # 2) 주문을 만드는 일 자체를 맡겼는가
    if Action.BUILD_ORDER not in policy.delegation.allowed_actions:
        r.fail(Reason.ACTION_NOT_ALLOWED, missing_action=Action.BUILD_ORDER)

    # 3) 견적이 아직 살아 있는가
    if now >= q.expires_at:
        r.fail(Reason.QUOTE_EXPIRED, quote_expires_at=q.expires_at.isoformat())

    # 4) 확인할 수 없는 데이터가 있는가 (fail-closed)
    if q.unverified_conditions:
        r.fail(Reason.QUOTE_DATA_UNVERIFIED, unverified_conditions=q.unverified_conditions)
    if expected_discount is None or payment_method_type is None:
        r.fail(Reason.QUOTE_DATA_UNVERIFIED, benefit_check="UNAVAILABLE")

    # 5) 금액을 D가 직접 다시 계산
    subtotal = recompute_subtotal(q)
    if subtotal != q.menu_subtotal_krw:
        r.fail(
            Reason.AMOUNT_MISMATCH, subtotal_expected=subtotal, subtotal_quoted=q.menu_subtotal_krw
        )
    if expected_discount is not None and expected_discount != q.instant_discount_krw:
        r.fail(
            Reason.AMOUNT_MISMATCH,
            discount_expected=expected_discount,
            discount_quoted=q.instant_discount_krw,
        )
    charge = q.menu_subtotal_krw + q.delivery_fee_krw - q.instant_discount_krw
    if charge != q.charge_krw or charge < 0:
        r.fail(Reason.AMOUNT_MISMATCH, charge_expected=charge, charge_quoted=q.charge_krw)

    # 6) 예산 (최종 결제금액 기준)
    limit = spending_limit(policy.constraints, policy.delegation)
    if limit is not None and q.charge_krw > limit:
        r.fail(Reason.BUDGET_EXCEEDED, charge_krw=q.charge_krw, limit_krw=limit)

    # 7) 결제수단
    allowed_ids = policy.delegation.allowed_payment_method_ids
    if allowed_ids is not None and q.payment_method_id not in allowed_ids:
        r.fail(Reason.PAYMENT_METHOD_NOT_ALLOWED, payment_method_id=q.payment_method_id)
    if (
        policy.constraints.payment_requirements.local_currency_only
        and payment_method_type is not None
        and payment_method_type != PaymentMethodType.LOCAL_CURRENCY
    ):
        r.fail(Reason.PAYMENT_METHOD_NOT_ALLOWED, local_currency_required=True)

    # 7-2) 분할결제(로컬 확장): 지역화폐 몫도 같은 기준으로 확인한다
    if q.local_currency_payment_method_id is not None:
        _check_split(policy, q, r, expected_local_charge, local_method_type)

    # 8) 제외 재료 (메뉴명·설명 키워드)
    _check_ingredients(policy.constraints.excluded_ingredients, q, r)

    return r


def _check_split(
    policy: PolicyView,
    q: Quote,
    r: RuleResult,
    expected_local_charge: int | None,
    local_method_type: PaymentMethodType | None,
) -> None:
    total = q.menu_subtotal_krw + q.delivery_fee_krw
    amount, local_charge = q.local_currency_amount_krw, q.local_currency_charge_krw
    if amount is None or local_charge is None or not 0 < amount < total:
        r.fail(Reason.AMOUNT_MISMATCH, local_currency_amount_krw=amount, total_krw=total)
        return
    if local_charge > amount or local_charge > q.charge_krw:
        r.fail(Reason.AMOUNT_MISMATCH, local_currency_charge_krw=local_charge)
    if expected_local_charge is None or local_method_type is None:
        r.fail(Reason.QUOTE_DATA_UNVERIFIED, benefit_check="UNAVAILABLE")
        return
    if expected_local_charge != local_charge:
        r.fail(
            Reason.AMOUNT_MISMATCH,
            local_charge_expected=expected_local_charge,
            local_charge_quoted=local_charge,
        )
    if local_method_type != PaymentMethodType.LOCAL_CURRENCY:
        r.fail(Reason.PAYMENT_METHOD_NOT_ALLOWED, local_leg_not_local_currency=True)
    allowed_ids = policy.delegation.allowed_payment_method_ids
    if allowed_ids is not None and q.local_currency_payment_method_id not in allowed_ids:
        r.fail(
            Reason.PAYMENT_METHOD_NOT_ALLOWED, payment_method_id=q.local_currency_payment_method_id
        )


def _check_ingredients(excluded: list[str], q: Quote, r: RuleResult) -> None:
    if not excluded:
        return
    for item in q.items:
        text = f"{item.name or ''} {item.description or ''}".strip()
        if not text:
            # 무엇이 들었는지 알 수 없으면 통과시키지 않는다
            r.fail(Reason.INGREDIENT_UNKNOWN, menu_id=item.menu_id)
            continue
        hits = [word for word in excluded if word in text]
        if hits:
            r.fail(Reason.INGREDIENT_CONFLICT, menu_id=item.menu_id, matched=hits)


# ---------------------------------------------------------------------------
# D-06 결제 직전 검증
# ---------------------------------------------------------------------------
def check_payment(
    approval: ApprovalView | None,
    policy: PolicyView | None,
    req: AuthorizationCheckRequest,
) -> RuleResult:
    """E의 결제 요청이 사용자가 승인한 내용과 '정확히' 같은지 검사한다.

    상태 문제(승인 없음·철회·만료·이미 사용)가 있으면 내용 비교는 의미가 없으므로 바로 끝낸다.
    """
    r = RuleResult()

    if approval is None:
        r.fail(Reason.APPROVAL_NOT_FOUND)
        return r

    status = approval.status
    if status == ApprovalStatus.PENDING:
        r.fail(Reason.APPROVAL_NOT_CONFIRMED)
        return r
    if status == ApprovalStatus.REVOKED:
        r.fail(Reason.APPROVAL_REVOKED)
        return r
    if status == ApprovalStatus.EXPIRED:
        r.fail(Reason.APPROVAL_EXPIRED)
        return r
    if status == ApprovalStatus.CONSUMED and req.execution_id != approval.execution_id:
        r.fail(Reason.ALREADY_EXECUTED)
        return r

    # 내용 비교: 승인된 것과 하나라도 다르면 차단
    if req.execution_id != approval.execution_id:
        r.fail(Reason.EXECUTION_ID_MISMATCH)
    if req.quote_id != approval.quote_id:
        r.fail(Reason.QUOTE_ID_MISMATCH)
    if req.proposal_hash != approval.proposal_hash:
        r.fail(Reason.PROPOSAL_HASH_MISMATCH)
    if req.charge_krw != approval.charge_krw:
        r.fail(
            Reason.PAYMENT_AMOUNT_MISMATCH,
            approved_krw=approval.charge_krw,
            requested_krw=req.charge_krw,
        )
    if req.payment_method_id != approval.payment_method_id:
        r.fail(
            Reason.PAYMENT_METHOD_MISMATCH,
            approved_method=approval.payment_method_id,
            requested_method=req.payment_method_id,
        )

    # 분할결제(로컬 확장): 지역화폐 몫(수단·금액)도 승인한 것과 정확히 같아야 한다 (일반 결제는 둘 다 None)
    if req.local_currency_payment_method_id != approval.local_currency_payment_method_id:
        r.fail(
            Reason.PAYMENT_METHOD_MISMATCH,
            approved_local_method=approval.local_currency_payment_method_id,
            requested_local_method=req.local_currency_payment_method_id,
        )
    if req.local_currency_charge_krw != approval.local_currency_charge_krw:
        r.fail(
            Reason.PAYMENT_AMOUNT_MISMATCH,
            approved_local_krw=approval.local_currency_charge_krw,
            requested_local_krw=req.local_currency_charge_krw,
        )

    # 이미 사용된 승인에 같은 execution_id로 '다른 내용'을 보내면 충돌
    if status == ApprovalStatus.CONSUMED and not r.ok:
        r.reasons = [Reason.EXECUTION_CONFLICT]

    # 조건과 위임 범위가 여전히 유효한가
    if policy is None or not policy.is_active:
        r.fail(Reason.POLICY_CHANGED)
    elif Action.REQUEST_PAYMENT not in policy.delegation.allowed_actions:
        r.fail(Reason.ACTION_NOT_ALLOWED, missing_action=Action.REQUEST_PAYMENT)

    return r
