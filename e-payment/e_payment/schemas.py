"""요청 모델 (명세 E-01·E-05·E-10). 알 수 없는 필드는 거부한다."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9_\-:.]{1,64}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaymentRequest(_Strict):
    request_id: Id
    user_id: Id
    execution_id: Id
    approval_id: Id
    quote_id: Id
    proposal_hash: Annotated[str, Field(min_length=1, max_length=80)]
    payment_method_id: Id
    charge_krw: Annotated[int, Field(gt=0, le=100_000_000, strict=True)]
    # 분할결제(로컬 확장, 팀 합의 전). charge_krw = 전체 결제금액, 그중 지역화폐 몫이 local_currency_charge_krw,
    # 나머지(charge_krw − 지역화폐 몫)는 payment_method_id(카드)로 낸다. 둘 다 있거나 둘 다 없어야 한다.
    local_currency_payment_method_id: Id | None = None
    local_currency_charge_krw: Annotated[int, Field(gt=0, le=100_000_000, strict=True)] | None = None

    @model_validator(mode="after")
    def _split_fields(self):
        local_id, local_krw = self.local_currency_payment_method_id, self.local_currency_charge_krw
        if (local_id is None) != (local_krw is None):
            raise ValueError("local_currency_payment_method_id와 local_currency_charge_krw는 함께 보내야 합니다.")
        if local_krw is not None and local_krw >= self.charge_krw:
            raise ValueError("지역화폐 몫은 전체 결제금액보다 작아야 합니다.")
        if local_id is not None and local_id == self.payment_method_id:
            raise ValueError("지역화폐와 나머지 결제수단은 달라야 합니다.")
        return self


class RefundRequest(_Strict):
    user_id: Id
    amount_krw: Annotated[int, Field(gt=0, le=100_000_000, strict=True)]
    reason: Annotated[str, Field(max_length=300)] | None = None


class FepAuthorization(_Strict):
    transaction_id: Id
    payment_id: Id
    amount_krw: Annotated[int, Field(gt=0, strict=True)]
    payment_method_token: str
    mock: bool = True


class FepRefund(_Strict):
    refund_transaction_id: Id
    original_transaction_id: Id
    refund_id: Id
    amount_krw: Annotated[int, Field(gt=0, strict=True)]
    mock: bool = True


class ReconciliationRequest(_Strict):
    transaction_ids: Annotated[list[Id], Field(min_length=1, max_length=500)]
    reason: str | None = None
