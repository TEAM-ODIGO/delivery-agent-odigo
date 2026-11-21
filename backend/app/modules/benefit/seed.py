"""MVP 1 시드 데이터 (명세 예시 값과 같음). 이후 DB(c_ 접두어 테이블)로 교체한다.
- 혜택 규칙은 상품 설명서에서 뽑아 사람이 검토한 것만 ACTIVE로 넣는다.
- 식당 지역화폐 여부는 실제로는 수집 DB의 restaurant.local_currency_* 열을 읽는다."""
from .domain import BenefitRule, Restaurant

CURRENT_VERSION = "BEN-V1"

_SHINHAN = "https://www.shinhancard.com/pconts/html/card/apply"

# 출처는 신한카드 공식 카드 안내 페이지(2026-11-20 확인).
# 규칙 모델로 표현하지 못한 조건(횟수 제한 등)은 evidence에 적어 둔다.
SOURCES = {
    "RULE-CARD-A-DELIVERY": {
        "document_id": "DOC-PRD-CARD-A-2026",
        "title": "요기요 신한카드 체크 카드 안내 (신한카드 공식)",
        "page": 1,
        "evidence": (
            "배달앱(요기요) 결제 시 15% 할인(하루 1회·건당 최대 2,000원, 전월 실적 20만원 이상, "
            "월 한도 6,000원). 월 횟수 제한은 규칙 모델에 없어 반영하지 않음. 신규 발급 중단 카드. "
            f"{_SHINHAN}/check/1188364_2206.html"
        ),
    },
    "RULE-CARD-B-RATE": {
        "document_id": "DOC-PRD-CARD-B-2026",
        "title": "신한카드 구독 좋아요 카드 안내 (신한카드 공식)",
        "page": 1,
        "evidence": (
            "배달앱 앱 내 카드결제 시 10% 할인(전월 실적 40만원 이상, 월 한도 2,000원). "
            "전월 실적 구간별 한도 중 첫 구간만 반영. 신규 발급 중단 카드. "
            f"{_SHINHAN}/credit/1220057_2207.html"
        ),
    },
    "RULE-CARD-C-DELIVERY": {
        "document_id": "DOC-PRD-CARD-C-2026",
        "title": "신한카드 Deep On Platinum+ 카드 안내 (신한카드 공식)",
        "page": 1,
        "evidence": (
            "간편결제 10% + 요기요 추가 10%, 최대 20% 할인(결제 2만원 이상, 건당 인정 5만원이라 "
            "배달 할인 최대 1만원, 월 6회, 전월 실적 30만원 이상). "
            "월 한도 10,000원은 시연용 가정이며 상품설명서 확인이 필요함. "
            "간편결제로 결제해야 한다는 조건과 월 횟수는 규칙 모델에 없어 "
            f"반영하지 않음. {_SHINHAN}/credit/1188280_2207.html"
        ),
    },
}

# 규칙 버전별 규칙 목록. 이전 버전도 남겨 진행 중인 추천·D 검증이 같은 버전으로 계산되게 한다.
RULE_VERSIONS: dict[str, list[BenefitRule]] = {
    "BEN-V1": [
        # 요기요 신한카드 체크: 15%(건당 최대 2천원), 최소 주문 없음, 전월 20만원, 월 6천원
        BenefitRule("RULE-CARD-A-DELIVERY", "PRD-CARD-A", discount_percent=15,
                    max_discount_krw=2000, min_performance_krw=200000, monthly_cap_krw=6000),
        # 신한카드 구독 좋아요: 10%(건당 상한 없음), 최소 주문 없음, 전월 40만원, 월 2천원
        BenefitRule("RULE-CARD-B-RATE", "PRD-CARD-B", discount_percent=10,
                    min_performance_krw=400000, monthly_cap_krw=2000),
        # 신한카드 Deep On Platinum+: 20%(건당 최대 1만원), 최소 주문 2만원,
        # 전월 30만원, 월 1만원(가정)
        BenefitRule("RULE-CARD-C-DELIVERY", "PRD-CARD-C", discount_percent=20,
                    max_discount_krw=10000, min_order_krw=20000, min_performance_krw=300000,
                    monthly_cap_krw=10000),
        BenefitRule("RULE-LOCAL-A-DISCOUNT", "PRD-LOCAL-A", discount_percent=5, monthly_cap_krw=15000),
    ],
}

RESTAURANTS = {
    "STORE101": Restaurant("STORE101", True, True, "ASSUMED_FROM_VOUCHER", None,
                           "가상치킨 강남점", "서울특별시 강남구 테헤란로 123", 37.5001, 127.0362, "11680"),
    "STORE102": Restaurant("STORE102", False, False, "ASSUMED_FROM_VOUCHER", None,
                           "가상피자 역삼점", "서울특별시 강남구 역삼로 45", 37.4998, 127.0330, "11680"),
    "STORE103": Restaurant("STORE103", None, None, None, None,
                           "가상분식 서초점", "서울특별시 서초구 서초대로 77", 37.4920, 127.0100, "11650"),
}

# 시드 식당의 원본 (공용 식당 데이터로 바꿔 끼운 뒤에도 C 단독 테스트가 같은 값을 쓰게 보관)
DEFAULT_RESTAURANTS = dict(RESTAURANTS)

# 이번 달 이미 받은 혜택 금액. E-11 응답에는 이 값이 없어서 임시로 C에 둔다. (협의 필요)
BENEFIT_USED_KRW = {"CARD001": 2000, "CARD002": 0, "CARD003": 0, "LOCAL001": 0}

PRODUCT_TYPES = {"PRD-CARD-A": "CARD", "PRD-CARD-B": "CARD", "PRD-CARD-C": "CARD", "PRD-LOCAL-A": "LOCAL_CURRENCY"}


# ---------- 테스트·재시작용 초기 상태 ----------
import copy as _copy

_INITIAL = {"version": CURRENT_VERSION, "rules": _copy.deepcopy(RULE_VERSIONS),
            "sources": _copy.deepcopy(SOURCES), "restaurants": _copy.deepcopy(RESTAURANTS),
            "used": _copy.deepcopy(BENEFIT_USED_KRW)}


def reset() -> None:
    global CURRENT_VERSION
    CURRENT_VERSION = _INITIAL["version"]
    for target, key in ((RULE_VERSIONS, "rules"), (SOURCES, "sources"), (RESTAURANTS, "restaurants"),
                        (BENEFIT_USED_KRW, "used")):
        target.clear()
        target.update(_copy.deepcopy(_INITIAL[key]))
