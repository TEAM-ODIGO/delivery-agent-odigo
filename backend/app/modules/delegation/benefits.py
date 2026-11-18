"""D가 C(혜택) 기능에 물어볼 것을 정해 둔 '연결 지점'(port).

D는 할인을 직접 계산하지 않는다. 계산 규칙은 C 한 곳에만 있어야 하기 때문이다.
D는 '이 견적의 즉시할인이 정말 이 금액이 맞는가'를 C에게 다시 물어(C-05 혜택 재계산),
B가 넘긴 할인액과 비교만 한다.

구현은 두 가지다.
- StaticBenefitPort: C가 없을 때 쓰는 임시 고정 규칙 (테스트·데모용).
- CBenefitPort: C의 함수를 받아 명세(C-05·C-02) 형태로 묻는 어댑터.
  C 모듈이 생기면 main.py에서 C의 함수 두 개만 넘겨 끼우면 되고, D의 나머지 코드는 그대로다.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.modules.delegation.constants import PaymentMethodType
from app.modules.delegation.schemas import Quote


class BenefitUnavailable(Exception):
    """C에 물어볼 수 없음(장애, 규칙 없음, 응답 형식 이상 등). D는 이때 통과시키지 않는다."""


class BenefitPort(Protocol):
    def expected_instant_discount(self, quote: Quote, *, request_id: str, user_id: str) -> int:
        """C-05 POST /benefit/calculations: 지정 결제수단·규칙 버전으로 계산한 즉시할인액.

        C가 eligible=false(혜택 대상 아님)로 답하면 0을 돌려준다.
        규칙을 못 찾거나(RULE_NOT_FOUND) 응답이 없으면 BenefitUnavailable.
        """
        ...

    def payment_method_type(self, user_id: str, payment_method_id: str) -> PaymentMethodType:
        """C-02 GET /benefit/users/{user_id}/payment-methods 의 type (CARD / LOCAL_CURRENCY)."""
        ...

    def expected_local_charge(self, quote: Quote, *, request_id: str, user_id: str) -> int:
        """분할결제(로컬 확장): 지역화폐로 실제 차감할 금액 = 지역화폐 몫 - 그 몫의 즉시할인."""
        ...


@dataclass
class StaticBenefitPort:
    """C 연동 전 임시 구현. 결제수단별 정액 즉시할인만 있다고 가정한다.

    discounts: {"CARD001": 2000} → CARD001로 결제하면 2,000원 즉시할인
    """

    rule_version: str = "BEN-V1"
    discounts: dict[str, int] = field(default_factory=lambda: {"CARD001": 2000})
    method_types: dict[str, PaymentMethodType] = field(
        default_factory=lambda: {"LOCAL001": PaymentMethodType.LOCAL_CURRENCY}
    )
    available: bool = True  # 테스트에서 False로 바꿔 C 장애를 흉내 낸다

    def expected_instant_discount(self, quote: Quote, *, request_id: str, user_id: str) -> int:
        if not self.available:
            raise BenefitUnavailable("C 응답 없음")
        if quote.benefit_rule_version != self.rule_version:
            raise BenefitUnavailable(f"알 수 없는 할인 규칙 버전: {quote.benefit_rule_version}")
        discount = self.discounts.get(quote.payment_method_id, 0)
        if quote.local_currency_payment_method_id:
            discount += self.discounts.get(quote.local_currency_payment_method_id, 0)
        return discount

    def expected_local_charge(self, quote: Quote, *, request_id: str, user_id: str) -> int:
        if not self.available:
            raise BenefitUnavailable("C 응답 없음")
        share = quote.local_currency_amount_krw or 0
        return share - self.discounts.get(quote.local_currency_payment_method_id or "", 0)

    def payment_method_type(self, user_id: str, payment_method_id: str) -> PaymentMethodType:
        if not self.available:
            raise BenefitUnavailable("C 응답 없음")
        return self.method_types.get(payment_method_id, PaymentMethodType.CARD)


# C 함수의 모양 (명세 C-05·C-02와 같은 dict를 주고받는다)
CalculateFn = Callable[[dict[str, Any]], Mapping[str, Any]]
ListMethodsFn = Callable[[str], Mapping[str, Any]]


@dataclass
class CBenefitPort:
    """C 모듈 함수를 명세 형태로 부르는 어댑터.

    calculate(body) : C-05 요청 body(dict) → C-05 응답(dict). 규칙이 없으면 예외를 던진다.
    list_methods(user_id) : C-02 응답(dict) {"results": [{"payment_method_id", "type", ...}]}

    C 쪽 예외는 종류와 상관없이 BenefitUnavailable로 바꾼다 → D는 '확인 불가'로 차단(fail-closed).
    C-05는 계산 전용이라 Idempotency-Key를 보내지 않는다(명세).
    """

    calculate: CalculateFn
    list_methods: ListMethodsFn

    def _discount_of(self, quote: Quote, payment_method_id: str, share_krw: int | None,
                     *, request_id: str, user_id: str) -> int:
        """C-05로 결제수단 하나의 즉시할인을 다시 계산한다. share_krw가 있으면 그 몫에 대해서만(분할결제)."""
        body = {
            "request_id": request_id,
            "user_id": user_id,
            "restaurant_id": quote.restaurant_id,
            "menu_subtotal_krw": quote.menu_subtotal_krw,
            "delivery_fee_krw": quote.delivery_fee_krw,
            "payment_method_id": payment_method_id,
            "benefit_rule_version": quote.benefit_rule_version,
        }
        if share_krw is not None:
            body["share_krw"] = share_krw
        try:
            res = self.calculate(body)
        except Exception as exc:  # C의 404 RULE_NOT_FOUND·장애 모두 같은 처리
            raise BenefitUnavailable(f"C-05 실패: {type(exc).__name__}") from exc
        try:
            # 다른 결제수단·다른 규칙 버전으로 계산한 답이면 믿지 않는다
            if res["payment_method_id"] != payment_method_id:
                raise BenefitUnavailable("C-05 응답의 결제수단이 다름")
            if res["benefit_rule_version"] != quote.benefit_rule_version:
                raise BenefitUnavailable("C-05 응답의 규칙 버전이 다름")
            if res["eligible"] is False:
                return 0  # 혜택 대상 아님 → 기대 할인 0원
            discount = res["instant_discount_krw"]
        except (KeyError, TypeError) as exc:
            raise BenefitUnavailable("C-05 응답 형식 이상") from exc
        if not isinstance(discount, int) or isinstance(discount, bool) or discount < 0:
            raise BenefitUnavailable("C-05 할인액 형식 이상")
        return discount

    def expected_instant_discount(self, quote: Quote, *, request_id: str, user_id: str) -> int:
        local_pm, local_amount = quote.local_currency_payment_method_id, quote.local_currency_amount_krw
        if not local_pm:
            return self._discount_of(quote, quote.payment_method_id, None, request_id=request_id, user_id=user_id)
        if local_amount is None:
            raise BenefitUnavailable("분할결제인데 지역화폐 금액이 없음")
        total = quote.menu_subtotal_krw + quote.delivery_fee_krw
        if not 0 < local_amount < total:
            raise BenefitUnavailable("지역화폐 금액이 올바르지 않음")
        main = self._discount_of(quote, quote.payment_method_id, total - local_amount,
                                 request_id=request_id, user_id=user_id)
        local = self._discount_of(quote, local_pm, local_amount, request_id=request_id, user_id=user_id)
        return main + local

    def expected_local_charge(self, quote: Quote, *, request_id: str, user_id: str) -> int:
        local_amount = quote.local_currency_amount_krw
        if not quote.local_currency_payment_method_id or local_amount is None:
            raise BenefitUnavailable("분할결제가 아님")
        local = self._discount_of(quote, quote.local_currency_payment_method_id, local_amount,
                                  request_id=request_id, user_id=user_id)
        return local_amount - local

    def payment_method_type(self, user_id: str, payment_method_id: str) -> PaymentMethodType:
        try:
            res = self.list_methods(user_id)
            for m in res["results"]:
                if m["payment_method_id"] == payment_method_id:
                    return PaymentMethodType(m["type"])
        except Exception as exc:
            raise BenefitUnavailable(f"C-02 실패: {type(exc).__name__}") from exc
        # 사용자가 갖고 있지 않은 결제수단이면 유형을 확인할 수 없다
        raise BenefitUnavailable("C-02에 없는 결제수단")


def http_benefit_port(
    base_url: str,
    service_token: str,
    *,
    timeout: float = 3.0,
    client: httpx.Client | None = None,
) -> CBenefitPort:
    """C가 별도로 떠 있는 서버일 때(각자 개발 중 함께 돌려 볼 때) HTTP로 C-05·C-02를 부른다.

    base_url 예: http://localhost:8001  → POST {base_url}/benefit/calculations
                                         GET  {base_url}/benefit/users/{user_id}/payment-methods
    2xx가 아니거나 연결이 안 되면 예외 → CBenefitPort가 BenefitUnavailable로 바꾼다 (차단).
    모놀리스로 합친 뒤에는 이 함수 대신 C의 함수를 CBenefitPort에 바로 넘기면 된다.
    """
    http = client or httpx.Client(base_url=base_url, timeout=timeout)
    headers = {"Authorization": f"Bearer {service_token}", "Accept": "application/json"}

    def calculate(body: dict[str, Any]) -> Mapping[str, Any]:
        r = http.post(
            "/benefit/calculations",
            json=body,
            headers={**headers, "X-Request-ID": body["request_id"]},
        )
        r.raise_for_status()
        return r.json()

    def list_methods(user_id: str) -> Mapping[str, Any]:
        r = http.get(f"/benefit/users/{user_id}/payment-methods", headers=headers)
        r.raise_for_status()
        return r.json()

    return CBenefitPort(calculate=calculate, list_methods=list_methods)
