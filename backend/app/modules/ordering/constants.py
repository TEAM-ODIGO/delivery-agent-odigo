"""B(주문) 모듈에서 쓰는 상태 이름과 다음 행동 이름.

명세서의 응답 예시에 나온 값을 그대로 쓴다. 문자열을 코드 곳곳에 직접 쓰지 않으려고 모아 두었다.
"""

from enum import StrEnum


class PlanStatus(StrEnum):
    OPEN = "OPEN"  # 진행 중 (견적·혜택·승인 대상을 만들 수 있음)
    PAID = "PAID"  # 결제 승인(APPROVED) 결과가 반영됨 → 더 바꿀 수 없음


class ValidationStatus(StrEnum):
    VALID = "VALID"


class QuoteStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"  # 유효시간이 지남 → 재검증(B-04, MVP 2) 필요
    USED = "USED"  # 이 견적으로 결제가 끝남


class BudgetBasis(StrEnum):
    POST_FINANCIAL = "POST_FINANCIAL"  # 금융 혜택(즉시할인) 뒤 결제액으로 예산 비교
    PRE_FINANCIAL = "PRE_FINANCIAL"  # 혜택 전 금액(메뉴 + 배달비)으로 예산 비교


class BudgetStatus(StrEnum):
    PENDING_BENEFIT = "PENDING_BENEFIT"  # 혜택을 아직 안 붙여서 비교 못 함
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_REQUIRED = "NOT_REQUIRED"  # 조건에 예산이 없음


class TargetStatus(StrEnum):
    PREPARED = "PREPARED"  # D-02로 보낼 준비 완료
    SUPERSEDED = "SUPERSEDED"  # 혜택이 바뀌어 새 승인 대상으로 대체됨
    PAID = "PAID"  # 이 대상으로 결제 완료


class PaymentStatus(StrEnum):
    """결제 상태 원본은 E. 이름도 E 기준."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"  # 응답 유실 → 새 결제로 재시도하지 않고 E 결과를 기다림


FINAL_PAYMENT_STATUSES = (PaymentStatus.APPROVED, PaymentStatus.FAILED)


class ApplicationStatus(StrEnum):
    """B-08: 받은 결제 결과를 주문안에 어떻게 반영했는지."""

    APPLIED = "APPLIED"  # 현재 승인 대상·견적에 반영
    RECORDED_STALE = "RECORDED_STALE"  # 지난 견적의 결과 → 기록만 하고 현재 상태는 안 바꿈
    IGNORED_AFTER_FINAL = "IGNORED_AFTER_FINAL"  # 이미 확정(APPROVED·FAILED)된 뒤 다른 결과 → 기록만


class NextAction(StrEnum):
    NONE = "NONE"
    CALCULATE_BENEFITS = "CALCULATE_BENEFITS"  # O → C-01 → B-05
    BUILD_APPROVAL_TARGET = "BUILD_APPROVAL_TARGET"  # O → B-06
    REQUEST_D_APPROVAL = "REQUEST_D_APPROVAL"  # O → D-02
    RESEARCH_REQUIRED = "RESEARCH_REQUIRED"  # O → 다른 후보 / A-07 재탐색
    REVALIDATE = "REVALIDATE"  # O → B-04 재검증 (MVP 2)
    CHECK_DATA = "CHECK_DATA"  # 의존 서비스·데이터 확인 후 재시도
    WAIT_PAYMENT_RESULT = "WAIT_PAYMENT_RESULT"  # E 결과(E-03)를 기다림


class Stage(StrEnum):
    MOCK_PAYMENT = "MOCK_PAYMENT"


# B-06: '지역화폐만' 조건인데 식당의 지역화폐 가맹 여부를 모를 때 D가 막도록 넣는 표시
UNVERIFIED_LOCAL_CURRENCY = "LOCAL_CURRENCY_MERCHANT_UNKNOWN"

MAX_QUANTITY = 99
