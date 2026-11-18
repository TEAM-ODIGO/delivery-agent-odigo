"""별도 E에 시연용 결과를 요청별로 전달하는 O 연결 계약."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from app.fakes.payment import FakePayment
from app.fakes.scenario import ScenarioBook
from app.modules.orchestrator import wiring
from app.modules.orchestrator.ports import HttpPaymentPort, PaymentResponseLost
from app.modules.orchestrator.wiring import _ScenarioHttpPayment
from tests.orchestrator.conftest import OK_MESSAGE


def _body(request_id: str) -> dict:
    return {"request_id": request_id, "execution_id": f"EXE-{request_id}"}


def test_http_e_sends_override_only_for_tagged_requests():
    headers_by_request = {}

    def respond(request):
        body = json.loads(request.content)
        headers_by_request[body["request_id"]] = dict(request.headers)
        return httpx.Response(200, json={"status": "APPROVED"})

    client = httpx.Client(base_url="http://e", transport=httpx.MockTransport(respond))
    book = ScenarioBook()
    book.set("NORMAL", set())
    book.set("DECLINE", {"PAY_DECLINE"})
    book.set("UNKNOWN", {"PAY_UNKNOWN"})
    port = _ScenarioHttpPayment(HttpPaymentPort("http://e", "test-token", client=client), book)

    for request_id in ("NORMAL", "DECLINE", "UNKNOWN"):
        port.request_payment(_body(request_id))

    assert "x-mock-outcome" not in headers_by_request["NORMAL"]
    assert headers_by_request["DECLINE"]["x-mock-outcome"] == "DECLINE"
    assert headers_by_request["UNKNOWN"]["x-mock-outcome"] == "UNKNOWN"
    assert all(
        headers_by_request[rid]["idempotency-key"] == f"EXE-{rid}"
        for rid in headers_by_request
    )


def test_http_e_without_demo_wrapper_sends_no_override():
    received = []

    def respond(request):
        received.append(request.headers)
        return httpx.Response(200, json={"status": "APPROVED"})

    client = httpx.Client(base_url="http://e", transport=httpx.MockTransport(respond))
    HttpPaymentPort("http://e", "test-token", client=client).request_payment(_body("NORMAL"))
    assert "x-mock-outcome" not in received[0]


def test_payment_port_uses_scenario_wrapper_only_in_demo_mode(monkeypatch):
    monkeypatch.setattr(
        wiring, "settings", SimpleNamespace(e_payment_url="http://e", service_token="t")
    )
    monkeypatch.setattr(wiring, "_http_payment", None)
    monkeypatch.setattr(wiring, "world", lambda: SimpleNamespace(book=ScenarioBook()))

    monkeypatch.delenv("O_DEMO_E_OVERRIDE", raising=False)
    normal = wiring.payment_port()
    assert isinstance(normal, HttpPaymentPort)

    monkeypatch.setattr(wiring, "_http_payment", None)
    monkeypatch.setenv("O_DEMO_E_OVERRIDE", "1")
    demo = wiring.payment_port()
    assert isinstance(demo, _ScenarioHttpPayment)


def test_response_loss_is_raised_after_e_accepted_payment():
    seen = []

    def respond(request):
        seen.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"status": "APPROVED"})
        return httpx.Response(200, json={"status": "APPROVED"})

    client = httpx.Client(base_url="http://e", transport=httpx.MockTransport(respond))
    book = ScenarioBook()
    book.set("LOST", {"PAY_LOST"})
    port = _ScenarioHttpPayment(HttpPaymentPort("http://e", "test-token", client=client), book)

    with pytest.raises(PaymentResponseLost):
        port.request_payment(_body("LOST"))
    assert seen == ["POST"]
    assert port.get_by_execution("EXE-LOST")["status"] == "APPROVED"
    assert seen == ["POST", "GET"]


def test_o_scenarios_with_separate_http_e(env):
    """요청별 모의 결과를 받는 별도 E로 o5·o5b·o8과 일반 결제를 확인한다."""
    e_book = ScenarioBook()
    e = FakePayment(e_book, env._check, now=env.clock)
    e_app = FastAPI()

    @e_app.post("/payments")
    def pay(body: dict, x_mock_outcome: str | None = Header(default=None)):
        flags = {"DECLINE": "PAY_DECLINE", "UNKNOWN": "PAY_UNKNOWN"}
        e_book.set(body["request_id"], {flags[x_mock_outcome]} if x_mock_outcome else set())
        return e.request_payment(body)

    @e_app.get("/payments/executions/{execution_id}")
    def lookup(execution_id: str):
        return e.get_by_execution(execution_id)

    env.payment = _ScenarioHttpPayment(
        HttpPaymentPort("http://e", "test-token", client=TestClient(e_app)),
        env.world.book,
    )

    unknown = env.flow("COMPLETED", message=OK_MESSAGE + " #결제미확정")
    assert unknown["state"] == "PAYMENT_UNKNOWN"
    assert env.get(unknown)["state"] == "COMPLETED"

    declined = env.flow("COMPLETED", message=OK_MESSAGE + " #결제거절")
    assert declined["state"] == "FAILED"
    assert declined["result"]["payment_status"] == "FAILED"

    normal = env.flow("COMPLETED")
    assert normal["state"] == "COMPLETED"
