"""D 모듈의 요청·응답 형식 (Pydantic).

이 파일이 곧 API 명세다. 형식이 맞지 않는 요청은 service에 닿기 전에 400으로 거부된다.
- 문자열 길이·숫자 범위를 제한한다 (로그 폭주·주입 방지, OWASP Logging).
- 요청 최상위는 모르는 필드를 거부한다(extra="forbid"). 오타나 끼워 넣은 값이 조용히 무시되지 않게.
"""

from datetime import datetime
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.modules.delegation.constants import (
    Action,
    ApprovalStatus,
    EventType,
    PaymentStatus,
    RevokeReason,
)

# 공통 문자열 타입 ---------------------------------------------------------------
Id = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-:.]+$")]
Krw = Annotated[int, Field(ge=0, le=10_000_000)]  # 금액은 항상 원 단위 정수
ShortText = Annotated[str, Field(max_length=200)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# D-01 조건·위임 등록 -----------------------------------------------------------
class PaymentRequirements(Strict):
    local_currency_only: bool = False


class Constraints(Strict):
    """A가 확정한 조건 (A-01·A-02 응답의 constraints와 같은 모양)."""

    budget_krw: Krw | None = None
    people: Annotated[int, Field(ge=1, le=50)] | None = None
    cuisine: ShortText | None = None
    excluded_ingredients: list[Annotated[str, Field(min_length=1, max_length=30)]] = Field(
        default_factory=list, max_length=20
    )
    payment_requirements: PaymentRequirements = Field(default_factory=PaymentRequirements)
    # A가 함께 보내는 조건. D 검증에는 아직 쓰지 않지만 기록용으로 받는다.
    max_delivery_minutes: Annotated[int, Field(ge=1, le=240)] | None = None
    max_spice_level: Annotated[int, Field(ge=0, le=5)] | None = None  # 0 = 안 매움 (판정은 B)
    menu_keywords: list[Annotated[str, Field(max_length=30)]] = Field(
        default_factory=list, max_length=20
    )
    # A 확장 조건: 메뉴 이름 없이 맛만 말했을 때 ("매운 음식" → ["SPICY"]). D 검증에는 쓰지 않고 기록용
    taste_preferences: list[Annotated[str, Field(max_length=20)]] = Field(
        default_factory=list, max_length=10
    )
    # A 확장 조건 (이슈 #58): 브랜드로 말한 요청 ("맘스터치"). D 검증에는 쓰지 않고 기록용
    brands: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=10)
    # 배달 주소는 개인정보라 받기만 하고 저장·로그에서 뺀다 (exclude=True → model_dump에 안 나옴).
    delivery_address: Annotated[Any, Field(exclude=True)] = None


class Delegation(Strict):
    # 기본값: 세 가지 모두 위임. 목록에 없는 작업 이름은 Enum이라 400으로 거부된다.
    allowed_actions: list[Action] = Field(default_factory=lambda: list(Action))
    allowed_payment_method_ids: list[Id] | None = None  # None = 제한 없음
    max_charge_krw: Krw | None = None


class PolicyCreate(Strict):
    request_id: Id
    user_id: Id
    original_message: Annotated[str, Field(min_length=1, max_length=1000)]
    constraints_confirmed: bool
    constraints: Constraints = Field(default_factory=Constraints)
    delegation: Delegation = Field(default_factory=Delegation)


class PolicyOut(BaseModel):
    policy_id: str
    request_id: str
    policy_version: int
    status: str
    revoked_approval_ids: list[str]


# D-02 주문 검증 ---------------------------------------------------------------
class QuoteOption(BaseModel):
    option_id: Id
    price_krw: Krw


class QuoteItem(BaseModel):
    menu_id: Id
    name: ShortText | None = None
    description: Annotated[str, Field(max_length=500)] | None = None
    quantity: Annotated[int, Field(ge=1, le=99)]
    unit_price_krw: Krw
    options: list[QuoteOption] = Field(default_factory=list, max_length=20)


class Quote(BaseModel):
    """B ② 견적을 그대로 받는다. B가 필드를 더 넣어도 D가 모르는 필드는 버리고(ignore),
    D가 검증한 필드만 해시에 들어간다."""

    model_config = ConfigDict(extra="ignore")

    quote_id: Id
    restaurant_id: Id  # 요기요 식당 ID (수집 DB restaurant.id) — A·B·C와 같은 이름
    items: list[QuoteItem] = Field(min_length=1, max_length=50)
    menu_subtotal_krw: Krw
    delivery_fee_krw: Krw
    payment_method_id: Id
    instant_discount_krw: Krw
    charge_krw: Krw
    catalog_version: Id
    benefit_rule_version: Id
    expires_at: AwareDatetime  # 시간대 없는 시각은 거부
    unverified_conditions: list[ShortText] = Field(default_factory=list, max_length=20)
    # 분할결제(로컬 확장, 팀 합의 전): 지역화폐로 낸 몫. 일반 결제면 모두 None.
    # payment_method_id는 나머지를 낼 카드, charge_krw·instant_discount_krw는 두 몫의 합계
    local_currency_payment_method_id: Id | None = None
    local_currency_amount_krw: Krw | None = None  # 혜택 전 몫
    local_currency_charge_krw: Krw | None = None  # 혜택 후 실제 차감액


class OrderValidationRequest(Strict):
    request_id: Id
    user_id: Id
    policy_id: Id
    policy_version: Annotated[int, Field(ge=1)]
    approval_target_id: Id
    order_version: Annotated[int, Field(ge=1)]
    quote: Quote


class OrderValidationResult(BaseModel):
    valid: bool
    reason_codes: list[str]
    approval_id: str | None = None
    approval_status: ApprovalStatus | None = None
    proposal_hash: str | None = None
    expires_at: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


# D-03 승인 확정 ---------------------------------------------------------------
class ConfirmRequest(Strict):
    user_id: Id
    proposal_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class ConfirmOut(BaseModel):
    approval_id: str
    status: ApprovalStatus
    approved_at: datetime
    execution_id: str
    expires_at: datetime


# D-04 승인 조회 ---------------------------------------------------------------
class ApprovalOut(BaseModel):
    approval_id: str
    request_id: str
    approval_target_id: str
    order_version: int
    quote_id: str
    status: ApprovalStatus
    execution_id: str | None
    approved_at: datetime | None
    expires_at: datetime
    revoke_reason: str | None


# D-05 승인 철회 ---------------------------------------------------------------
class RevokeRequest(Strict):
    reason_code: RevokeReason


class RevokeOut(BaseModel):
    approval_id: str
    status: ApprovalStatus


# D-06 결제 직전 검증 -----------------------------------------------------------
class AuthorizationCheckRequest(Strict):
    execution_id: Id
    approval_id: Id
    quote_id: Id
    proposal_hash: Annotated[str, Field(max_length=80)]
    charge_krw: Krw
    payment_method_id: Id
    # 분할결제(로컬 확장): E가 결제하려는 지역화폐 몫. 승인한 내용과 다르면 차단한다
    local_currency_payment_method_id: Id | None = None
    local_currency_charge_krw: Krw | None = None


class AuthorizationCheckResult(BaseModel):
    execution_id: str
    allowed: bool
    reason_codes: list[str]


# D-08 결제 결과 기록 (MVP 2) ----------------------------------------------------
class PaymentResultRequest(Strict):
    """O가 E-01·E-03 응답을 받아 그대로 넘긴다. 이름은 E 응답과 같다."""

    execution_id: Id
    payment_id: Id
    status: PaymentStatus
    charge_krw: Krw


class PaymentResultOut(BaseModel):
    execution_id: str
    recorded: bool
    status: PaymentStatus


# D-09 감사 로그 조회 (MVP 2) ----------------------------------------------------
class AuditLogOut(BaseModel):
    log_id: str
    event_type: EventType
    request_id: str
    approval_id: str | None
    execution_id: str | None
    actor: str
    result: str
    reason_codes: list[str]
    details: dict[str, Any]
    created_at: datetime


class AuditLogPage(BaseModel):
    count: int  # 조건에 맞는 전체 건수 (이 페이지 건수가 아님)
    results: list[AuditLogOut]
