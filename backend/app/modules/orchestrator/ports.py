"""O가 다른 모듈에 기대하는 모양(연결 지점, port).

- A·B·C는 같은 서버 안의 함수 호출이다. 명세 요청 body(dict)를 넘기고 명세 응답(dict)을 받는다.
  업무 오류는 공통 AppError(code, message, details)로 던진다 (팀 공통 규칙 '모듈 간 오류').
- E는 별도 서버라 HTTP다. 응답을 못 받으면 PaymentResponseLost (= 결과 모름, 재결제 금지).
- D는 진짜 모듈(DelegationService)을 그대로 쓴다.

지금은 app/fakes의 가짜가 이 모양을 채운다. 진짜 모듈이 생기면 wiring.py에서 바꿔 끼운다.
"""

from typing import Any, Protocol

import httpx

from app.core.errors import AppError

Body = dict[str, Any]


class PaymentResponseLost(Exception):
    """E-01을 보냈지만 결과를 모른다 (타임아웃·연결 끊김·5xx). 다시 결제하지 않고 E-03으로 확인."""


class DiscoveryPort(Protocol):  # A
    def create_request(self, body: Body) -> Body: ...  # A-01

    def update_constraints(self, request_id: str, body: Body) -> Body: ...  # A-04

    def search(self, request_id: str, body: Body) -> Body: ...  # A-05

    def select(self, request_id: str, body: Body) -> Body: ...  # A-06


class OrderingPort(Protocol):  # B
    def create_order_plan(self, body: Body, *, idempotency_key: str) -> Body: ...  # B-01

    def bind_benefit(self, order_plan_id: str, body: Body, *, idempotency_key: str) -> Body: ...

    def create_approval_target(
        self, order_plan_id: str, body: Body, *, idempotency_key: str
    ) -> Body: ...  # B-06

    def check_execution(self, order_plan_id: str, body: Body) -> Body: ...  # B-07

    def record_payment_event(
        self, order_plan_id: str, body: Body, *, idempotency_key: str
    ) -> Body: ...  # B-08


class RecommendationPort(Protocol):  # C
    def recommend(self, body: Body, *, idempotency_key: str | None = None) -> Body: ...  # C-01


class PaymentPort(Protocol):  # E
    def request_payment(self, body: Body) -> Body: ...  # E-01 (Idempotency-Key = execution_id)

    def get_by_execution(self, execution_id: str) -> Body: ...  # E-03


class HttpPaymentPort:
    """진짜 E(또는 scripts/fake_e_server.py)를 HTTP로 부른다."""

    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout: float = 5.0,
        client: httpx.Client | None = None,
    ):
        self.http = client or httpx.Client(base_url=base_url, timeout=timeout)
        self.headers = {"Authorization": f"Bearer {service_token}", "Accept": "application/json"}

    def request_payment(self, body: Body) -> Body:
        headers = {
            **self.headers,
            "X-Request-ID": body["request_id"],
            "Idempotency-Key": body["execution_id"],
        }
        try:
            r = self.http.post("/payments", json=body, headers=headers)
        except httpx.HTTPError as exc:  # 타임아웃·연결 끊김: E가 처리했을 수도 있다
            raise PaymentResponseLost(type(exc).__name__) from exc
        if r.status_code >= 500:
            raise PaymentResponseLost(f"E-01 {r.status_code}")
        if r.status_code >= 400:
            raise _app_error(r)
        return r.json()

    def get_by_execution(self, execution_id: str) -> Body:
        try:
            r = self.http.get(f"/payments/executions/{execution_id}", headers=self.headers)
        except httpx.HTTPError as exc:
            raise AppError(503, "PAYMENT_UNAVAILABLE", "E 응답 없음") from exc
        if r.status_code >= 400:
            raise _app_error(r)
        return r.json()


def _app_error(r: httpx.Response) -> AppError:
    try:
        err = r.json().get("error") or {}
    except ValueError:
        err = {}
    return AppError(
        r.status_code,
        err.get("code", "E_ERROR"),
        err.get("message", "E 오류"),
        err.get("details") or {},
    )
