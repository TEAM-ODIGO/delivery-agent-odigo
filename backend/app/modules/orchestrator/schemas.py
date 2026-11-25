"""O API 요청 형식 (O-01·O-02). 응답은 service가 명세 모양의 dict로 만든다.

O-02는 type에 따라 body 모양이 달라서 type을 기준으로 나눠 받는다(discriminated union).
expected_state_version은 모든 응답 종류에서 필수다.
"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.modules.delegation.schemas import Delegation, Id, Strict


class DeliveryAddress(Strict):
    region_code: Annotated[str, Field(min_length=1, max_length=20)]
    lat: Annotated[float, Field(ge=-90, le=90)]
    lng: Annotated[float, Field(ge=-180, le=180)]


class RunCreate(Strict):
    """O-01 POST /orchestrator/runs"""

    message: Annotated[str, Field(min_length=1, max_length=300)]
    delivery_address: DeliveryAddress
    delegation: Delegation = Field(default_factory=Delegation)


Version = Annotated[int, Field(ge=0)]


class Answer(Strict):
    slot: Annotated[str, Field(min_length=1, max_length=40)]
    value: int | Annotated[str, Field(max_length=100)]


class ClarificationReply(Strict):
    type: Literal["CLARIFICATION"]
    expected_state_version: Version
    answers: Annotated[list[Answer], Field(min_length=1, max_length=10)]


class SelectCandidateReply(Strict):
    type: Literal["SELECT_CANDIDATE"]
    expected_state_version: Version
    candidate_index: Annotated[int, Field(ge=0, le=50)]
    # 예산 초과 확인(로컬 확장, 팀 합의 전): 예산을 넘는 가게임을 알고 계속하겠다고 했는가.
    # 안 보내거나 false면 기존과 같이 D가 예산 초과를 막는다.
    allow_over_budget: bool = False


class SelectPaymentMethodReply(Strict):
    type: Literal["SELECT_PAYMENT_METHOD"]
    expected_state_version: Version
    payment_method_id: Id
    # 분할결제(로컬 확장, 팀 합의 전): 지역화폐로 낼 금액. 있으면 payment_method_id는 나머지를 낼 카드
    local_currency_amount_krw: Annotated[int, Field(ge=1, le=10_000_000)] | None = None


class CardTap(Strict):
    """휴대폰이 읽은 NFC 실물 카드 정보 (로컬 확장, 팀 합의 전). 둘 중 하나는 있어야 한다.
    token: 카드 URL의 t 값 (아이폰·안드로이드), serial_number: 카드 일련번호 (안드로이드만)"""

    serial_number: Annotated[str, Field(min_length=1, max_length=40)] | None = None
    token: Annotated[str, Field(min_length=1, max_length=100)] | None = None

    @model_validator(mode="after")
    def _one_of(self):
        if self.serial_number is None and self.token is None:
            raise ValueError("serial_number나 token 중 하나는 있어야 합니다.")
        return self


class ApproveReply(Strict):
    type: Literal["APPROVE"]
    expected_state_version: Version
    # NFC 카드를 태그하고 승인했을 때만 온다. O가 E에 같은 카드인지 먼저 묻고, E-01에도 함께 넘긴다
    card_tap: CardTap | None = None


class CancelReply(Strict):
    type: Literal["CANCEL"]
    expected_state_version: Version


class RecommendAgainReply(Strict):
    """후보 화면에서 '다시 추천' (로컬 확장, 이슈 #68): 지금까지 보여 준 가게를 빼고 다시 찾는다."""

    type: Literal["RECOMMEND_AGAIN"]
    expected_state_version: Version


class RequoteFromCartReply(Strict):
    """승인 화면 (로컬 확장, 이슈 #86): 요기요 장바구니 금액이 견적과 다르면 그 금액으로 견적·승인을 다시 만든다."""

    type: Literal["REQUOTE_FROM_CART"]
    expected_state_version: Version


Reply = Annotated[
    ClarificationReply
    | SelectCandidateReply
    | SelectPaymentMethodReply
    | ApproveReply
    | CancelReply
    | RecommendAgainReply
    | RequoteFromCartReply,
    Field(discriminator="type"),
]


# ---------------- 카드 혜택 화면 (로컬 확장, 팀 합의 전) ----------------
class CardDashboardRequest(Strict):
    """결제 중이 아닐 때 NFC 카드를 대면 그 카드의 혜택 화면을 연다."""

    card_tap: CardTap


class RefundCreate(Strict):
    """카드 혜택 화면의 '환불' 버튼. 이 결제의 남은 금액 전부를 환불한다 (금액은 O가 E 기록으로 정한다)."""

    payment_method_id: Id  # 지금 보고 있는 카드. 이 카드의 결제가 맞는지 확인한다
    reason: Annotated[str, Field(max_length=300)] | None = None


# ---------------- 내 카드 추가 · 모의 마이데이터 (로컬 확장, 팀 합의 전) ----------------
class CardAdd(Strict):
    """내 결제수단에 카드 한 장을 넣는다 (user_id는 토큰에서).
    카드 번호 전체·유효기간·CVC는 받지 않는다.

    MANUAL: product_id(혜택 규칙이 있는 카드)를 고르거나 name을 직접 적는다(혜택 규칙 없음).
    MYDATA: 모의 마이데이터에서 고른 카드. consent_id와 mydata_card_id가 필요하다.
    """

    source: Literal["MANUAL", "MYDATA"]
    product_id: Id | None = None
    name: Annotated[str, Field(min_length=1, max_length=40)] | None = None
    issuer: Annotated[str, Field(min_length=1, max_length=20)] | None = None
    last4: Annotated[str, Field(pattern=r"^[0-9]{4}$")] | None = None
    consent_id: Id | None = None
    mydata_card_id: Id | None = None

    @model_validator(mode="after")
    def _by_source(self):
        if self.source == "MANUAL":
            if self.consent_id is not None or self.mydata_card_id is not None:
                raise ValueError("직접 입력에는 consent_id·mydata_card_id를 보낼 수 없습니다.")
            if self.product_id is None and self.name is None:
                raise ValueError("product_id나 name 중 하나는 있어야 합니다.")
            if self.product_id is not None and (self.name is not None or self.issuer is not None):
                raise ValueError("product_id를 고르면 name·issuer는 보낼 수 없습니다.")
        else:
            if self.consent_id is None or self.mydata_card_id is None:
                raise ValueError("마이데이터에는 consent_id와 mydata_card_id가 필요합니다.")
            if any(v is not None for v in (self.product_id, self.name, self.issuer, self.last4)):
                raise ValueError("마이데이터에는 카드 정보를 따로 보낼 수 없습니다.")
        return self


class ShownCandidate(Strict):
    candidate_index: int = Field(ge=0)
    collapsed: bool = False  # '조건을 조금 벗어난 가게 더 보기'를 펼쳐서 보인 후보
    live_status: str | None = Field(default=None, max_length=20)  # 그때 화면의 요기요 실시간 확인 결과


class CandidateViews(Strict):
    """(로컬 확장, 이슈 #48) 후보 화면이 실제로 보여 준 후보. 점주 대시보드의 '화면 표시' 수가 된다."""

    shown: list[ShownCandidate] = Field(max_length=20)
