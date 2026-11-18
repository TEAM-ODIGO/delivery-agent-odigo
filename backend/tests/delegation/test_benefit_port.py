"""CBenefitPort: C 함수가 명세(C-05·C-02)대로 답할 때와 이상하게 답할 때."""

import json
from pathlib import Path

import pytest

from app.modules.delegation.benefits import BenefitUnavailable, CBenefitPort
from app.modules.delegation.constants import PaymentMethodType
from app.modules.delegation.schemas import Quote

SPEC = json.loads((Path(__file__).parent / "spec_examples.json").read_text())["apis"]
C05 = SPEC["C-05"]["response"]
C02 = SPEC["C-02"]["response"]
QUOTE = Quote.model_validate(SPEC["D-02"]["request"]["quote"])


def port(calc=lambda body: C05, methods=lambda uid: C02):
    return CBenefitPort(calculate=calc, list_methods=methods)


def discount(p):
    return p.expected_instant_discount(QUOTE, request_id="REQ001", user_id="U001")


def test_sends_spec_c05_request():
    seen = {}
    discount(port(calc=lambda body: seen.update(body) or C05))
    assert set(seen) == set(SPEC["C-05"]["request"])  # Idempotency-Key 같은 추가 값 없음
    assert seen["restaurant_id"] == QUOTE.restaurant_id
    assert seen["benefit_rule_version"] == QUOTE.benefit_rule_version


def test_spec_response_gives_discount():
    assert discount(port()) == C05["instant_discount_krw"] == 2000


def test_not_eligible_means_zero():
    assert discount(port(calc=lambda b: {**C05, "eligible": False, "instant_discount_krw": 0})) == 0


@pytest.mark.parametrize(
    "bad",
    [
        {**C05, "benefit_rule_version": "BEN-V2"},  # 다른 규칙 버전으로 계산
        {**C05, "payment_method_id": "CARD999"},  # 다른 결제수단으로 계산
        {**C05, "instant_discount_krw": -1},
        {**C05, "instant_discount_krw": "2000"},
        {"eligible": True},  # 필드 빠짐
    ],
)
def test_odd_c05_answer_is_unavailable(bad):
    with pytest.raises(BenefitUnavailable):
        discount(port(calc=lambda b: bad))


def test_c05_error_is_unavailable():
    def rule_not_found(body):
        raise LookupError("RULE_NOT_FOUND")

    with pytest.raises(BenefitUnavailable):
        discount(port(calc=rule_not_found))


def test_payment_method_type_from_c02():
    p = port()
    assert p.payment_method_type("U001", "LOCAL001") == PaymentMethodType.LOCAL_CURRENCY
    assert p.payment_method_type("U001", "CARD001") == PaymentMethodType.CARD
    with pytest.raises(BenefitUnavailable):
        p.payment_method_type("U001", "NOT-MINE")  # 사용자가 갖지 않은 수단


def test_c02_error_is_unavailable():
    def down(uid):
        raise ConnectionError

    with pytest.raises(BenefitUnavailable):
        port(methods=down).payment_method_type("U001", "CARD001")
