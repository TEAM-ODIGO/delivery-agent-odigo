"""http_benefit_port: C가 별도 서버일 때 HTTP로 붙는 연결 (가짜 C 서버로 확인)."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.modules.delegation.benefits import BenefitUnavailable, http_benefit_port
from app.modules.delegation.constants import PaymentMethodType
from app.modules.delegation.schemas import Quote
from scripts.fake_c_server import app as fake_c
from tests.conftest import make_quote


@pytest.fixture
def port():
    client = TestClient(fake_c, base_url="http://c.test")
    return http_benefit_port("http://c.test", "tok", client=client)


def test_discount_and_type_over_http(port):
    quote = Quote.model_validate(make_quote())
    assert port.expected_instant_discount(quote, request_id="REQ001", user_id="U001") == 2000
    assert port.payment_method_type("U001", "LOCAL001") == PaymentMethodType.LOCAL_CURRENCY


def test_unknown_rule_version_is_unavailable(port):
    quote = Quote.model_validate(make_quote(benefit_rule_version="BEN-NOPE"))
    with pytest.raises(BenefitUnavailable):
        port.expected_instant_discount(quote, request_id="REQ001", user_id="U001")


def test_c_down_is_unavailable():
    def down(request):
        raise httpx.ConnectError("refused")

    client = httpx.Client(base_url="http://c.test", transport=httpx.MockTransport(down))
    port = http_benefit_port("http://c.test", "tok", client=client)
    with pytest.raises(BenefitUnavailable):
        port.payment_method_type("U001", "CARD001")
