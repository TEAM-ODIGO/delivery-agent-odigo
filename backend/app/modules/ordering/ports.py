"""B가 부르는 다른 모듈 (명세에서 허용된 두 가지뿐).

- B-05 → C-05 혜택 계산 (계산만)
- B-07 → D-04 승인 상태 조회 (읽기 전용. 승인 '사용' 처리는 E → D-06만)

단독 실행: HTTP로 C·D 서버를 부른다 (C_BENEFIT_URL, D_DELEGATION_URL).
통합 서버: 같은 프로세스의 C·D 함수를 바로 부르는 구현을 configure()로 끼운다.

실패는 두 종류로만 나눈다.
- DependencyUnavailable: 연결·시간 초과·5xx → B는 503 (재시도 가능)
- DependencyRejected: 상대가 4xx로 거절 (예: 결제수단 없음, 승인 없음) → B가 업무 규칙으로 판단
"""

from typing import Protocol

import httpx

from app.modules.ordering.config import get_settings


class DependencyUnavailable(Exception):
    pass


class DependencyRejected(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class BenefitCalculator(Protocol):
    def calculate(self, body: dict) -> dict:
        """C-05 요청 dict → 응답 dict (payment_method_id, eligible, benefit_rule_version,
        instant_discount_krw, charge_krw, applied_benefits, reason_codes)"""


class ApprovalReader(Protocol):
    def get_approval(self, approval_id: str) -> dict:
        """D-04 응답 dict (approval_id, approval_target_id, order_version, quote_id, status,
        execution_id, expires_at, proposal_hash …)"""


# ---------------------------------------------------------------- HTTP 구현 (단독 실행)
def _http_call(method: str, url: str, request_id: str | None, json: dict | None = None) -> dict:
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.service_token}", "Accept": "application/json"}
    if request_id:
        headers["X-Request-ID"] = request_id
    try:
        response = httpx.request(method, url, json=json, headers=headers, timeout=settings.http_timeout_seconds)
    except httpx.HTTPError as error:
        raise DependencyUnavailable(str(error)) from error

    if response.status_code >= 500:
        raise DependencyUnavailable(f"{url} → {response.status_code}")
    if response.status_code >= 400:
        code = "UNKNOWN"
        message = response.text[:200]
        try:
            error = response.json().get("error") or {}
            code = error.get("code", code)
            message = error.get("message", message)
        except ValueError:
            pass
        raise DependencyRejected(response.status_code, code, message)
    try:
        return response.json()
    except ValueError as error:
        raise DependencyUnavailable("JSON이 아닌 응답") from error


class HttpBenefitCalculator:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def calculate(self, body: dict) -> dict:
        return _http_call("POST", f"{self.base_url}/benefit/calculations", body.get("request_id"), body)


class HttpApprovalReader:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def get_approval(self, approval_id: str) -> dict:
        return _http_call("GET", f"{self.base_url}/delegation/approvals/{approval_id}", None)


class NotConfigured:
    """주소를 설정하지 않았을 때: 항상 '연결 안 됨'(503). 실패를 성공처럼 꾸미지 않는다."""

    def calculate(self, body: dict) -> dict:
        raise DependencyUnavailable("C_BENEFIT_URL이 설정되지 않았습니다.")

    def get_approval(self, approval_id: str) -> dict:
        raise DependencyUnavailable("D_DELEGATION_URL이 설정되지 않았습니다.")


# ---------------------------------------------------------------- 고르기
_benefit: BenefitCalculator | None = None
_approvals: ApprovalReader | None = None


def configure(benefit: BenefitCalculator | None = None, approvals: ApprovalReader | None = None) -> None:
    """통합 서버·테스트에서 C·D 구현을 바꿔 끼운다. None을 주면 설정값(HTTP) 기준으로 돌아간다."""
    global _benefit, _approvals
    _benefit = benefit
    _approvals = approvals


def benefit_calculator() -> BenefitCalculator:
    if _benefit is not None:
        return _benefit
    url = get_settings().c_benefit_url
    return HttpBenefitCalculator(url) if url else NotConfigured()


def approval_reader() -> ApprovalReader:
    if _approvals is not None:
        return _approvals
    url = get_settings().d_delegation_url
    return HttpApprovalReader(url) if url else NotConfigured()
