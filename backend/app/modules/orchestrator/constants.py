"""O 모듈의 고정 값: 주문 상태, 사용자 응답 종류, 기록 종류."""

from enum import StrEnum


class RunState(StrEnum):
    RECEIVED = "RECEIVED"
    NEED_CLARIFICATION = "NEED_CLARIFICATION"  # 사용자 대기: 부족한 조건 선택·입력
    CANDIDATES_READY = "CANDIDATES_READY"  # 사용자 대기: 후보 선택
    ORDER_BUILT = "ORDER_BUILT"
    PAYMENT_METHOD_SELECTION = "PAYMENT_METHOD_SELECTION"  # 사용자 대기: 결제수단 선택
    QUOTE_CONFIRMED = "QUOTE_CONFIRMED"
    VALIDATING = "VALIDATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"  # 사용자 대기: 승인
    PAYING = "PAYING"
    PAYMENT_UNKNOWN = "PAYMENT_UNKNOWN"  # 결제 응답 없음·미확정 → E-03으로 확인 (재결제 금지)
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"  # D(또는 B)가 막음
    FAILED = "FAILED"  # 처리 불가 (후보 없음, 결제 거절, 서비스 장애)
    CANCELLED = "CANCELLED"
    # 맡긴 일에 따라 ODIGO가 거기까지만 하고 넘긴 정상 종료 (로컬 확장, 이슈 #111)
    RECOMMENDED = "RECOMMENDED"  # '주문안 만들기'를 맡기지 않음: 추천까지만
    HANDED_OFF = "HANDED_OFF"  # '결제 요청'을 맡기지 않음: 주문안까지, 결제는 사용자가 요기요에서


class ReplyType(StrEnum):
    CLARIFICATION = "CLARIFICATION"
    SELECT_CANDIDATE = "SELECT_CANDIDATE"
    SELECT_PAYMENT_METHOD = "SELECT_PAYMENT_METHOD"
    APPROVE = "APPROVE"
    CANCEL = "CANCEL"
    RECOMMEND_AGAIN = "RECOMMEND_AGAIN"  # 로컬 확장 (이슈 #68): 후보 화면에서 다른 가게 추천받기
    REQUOTE_FROM_CART = "REQUOTE_FROM_CART"  # 로컬 확장 (이슈 #86): 요기요 장바구니 금액으로 재견적


# 사용자 대기 상태 → 그 상태에서 받을 수 있는 응답 (CANCEL은 모든 대기 상태에서 가능)
WAITING: dict[RunState, ReplyType] = {
    RunState.NEED_CLARIFICATION: ReplyType.CLARIFICATION,
    RunState.CANDIDATES_READY: ReplyType.SELECT_CANDIDATE,
    RunState.PAYMENT_METHOD_SELECTION: ReplyType.SELECT_PAYMENT_METHOD,
    RunState.AWAITING_APPROVAL: ReplyType.APPROVE,
}
# 결제가 시작된 뒤에는 취소할 수 없다 (결제 후에는 O-08 환불)
PAYMENT_STARTED = (RunState.PAYING, RunState.PAYMENT_UNKNOWN, RunState.COMPLETED)
FINAL = (
    RunState.COMPLETED, RunState.BLOCKED, RunState.FAILED, RunState.CANCELLED,
    RunState.RECOMMENDED, RunState.HANDED_OFF,
)
YOGIYO_STORE_URL = "https://www.yogiyo.co.kr/mobile/#/{restaurant_id}/"

# 화면 진행 표시 (step.current / total)
TOTAL_STEPS = 8
# 요기요 실시간 확인을 끈 경우(off) A에서 받아 보여 줄 후보 수 (켜져 있으면 LIVE_CHECK_TOP_N·WANT, 이슈 #109)
CANDIDATES_SHOWN = 5
STEPS: dict[RunState, tuple[int, str]] = {
    RunState.RECEIVED: (1, "요청 접수"),
    RunState.NEED_CLARIFICATION: (2, "조건 확인"),
    RunState.CANDIDATES_READY: (3, "후보 선택"),
    RunState.ORDER_BUILT: (4, "주문안 생성"),
    RunState.PAYMENT_METHOD_SELECTION: (4, "결제수단 선택"),
    RunState.QUOTE_CONFIRMED: (5, "견적 확정"),
    RunState.VALIDATING: (5, "주문 검증"),
    RunState.AWAITING_APPROVAL: (6, "승인 대기"),
    RunState.PAYING: (7, "결제 중"),
    RunState.PAYMENT_UNKNOWN: (7, "결제 확인 중"),
    RunState.COMPLETED: (8, "결제 완료"),
    RunState.BLOCKED: (8, "진행 차단"),
    RunState.FAILED: (8, "처리 실패"),
    RunState.CANCELLED: (8, "취소됨"),
    RunState.RECOMMENDED: (8, "추천 완료"),
    RunState.HANDED_OFF: (8, "주문안 전달"),
}


class RunEventType(StrEnum):
    STATE_CHANGED = "STATE_CHANGED"
    USER_REPLY = "USER_REPLY"
    SERVICE_CALL = "SERVICE_CALL"
    SERVICE_ERROR = "SERVICE_ERROR"
    PAYMENT_RESULT = "PAYMENT_RESULT"
    LIVE_CHECKED = "LIVE_CHECKED"  # 후보 선택 때 본 요기요 실시간 확인 결과


class SyncStatus(StrEnum):
    PENDING = "PENDING"  # B-08 전달 대기
    DONE = "DONE"


class FailCode(StrEnum):
    NO_CANDIDATE = "NO_CANDIDATE"
    NO_PAYMENT_METHOD = "NO_PAYMENT_METHOD"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_NOT_STARTED = "PAYMENT_NOT_STARTED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    REQUEST_REJECTED = "REQUEST_REJECTED"
