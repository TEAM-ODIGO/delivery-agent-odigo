"""O API 요청 형식 (O-01·O-02). 응답은 service가 명세 모양의 dict로 만든다.

O-02는 type에 따라 body 모양이 달라서 type을 기준으로 나눠 받는다(discriminated union).
expected_state_version은 모든 응답 종류에서 필수다.
"""

from typing import Annotated, Literal

from pydantic import Field

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


class ApproveReply(Strict):
    type: Literal["APPROVE"]
    expected_state_version: Version


class CancelReply(Strict):
    type: Literal["CANCEL"]
    expected_state_version: Version


Reply = Annotated[
    ClarificationReply
    | SelectCandidateReply
    | SelectPaymentMethodReply
    | ApproveReply
    | CancelReply,
    Field(discriminator="type"),
]
