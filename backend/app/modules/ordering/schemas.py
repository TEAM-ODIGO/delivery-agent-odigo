"""B 요청 모델 (명세서 요청 예시 그대로). 응답은 views.py에서 dict로 만든다."""

from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.modules.ordering.constants import MAX_QUANTITY, BudgetBasis, PaymentStatus, Stage

# 공통 규칙: ID는 문자열, 64자 이하, 영문·숫자·_ - : . 만
Id = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-:.]+$")]
Version = Annotated[int, Field(ge=1)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemIn(Strict):
    menu_id: Id
    option_ids: list[Id] = Field(default_factory=list, max_length=20)
    quantity: Annotated[int, Field(ge=1, le=MAX_QUANTITY)]


class PaymentRequirements(BaseModel):
    model_config = ConfigDict(extra="allow")

    local_currency_only: bool = False


class ConstraintsIn(BaseModel):
    """A의 constraints를 그대로 받는다. B가 쓰는 값만 확인하고 나머지는 그대로 보관한다."""

    model_config = ConfigDict(extra="allow")

    budget_krw: Annotated[int, Field(ge=0, le=10_000_000)] | None = None
    people: Annotated[int, Field(ge=1, le=50)] | None = None
    payment_requirements: PaymentRequirements = Field(default_factory=PaymentRequirements)


# ---------- B-01 ----------
class CreateOrderPlanBody(Strict):
    request_id: Id
    candidate_id: Id
    restaurant_id: Id
    items: list[ItemIn] = Field(min_length=1, max_length=50)
    constraints_version: Version
    constraints: ConstraintsIn
    budget_basis: BudgetBasis = BudgetBasis.POST_FINANCIAL
    catalog_version: Id
    policy_id: Id


# ---------- B-05 ----------
class BindBenefitBody(Strict):
    base_order_version: Version
    quote_id: Id
    recommendation_id: Id
    payment_method_id: Id
    benefit_rule_version: Id
    # 분할결제(로컬 확장, 팀 합의 전): 둘 다 있거나 둘 다 없어야 한다.
    # payment_method_id는 나머지를 낼 카드, 아래는 지역화폐 수단과 그 수단으로 낼 금액(혜택 전)
    local_currency_payment_method_id: Id | None = None
    local_currency_amount_krw: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def _both_or_neither(self):
        if (self.local_currency_payment_method_id is None) != (self.local_currency_amount_krw is None):
            raise ValueError("local_currency_payment_method_id와 local_currency_amount_krw는 함께 보내야 합니다.")
        if self.local_currency_payment_method_id == self.payment_method_id:
            raise ValueError("지역화폐 수단과 나머지 결제수단은 달라야 합니다.")
        return self


# ---------- B-06 ----------
class CreateApprovalTargetBody(Strict):
    base_order_version: Version
    quote_id: Id
    expected_benefit_revision: Version


# ---------- B-07 ----------
class ExecutionCheckBody(Strict):
    base_order_version: Version
    quote_id: Id
    approval_target_id: Id
    approval_id: Id
    execution_id: Id
    stage: Stage = Stage.MOCK_PAYMENT


# ---------- B-08 ----------
class PaymentEventBody(Strict):
    event_id: Id
    approval_target_id: Id
    quote_id: Id
    execution_id: Id
    payment_id: Id | None = None  # UNKNOWN이면 E가 결제 ID를 못 준 경우가 있다
    status: PaymentStatus
    observed_at: AwareDatetime
    reason_code: Annotated[str, Field(max_length=64)] | None = None
