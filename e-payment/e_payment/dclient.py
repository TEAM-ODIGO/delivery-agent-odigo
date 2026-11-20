"""E → D-06 (결제 직전 검증). 모놀리스의 D를 HTTP로 부른다.

응답이 없거나 오류면 예외 → E는 결제하지 않는다 (fail-closed, 명세 E-01 ②).
allowed: true일 때만 결제가 진행되고, 이때 D는 그 승인을 '사용됨'으로 바꾼다.
"""

from typing import Any, Protocol

import httpx

D06_FIELDS = (
    "execution_id",
    "approval_id",
    "quote_id",
    "proposal_hash",
    "charge_krw",
    "payment_method_id",
)
D06_SPLIT_FIELDS = ("local_currency_payment_method_id", "local_currency_charge_krw")  # 분할결제(로컬 확장)


class DClient(Protocol):
    def check(self, body: dict[str, Any], request_id: str) -> dict[str, Any]: ...


class HttpDClient:
    def __init__(self, base_url: str, token: str, timeout: float = 3.0, client: httpx.Client | None = None):
        self.http = client or httpx.Client(base_url=base_url, timeout=timeout)
        self.token = token

    def check(self, body: dict[str, Any], request_id: str) -> dict[str, Any]:
        r = self.http.post(
            "/delegation/authorizations/check",
            json={k: body[k] for k in (*D06_FIELDS, *D06_SPLIT_FIELDS) if body.get(k) is not None},
            headers={"Authorization": f"Bearer {self.token}", "X-Request-ID": request_id},
        )
        r.raise_for_status()
        return r.json()
