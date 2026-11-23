"""E-11 결제수단 원장 조회 (C → E, 읽기 전용). E_BASE_URL이 있으면 HTTP, 없으면 mock을 쓴다."""
import os
from typing import Protocol

import httpx

from .domain import PaymentMethod
from .errors import AppError

MOCK_USERS = {
    "U001": [
        PaymentMethod("CARD001", "PRD-CARD-A", "CARD", "요기요 신한카드 체크",
                      monthly_performance_krw=350000),
        PaymentMethod("CARD002", "PRD-CARD-B", "CARD", "신한카드 구독 좋아요",
                      monthly_performance_krw=120000),
        PaymentMethod("LOCAL001", "PRD-LOCAL-A", "LOCAL_CURRENCY", "서울페이(서울사랑상품권)", balance_krw=120000),
    ],
    "U002": [
        PaymentMethod("CARD101", "PRD-CARD-A", "CARD", "요기요 신한카드 체크",
                      monthly_performance_krw=100000),
        PaymentMethod("LOCAL101", "PRD-LOCAL-A", "LOCAL_CURRENCY", "서울페이(서울사랑상품권)", balance_krw=1000),
    ],
}


class Ledger(Protocol):
    def get_payment_methods(self, user_id: str, request_id: str | None = None) -> list[PaymentMethod]: ...


class MockLedger:
    def get_payment_methods(self, user_id, request_id=None):
        if user_id not in MOCK_USERS:
            raise AppError(404, "USER_NOT_FOUND", "사용자를 찾을 수 없습니다.")
        return list(MOCK_USERS[user_id])


class HttpLedger:
    def __init__(self, base_url: str, token: str, timeout: float = 3.0, transport=None):
        self.client = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)
        self.token = token

    def get_payment_methods(self, user_id, request_id=None):
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            r = self.client.get("/internal/payment-methods", params={"user_id": user_id}, headers=headers)
        except httpx.HTTPError as e:
            raise AppError(503, "DEPENDENCY_UNAVAILABLE", "결제 원장(E)에 연결할 수 없습니다.", {"cause": type(e).__name__})
        if r.status_code == 404:
            raise AppError(404, "USER_NOT_FOUND", "사용자를 찾을 수 없습니다.")
        if r.status_code != 200:
            raise AppError(503, "DEPENDENCY_UNAVAILABLE", "결제 원장(E)이 오류를 반환했습니다.", {"status": r.status_code})
        return [PaymentMethod(m["payment_method_id"], m["product_id"], m["type"], m.get("name", ""),
                              m.get("balance_krw", 0), m.get("monthly_performance_krw", 0), m.get("status", "ACTIVE"))
                for m in r.json()["results"]]


_ledger: Ledger | None = None


def get_ledger() -> Ledger:
    global _ledger
    if _ledger is None:
        base = os.environ.get("E_BASE_URL")
        _ledger = HttpLedger(base, os.environ.get("INTERNAL_SERVICE_TOKEN", "dev-service-token")) if base else MockLedger()
    return _ledger


def set_ledger(ledger: Ledger | None) -> None:
    """테스트나 설정에서 원장 구현을 바꾼다."""
    global _ledger
    _ledger = ledger
