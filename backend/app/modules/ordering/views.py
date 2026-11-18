"""응답 모양 만들기 (명세서 응답 예시 그대로)."""

from datetime import datetime

from app.core.clock import KST
from app.modules.ordering.constants import BudgetBasis, BudgetStatus, PlanStatus, QuoteStatus, ValidationStatus
from app.modules.ordering.models import ApprovalTarget, BenefitBinding, OrderPlan, Quote


def iso(value: datetime | None) -> str | None:
    """공통 규칙: ISO 8601 + 시간대(+09:00)."""
    if value is None:
        return None
    return value.astimezone(KST).isoformat(timespec="seconds")


def quote_status(plan: OrderPlan, quote: Quote, now: datetime) -> str:
    if plan.status == PlanStatus.PAID:
        return QuoteStatus.USED
    if now >= quote.expires_at:
        return QuoteStatus.EXPIRED
    return QuoteStatus.ACTIVE


def quote_summary(quote: Quote) -> dict:
    return {
        "quote_id": quote.quote_id,
        "restaurant_id": quote.restaurant_id,
        "menu_subtotal_krw": quote.menu_subtotal_krw,
        "delivery_fee_krw": quote.delivery_fee_krw,
        "pre_financial_amount_krw": quote.pre_financial_amount_krw,
        "expires_at": iso(quote.expires_at),
    }


def budget_check(plan: OrderPlan, quote: Quote, binding: BenefitBinding | None) -> dict:
    limit = (plan.constraints or {}).get("budget_krw")
    if limit is None:
        return {"basis": plan.budget_basis, "limit_krw": None, "compared_krw": None, "status": BudgetStatus.NOT_REQUIRED}

    if plan.budget_basis == BudgetBasis.PRE_FINANCIAL:
        compared = quote.pre_financial_amount_krw
    elif binding is not None:
        compared = binding.charge_krw
    else:
        return {"basis": plan.budget_basis, "limit_krw": limit, "compared_krw": None, "status": BudgetStatus.PENDING_BENEFIT}

    status = BudgetStatus.PASS if compared <= limit else BudgetStatus.FAIL
    return {"basis": plan.budget_basis, "limit_krw": limit, "compared_krw": compared, "status": status}


def plan_view(plan: OrderPlan, quote: Quote, binding: BenefitBinding | None, now: datetime) -> dict:
    return {
        "order_plan_id": plan.order_plan_id,
        "order_version": plan.order_version,
        "validation_status": ValidationStatus.VALID,
        "quote_status": quote_status(plan, quote, now),
        "quote": quote_summary(quote),
        "budget_check": budget_check(plan, quote, binding),
    }


def split_fields(binding: BenefitBinding) -> dict:
    """분할결제(로컬 확장)일 때만 붙는 필드. 일반 결제는 빈 dict라 응답이 그대로다."""
    if binding.local_currency_payment_method_id is None:
        return {}
    return {
        "local_currency_payment_method_id": binding.local_currency_payment_method_id,
        "local_currency_amount_krw": binding.local_currency_amount_krw,
        "local_currency_charge_krw": binding.local_currency_charge_krw,
    }


def binding_view(binding: BenefitBinding) -> dict:
    return {
        "benefit_revision": binding.benefit_revision,
        "payment_method_id": binding.payment_method_id,
        "instant_discount_krw": binding.instant_discount_krw,
        "charge_krw": binding.charge_krw,
        "benefit_rule_version": binding.benefit_rule_version,
        **split_fields(binding),
    }


def target_view(target: ApprovalTarget) -> dict:
    return {
        "approval_target_id": target.approval_target_id,
        "request_id": target.request_id,
        "order_plan_id": target.order_plan_id,
        "order_version": target.order_version,
        "constraints_version": target.constraints_version,
        "benefit_revision": target.benefit_revision,
        "status": target.status,
        "quote": target.quote,
        "created_at": iso(target.created_at),
    }
