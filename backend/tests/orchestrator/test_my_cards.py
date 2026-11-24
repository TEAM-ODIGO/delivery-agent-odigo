"""내 카드 추가 · 모의 마이데이터 (로컬 확장, 팀 합의 전).

가짜 E(FakePayment)에는 이 기능이 없어서, E 역할의 작은 대역과 httpx MockTransport로 시험한다.
진짜 E의 동작은 e-payment/tests/test_cards.py가 시험한다.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.db import get_session
from app.core.errors import AppError
from app.main import create_app
from app.modules.orchestrator import router as o_router
from app.modules.orchestrator.ports import HttpPaymentPort

USER = {"Authorization": "Bearer dev-token-U001"}


class StubE:
    """진짜 E의 카드 추가 API를 흉내 낸다. 받은 요청을 calls에 남긴다."""

    def __init__(self, base):
        self.base, self.calls, self.registered = base, [], {}

    def __getattr__(self, name):  # 결제 쪽 메서드는 가짜 E에 맡긴다
        return getattr(self.base, name)

    def card_catalog(self):
        return {
            "products": [
                {"product_id": "PRD-CARD-A", "name": "요기요 신한카드 체크", "has_benefit_rule": True}
            ],
            "mock": True,
        }

    def list_card_registrations(self, user_id):
        return {
            "user_id": user_id,
            "results": [
                {"payment_method_id": pid, "source": r["source"], "last4": r.get("last4")}
                for pid, r in self.registered.items()
            ],
        }

    def create_mydata_consent(self, user_id):
        self.calls.append(("consent", user_id))
        return {"consent_id": "MDC1", "user_id": user_id, "scopes": ["CARD_LIST"], "mock": True}

    def list_mydata_cards(self, user_id, consent_id):
        self.calls.append(("cards", user_id, consent_id))
        return {"consent_id": consent_id, "count": 0, "results": [], "mock": True}

    def register_card(self, body):
        self.calls.append(("register", body))
        self.registered["CARD900"] = body
        return {"payment_method": {"payment_method_id": "CARD900"}, "mock": True}

    def remove_card(self, user_id, payment_method_id):
        self.calls.append(("remove", user_id, payment_method_id))
        return {"payment_method_id": payment_method_id, "removed": True}


@pytest.fixture
def client(env, session_factory):
    app = create_app()

    def session():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[o_router.get_service] = lambda: env.svc()
    return TestClient(app)


@pytest.fixture
def stub(env):
    env.payment = StubE(env.world.payment)
    return env.payment


# ---------------- 로그인 · 가짜 E ----------------
def test_my_cards_routes_need_login(client):
    for method, path in (
        ("get", "/my-cards"),
        ("get", "/my-cards/catalog"),
        ("post", "/my-cards"),
        ("delete", "/my-cards/CARD1"),
        ("post", "/mydata/consents"),
        ("get", "/mydata/cards"),
    ):
        assert getattr(client, method)(f"/orchestrator{path}").status_code == 401, path


def test_my_cards_list_works_without_e_but_cannot_add(client):
    r = client.get("/orchestrator/my-cards", headers=USER)
    assert r.status_code == 200 and r.json()["can_add"] is False
    assert r.json()["count"] >= 2 and all(
        m["removable"] is False and m["source"] is None for m in r.json()["results"]
    )
    add = client.post(
        "/orchestrator/my-cards", headers=USER, json={"source": "MANUAL", "name": "내 카드"}
    )
    assert add.status_code == 503 and add.json()["error"]["code"] == "MY_CARDS_UNAVAILABLE"
    assert client.post("/orchestrator/mydata/consents", headers=USER).status_code == 503


# ---------------- E가 있을 때 ----------------
def test_my_cards_marks_added_cards_removable(client, stub):
    stub.registered["LOCAL001"] = {
        "source": "MYDATA",
        "last4": "1209",
    }  # 목록에 있는 수단 하나를 '추가된 카드'로 취급
    r = client.get("/orchestrator/my-cards", headers=USER).json()
    assert r["can_add"] is True
    by_id = {m["payment_method_id"]: m for m in r["results"]}
    assert by_id["LOCAL001"]["removable"] is True and by_id["LOCAL001"]["source"] == "MYDATA"
    assert by_id["LOCAL001"]["last4"] == "1209"
    assert by_id["CARD001"]["removable"] is False
    assert {"product_id", "has_benefit_rule", "monthly_performance_krw"} <= set(by_id["CARD001"])


def test_my_cards_list_survives_a_failing_registration_lookup(client, stub):
    def boom(user_id):
        raise AppError(503, "PAYMENT_UNAVAILABLE", "E 응답 없음")

    stub.list_card_registrations = boom
    r = client.get("/orchestrator/my-cards", headers=USER)
    assert r.status_code == 200 and all(m["removable"] is False for m in r.json()["results"])


def test_mydata_flow_uses_the_logged_in_user_only(client, stub):
    assert (
        client.get("/orchestrator/my-cards/catalog", headers=USER).json()["products"][0][
            "product_id"
        ]
        == "PRD-CARD-A"
    )
    c = client.post("/orchestrator/mydata/consents", headers=USER)
    assert c.status_code == 201 and c.json()["consent_id"] == "MDC1"
    assert (
        client.get(
            "/orchestrator/mydata/cards", params={"consent_id": "MDC1"}, headers=USER
        ).status_code
        == 200
    )
    assert stub.calls == [("consent", "U001"), ("cards", "U001", "MDC1")]


def test_add_card_sends_user_from_token_and_drops_empty_fields(client, stub):
    r = client.post(
        "/orchestrator/my-cards",
        headers=USER,
        json={"source": "MYDATA", "consent_id": "MDC1", "mydata_card_id": "MD-CARD-004"},
    )
    assert r.status_code == 201
    assert stub.calls == [
        (
            "register",
            {
                "user_id": "U001",
                "source": "MYDATA",
                "consent_id": "MDC1",
                "mydata_card_id": "MD-CARD-004",
            },
        )
    ]
    # 본문에 user_id를 넣어도 받지 않는다 (위조 방지)
    forged = client.post(
        "/orchestrator/my-cards",
        headers=USER,
        json={"source": "MANUAL", "name": "x", "user_id": "U002"},
    )
    assert forged.status_code == 400
    assert client.delete("/orchestrator/my-cards/CARD900", headers=USER).json() == {
        "payment_method_id": "CARD900",
        "removed": True,
    }
    assert stub.calls[-1] == ("remove", "U001", "CARD900")


@pytest.mark.parametrize(
    "body",
    [
        {"source": "MANUAL"},
        {"source": "MANUAL", "product_id": "PRD-CARD-A", "name": "x"},
        {"source": "MANUAL", "name": "x", "last4": "123456"},  # 카드 번호를 통째로 보내면 거부
        {"source": "MANUAL", "name": "x", "card_number": "1234123412341234"},
        {"source": "MYDATA", "consent_id": "MDC1"},
        {"source": "MYDATA", "consent_id": "MDC1", "mydata_card_id": "MD-CARD-004", "name": "x"},
        {"source": "OTHER", "name": "x"},
    ],
)
def test_add_card_validation(client, stub, body):
    r = client.post("/orchestrator/my-cards", headers=USER, json=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert stub.calls == []


# ---------------- HttpPaymentPort: E로 나가는 요청 모양 ----------------
def _port(handler):
    return HttpPaymentPort(
        "http://e",
        "svc-token",
        client=httpx.Client(base_url="http://e", transport=httpx.MockTransport(handler)),
    )


def test_http_port_sends_expected_requests():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.path,
                dict(request.url.params),
                request.headers["authorization"],
            )
        )
        return httpx.Response(200, json={"ok": True})

    port = _port(handler)
    port.card_catalog()
    port.list_card_registrations("U001")
    port.create_mydata_consent("U001")
    port.list_mydata_cards("U001", "MDC1")
    port.register_card({"user_id": "U001", "source": "MANUAL", "name": "x"})
    port.remove_card("U001", "CARD900")
    assert seen == [
        ("GET", "/internal/card-catalog", {}, "Bearer svc-token"),
        ("GET", "/internal/card-registrations", {"user_id": "U001"}, "Bearer svc-token"),
        ("POST", "/internal/mock-mydata/consents", {}, "Bearer svc-token"),
        (
            "GET",
            "/internal/mock-mydata/cards",
            {"user_id": "U001", "consent_id": "MDC1"},
            "Bearer svc-token",
        ),
        ("POST", "/internal/payment-methods", {}, "Bearer svc-token"),
        ("DELETE", "/internal/payment-methods/CARD900", {"user_id": "U001"}, "Bearer svc-token"),
    ]


def test_http_port_passes_e_errors_through():
    def handler(request):
        return httpx.Response(
            409,
            json={
                "error": {
                    "code": "ALREADY_REGISTERED",
                    "message": "이미 있는 카드예요.",
                    "details": {},
                }
            },
        )

    with pytest.raises(AppError) as err:
        _port(handler).register_card({"user_id": "U001", "source": "MANUAL", "name": "x"})
    assert (err.value.status, err.value.code) == (409, "ALREADY_REGISTERED")
