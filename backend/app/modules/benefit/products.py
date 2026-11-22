"""카드 혜택 화면(로컬 확장, 팀 합의 전)에 보여 줄 상품 정보.

가상카드 A·B·C에 매핑한 신한카드 3장의 공식 안내 페이지 내용을 사람이 정리했다 (2026-11-22 확인).
- 결제 금액 계산은 여기 값을 쓰지 않는다. 계산은 seed.RULE_VERSIONS의 규칙(BenefitRule)만 쓴다.
- tiers: 전월 이용금액 구간별 '배달 할인이 들어가는 한도'. 카드 혜택 화면의 "다음 혜택까지 N원"에 쓴다.
- 연회비·혜택은 바뀔 수 있으니 실제 발급 전에는 상품설명서를 다시 확인한다.
"""

_SHINHAN = "https://www.shinhancard.com/pconts/html/card/apply"
CHECKED_AT = "2026-11-22"

CARD_PRODUCTS: dict[str, dict] = {
    "PRD-CARD-A": {
        "product_name": "요기요 신한카드 체크",
        "card_kind": "체크카드",
        "annual_fee": "공식 페이지에 금액 표기 없음 (체크카드)",
        "issuance": "2021.02.02 신규 발급 중단 (기존 회원 재발급만 가능)",
        "headline": "요기요 결제금액 15% 할인",
        "performance_basis": "전월 이용금액 (이 카드 기준)",
        "tier_label": "통합할인한도",
        "tiers": [
            {"min_krw": 200_000, "cap_krw": 6_000},
            {"min_krw": 500_000, "cap_krw": 8_000},
            {"min_krw": 800_000, "cap_krw": 10_000},
            {"min_krw": 1_000_000, "cap_krw": 15_000},
        ],
        "benefits": [
            {"title": "요기요", "rate": "15% 할인", "delivery": True,
             "detail": "일 1회·월 4회, 하루 최대 2,000원"},
            {"title": "이디야", "rate": "10% 할인", "delivery": False,
             "detail": "건당 7,000원 이상, 일 1회·월 3회, 월 최대 3,000원"},
            {"title": "CU", "rate": "10% 할인", "delivery": False,
             "detail": "건당 10,000원 이상, 일 1회·월 3회, 월 최대 5,000원"},
        ],
        "notes": [
            "할인은 결제일에 결제계좌로 캐시백돼요.",
            "할인 금액·수수료·기프트카드 구매·선불카드 충전·취소 거래는 실적에서 빠져요.",
        ],
        "source_url": f"{_SHINHAN}/check/1188364_2206.html",
    },
    "PRD-CARD-B": {
        "product_name": "신한카드 구독 좋아요",
        "card_kind": "신용카드",
        "annual_fee": "국내전용 15,000원 · 해외겸용(VISA) 18,000원",
        "issuance": "신규 발급·유효기간 연장 중단 (기존 회원 재발급만 가능)",
        "headline": "배달앱 10% 할인 · 디지털 구독 100% 할인",
        "performance_basis": "전월 이용금액 (일시불+할부)",
        "tier_label": "생활서비스 월 한도",
        "tiers": [
            {"min_krw": 400_000, "cap_krw": 2_000},
            {"min_krw": 800_000, "cap_krw": 4_000},
        ],
        "benefits": [
            {"title": "생활서비스", "rate": "10% 할인", "delivery": True,
             "detail": "배달앱(배달의민족·요기요·쿠팡이츠·땡겨요) 앱 내 카드결제, GS25·CU, 다이소, 올리브영, "
                       "스타벅스 자동충전 · 월 2,000원 / 4,000원"},
            {"title": "디지털 구독", "rate": "100% 할인", "delivery": False,
             "detail": "OTT·음원·전자책·멤버십 정기결제 · 월 12,000원 / 20,000원"},
            {"title": "정기결제 자동이체", "rate": "건당 1,000원", "delivery": False,
             "detail": "통신요금·아파트관리비·도시가스·전기요금 · 월 1,000원 / 2,000원"},
        ],
        "notes": [
            "한도는 전월 40만원 이상 / 80만원 이상 구간 기준이에요.",
            "간편결제로 결제해 가맹점명이 페이 이름으로 찍히면 할인에서 빠져요.",
        ],
        "source_url": f"{_SHINHAN}/credit/1220057_2207.html",
    },
    "PRD-CARD-C": {
        "product_name": "신한카드 Deep On Platinum+",
        "card_kind": "신용카드",
        "annual_fee": "국내(UPI) 30,000원 · 해외겸용(Master) 33,000원",
        "issuance": None,
        "headline": "간편결제 10% + 요기요 추가 10% (최대 20%)",
        "performance_basis": "전월 이용금액 (일시불+할부, 가족카드 합산)",
        "tier_label": "간편결제 월 한도",
        "tiers": [
            {"min_krw": 300_000, "cap_krw": 10_000},
            {"min_krw": 600_000, "cap_krw": 20_000},
            {"min_krw": 900_000, "cap_krw": 30_000},
            {"min_krw": 1_200_000, "cap_krw": 40_000},
        ],
        "benefits": [
            {"title": "온라인 배달·슈퍼", "rate": "최대 20% 할인", "delivery": True,
             "detail": "간편결제로 요기요·GS프레시·오아시스마켓 결제 · 1회 2만원 이상, 최대 1만원, 월 6회"},
            {"title": "간편결제(Pay)", "rate": "10% 할인", "delivery": False,
             "detail": "10개 간편결제로 국내 온라인 결제 · 1회 2만원 이상, 최대 5,000원"},
            {"title": "생활", "rate": "20% 할인", "delivery": False,
             "detail": "4대 편의점, 올리브영·다이소, 스타벅스·투썸, 월납 · 영역별 월 4회, 최대 1만원"},
            {"title": "항공", "rate": "10% 할인", "delivery": False,
             "detail": "제주항공·에어부산 공식 채널 · 최대 3만원, 월 1회·연 3회"},
        ],
        "notes": [
            "생활 20% 할인은 간편결제 한도와 따로 월 1만~3만원 한도가 있어요.",
            "할인 전 금액은 1회 5만원까지만 인정돼요.",
        ],
        "source_url": f"{_SHINHAN}/credit/1188280_2207.html",
    },
    "PRD-LOCAL-A": {
        "product_name": "가상 지역화폐",
        "card_kind": "지역화폐",
        "annual_fee": None,
        "issuance": None,
        "headline": "가맹점 결제 5% 즉시할인",
        "performance_basis": None,
        "tier_label": None,
        "tiers": [],
        "benefits": [
            {"title": "지역화폐 가맹점", "rate": "5% 즉시할인", "delivery": True,
             "detail": "지역화폐 가맹점 배달 주문 · 월 최대 15,000원"},
        ],
        "notes": ["시연용 가상 상품이에요."],
        "source_url": None,
    },
}


def tier_progress(product: dict, performance_krw: int) -> dict:
    """지금 실적이 어느 구간인지, 다음 구간까지 얼마 남았는지 계산한다."""
    tiers = sorted(product.get("tiers") or [], key=lambda t: t["min_krw"])
    if not tiers:
        return {"current": None, "next": None, "to_next_krw": None, "message": None}
    current = next((t for t in reversed(tiers) if performance_krw >= t["min_krw"]), None)
    upcoming = next((t for t in tiers if performance_krw < t["min_krw"]), None)
    to_next = upcoming["min_krw"] - performance_krw if upcoming else None
    if current is None:
        message = f"{to_next:,}원 더 쓰면 배달 할인을 받을 수 있어요"
    elif upcoming is None:
        message = "가장 높은 혜택 구간이에요"
    else:
        message = f"다음 혜택까지 {to_next:,}원 남았어요"
    return {"current": current, "next": upcoming, "to_next_krw": to_next, "message": message}
