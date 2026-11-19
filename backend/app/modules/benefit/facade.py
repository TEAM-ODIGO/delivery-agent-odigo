"""다른 모듈(O·B·D)이 C를 함수로 부르는 입구. 명세 요청·응답 dict를 그대로 주고받는다.

O → C-01 : recommend(body)                (RecommendationPort)
B·D → C-05 : calculate(body)              (CBenefitPort.calculate)
D → C-02 : list_payment_methods(user_id)  (CBenefitPort.list_methods)
O → (로컬 확장) check_min_order(body)     후보 목록에서 최소주문금액 미달을 미리 거른다 (읽기 전용)

업무 오류는 AppError(status, code, message, details)로 던진다 (팀 공통 규칙 '모듈 간 오류').
HTTP 입구(router.py)와 같은 service 함수를 쓰므로 결과가 같다.
"""
from typing import Any

from . import service
from .schemas import CalculationRequest, RecommendRequest


def recommend(body: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
    """C-01. Idempotency-Key 기본값은 명세대로 {request_id}:c01:{order_version}이지만,
    O가 order_version을 넘기지 않으면 키 없이 부른다 (같은 request_id는 SUPERSEDED로 구분)."""
    return service.recommend_payment_methods(RecommendRequest.model_validate(body), idempotency_key)


def calculate(body: dict[str, Any]) -> dict[str, Any]:
    """C-05. 계산만 하며 상태를 바꾸지 않는다."""
    # 명세 C-05 응답에는 mock: true가 있다 (HTTP 라우터와 같게 함수 호출에도 붙인다)
    return {**service.calculate_benefit(CalculationRequest.model_validate(body)), "mock": True}


def list_payment_methods(user_id: str) -> dict[str, Any]:
    """C-02."""
    return service.list_payment_methods(user_id)


def check_min_order(body: dict[str, Any]) -> dict[str, Any]:
    """로컬 확장(명세 밖). 읽기 전용이라 추천 기록을 남기지 않는다.

    body: {"user_id", "request_id"?, "orders": [{"restaurant_id", "order_krw"}],
           "candidate_payment_method_ids"?, "local_currency_only"?}
    → {"results": [{"restaurant_id", "order_krw", "required_order_krw"}]}
    """
    return service.check_min_order(
        body["user_id"], body.get("request_id"), body["orders"],
        body.get("candidate_payment_method_ids") or None, bool(body.get("local_currency_only")))
