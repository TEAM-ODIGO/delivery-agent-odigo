"""A 모듈 요청·응답 모델 (JSON은 snake_case, 금액은 _krw 원 단위 정수)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.discovery.constants import TASTE_ORDER

# 공통 규칙: ID는 문자열, 64자 이하, 영문·숫자·_ - : . 만
ID_PATTERN = r"^[A-Za-z0-9_\-:.]{1,64}$"

# 맛 취향 코드 (constants.TASTES). 명세에 없는 A 확장 조건이라 D·B는 읽지 않아도 된다
Taste = Literal["SPICY", "SWEET", "SOUP", "CRISPY", "LIGHT", "CHEESY"]


def ordered_tastes(values: list[str] | None) -> list[str]:
    """중복을 빼고 TASTE_ORDER 순서로 맞춘다. 모르는 코드는 버린다 (LLM이 엉뚱한 값을 줄 때)."""
    result = []
    for code in TASTE_ORDER:
        if values and code in values:
            result.append(code)
    return result


class DeliveryAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_code: str = Field(min_length=1, max_length=20)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class PaymentRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_currency_only: bool = False


class Constraints(BaseModel):
    """주문 조건. A가 저장하고 D가 같은 형태로 판정에 쓴다."""

    budget_krw: int | None = None
    people: int | None = None
    cuisine: str | None = None
    excluded_ingredients: list[str] = Field(default_factory=list)
    max_spice_level: int | None = None  # 0=안 매움 ~ 3
    payment_requirements: PaymentRequirements = Field(default_factory=PaymentRequirements)
    delivery_address: DeliveryAddress | None = None
    max_delivery_minutes: int | None = None
    menu_keywords: list[str] = Field(default_factory=list)
    # A 확장: 메뉴 이름 없이 맛만 말했을 때 ("매운 음식" → ["SPICY"])
    taste_preferences: list[str] = Field(default_factory=list)
    # A 확장 (이슈 #58): 브랜드로 말했을 때 ("맘스터치 시켜줘" → ["맘스터치"]). 그 브랜드 가게에서 먼저 찾는다
    brands: list[str] = Field(default_factory=list)


# ---------- A-01 ----------
class CreateRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(pattern=ID_PATTERN)
    user_id: str = Field(pattern=ID_PATTERN)
    message: str
    delivery_address: DeliveryAddress


# ---------- A-04 ----------
class ConstraintsPatch(BaseModel):
    """보낸 필드만 바꾼다. null을 보내면 그 조건을 비운다."""

    model_config = ConfigDict(extra="forbid")

    budget_krw: int | None = Field(default=None, gt=0, le=10_000_000)
    people: int | None = Field(default=None, ge=1, le=50)
    cuisine: str | None = None
    excluded_ingredients: list[str] | None = None
    max_spice_level: int | None = Field(default=None, ge=0, le=3)
    payment_requirements: PaymentRequirements | None = None
    delivery_address: DeliveryAddress | None = None
    max_delivery_minutes: int | None = Field(default=None, ge=10, le=180)
    menu_keywords: list[str] | None = None
    taste_preferences: list[Taste] | None = None
    brands: list[str] | None = None


class PatchConstraintsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    constraints: ConstraintsPatch


# ---------- A-05 ----------
class SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_n: int = Field(default=3, ge=1, le=10)
    # A 확장 (이슈 #68, 재추천): 이미 보여 준 가게는 빼고 다음 순위를 찾는다
    exclude_restaurant_ids: list[str] = Field(default_factory=list, max_length=200)


# ---------- A-06 ----------
class SelectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=ID_PATTERN)


# ---------- LLM 추출 결과 검사용 ----------
class LlmExtraction(BaseModel):
    """LLM이 돌려준 JSON을 그대로 믿지 않고 이 모델로 범위를 확인한다."""

    model_config = ConfigDict(extra="ignore")

    budget_krw: int | None = Field(default=None, gt=0, le=10_000_000)
    people: int | None = Field(default=None, ge=1, le=50)
    cuisine: str | None = None
    excluded_ingredients: list[str] = Field(default_factory=list)
    max_spice_level: int | None = Field(default=None, ge=0, le=3)
    local_currency_only: bool = False
    max_delivery_minutes: int | None = Field(default=None, ge=10, le=180)
    menu_keywords: list[str] = Field(default_factory=list)
    taste_preferences: list[str] = Field(default_factory=list)

    @field_validator("taste_preferences", mode="before")
    @classmethod
    def keep_known_tastes(cls, value):
        if not isinstance(value, list):
            return []
        return ordered_tastes([str(code).upper() for code in value])
