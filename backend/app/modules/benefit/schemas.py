from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

# 공통 규칙: 모든 ID는 문자열, 64자 이하, 영문·숫자·_ - : . 만 사용
Id = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-:.]+$")]
Krw = Annotated[int, Field(ge=0)]


class PaymentRequirements(BaseModel):
    local_currency_only: bool = False


class RecommendRequest(BaseModel):
    request_id: Id
    user_id: Id
    restaurant_id: Id
    menu_subtotal_krw: Krw
    delivery_fee_krw: Krw
    catalog_version: str | None = None
    payment_requirements: PaymentRequirements = PaymentRequirements()
    candidate_payment_method_ids: list[Id] = []     # 빈 배열 = 제한 없음 (D-01 위임 범위를 넣는다)
    # 분할결제(로컬 확장, 팀 합의 전): 고객이 지역화폐로 낼 금액. 있으면 옵션마다 split(지역화폐분+나머지분)을 담는다
    local_currency_amount_krw: Annotated[int, Field(ge=1)] | None = None


class CalculationRequest(BaseModel):
    request_id: Id
    user_id: Id
    restaurant_id: Id
    menu_subtotal_krw: Krw
    delivery_fee_krw: Krw
    payment_method_id: Id
    benefit_rule_version: str | None = None         # 없으면 현재 버전
    # 분할결제(로컬 확장): 이 결제수단이 낼 몫(혜택 적용 전 금액). 없으면 주문 전체
    share_krw: Annotated[int, Field(ge=1)] | None = None


class SyncRequest(BaseModel):
    source: str = Field(pattern="^KOMSCO_MERCHANT$")
    region_codes: list[Annotated[str, StringConstraints(min_length=1, max_length=16)]] = Field(min_length=1)
    mode: str = Field(default="FULL", pattern="^(FULL|INCREMENTAL)$")


class DocumentIn(BaseModel):
    document_id: Id
    title: str
    text: str = Field(min_length=1)


class ExtractionRequest(BaseModel):
    product_id: Id
    document: DocumentIn


class RuleVersionRequest(BaseModel):
    base_version: str
    rules: list[dict] = Field(min_length=1)
    deactivate_rule_ids: list[Id] = []
