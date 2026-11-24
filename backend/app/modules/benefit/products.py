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
    # 아래 세 장(D·E·F)은 "내 카드 추가"(로컬 확장)로 들어오는 다른 카드사 카드. 신한카드 3장과 달리 공식 페이지를 직접 확인하지 않고
    # 팀원이 가져온 카드 안내문을 그대로 정리했다 (2026-11-24). 연회비·발급 조건은 안내문에 없어 비워 뒀다.
    "PRD-CARD-D": {
        "product_name": "KB국민 노리2 체크카드 (KB Pay)",
        "card_kind": "체크카드",
        "annual_fee": None,
        "issuance": None,
        "headline": "요기요 건당 1만원 이상 결제 시 1,000원 할인",
        "performance_basis": "전월 이용실적",
        "tier_label": "배달 월 할인한도",
        "tiers": [
            {"min_krw": 200_000, "cap_krw": 1_000},
        ],
        "benefits": [
            {"title": "배달앱", "rate": "건당 1,000원 할인", "delivery": True,
             "detail": "배달의민족·요기요 · 건당 1만원 이상 · 전월 이용실적 20만원 이상 · 월 1회(월 한도 1,000원)"},
            {"title": "커피·모바일·공연", "rate": "10% 할인", "delivery": False,
             "detail": "매일 받는 일상 혜택"},
            {"title": "뷰티·편의점", "rate": "5% 할인", "delivery": False,
             "detail": "매일 받는 일상 혜택"},
            {"title": "KB Pay", "rate": "2% 추가 할인", "delivery": False,
             "detail": "KB Pay로 결제할 때 추가"},
        ],
        "notes": [
            "카드 안내문을 정리한 내용이에요. 실제 이용 전에는 카드사 상품설명서를 확인해 주세요.",
            "항목별 세부 조건(한도·횟수)은 안내문에 없어 적지 않았어요.",
        ],
        "source_url": None,
        "checked_at": "2026-11-24",
    },
    "PRD-CARD-E": {
        "product_name": "현대카드 ZERO Up (포인트형)",
        "card_kind": None,
        "annual_fee": None,
        "issuance": None,
        "headline": "온라인몰·대형마트·교육·주유·이동통신 요금 2.4% 적립",
        "performance_basis": None,
        "tier_label": None,
        "tiers": [],
        "benefits": [
            {"title": "온라인몰·대형마트·교육·주유·이동통신 요금", "rate": "2.4% 적립", "delivery": False,
             "detail": "포인트 적립형"},
            {"title": "국내외 가맹점", "rate": "1.2% 적립", "delivery": False,
             "detail": "포인트 적립형"},
        ],
        "notes": [
            "적립형 카드라 결제 금액이 바로 줄지 않아요. 그래서 배달 즉시할인 추천 계산에는 쓰이지 않아요.",
            "카드 안내문을 정리한 내용이에요. 실제 이용 전에는 카드사 상품설명서를 확인해 주세요.",
        ],
        "source_url": None,
        "checked_at": "2026-11-24",
    },
    "PRD-CARD-F": {
        "product_name": "요기요 삼성카드",
        "card_kind": None,
        "annual_fee": None,
        "issuance": None,
        "headline": "요기요 10% 결제일할인 (전월 이용금액 30만원 이상)",
        "performance_basis": "전월 이용금액",
        "tier_label": "요기요 월 할인한도",
        "tiers": [
            {"min_krw": 300_000, "cap_krw": 6_000},
            {"min_krw": 600_000, "cap_krw": 12_000},
            {"min_krw": 900_000, "cap_krw": 20_000},
        ],
        "benefits": [
            {"title": "요기요", "rate": "10% 결제일할인", "delivery": True,
             "detail": "전월 이용금액 30만원 이상 · 월 할인한도 6,000원(30만원 이상) · 12,000원(60만원 이상) · 20,000원(90만원 이상)"},
            {"title": "요기요 (전월 30만원 미만)", "rate": "1% 결제일할인", "delivery": True,
             "detail": "전월 이용금액 30만원 미만일 때 · 월 할인한도 5,000원"},
            {"title": "커피전문점·편의점·생활잡화", "rate": "5% 결제일할인", "delivery": False,
             "detail": "스타벅스·이디야·투썸플레이스 등 커피전문점, CU·GS25·세븐일레븐·미니스톱·이마트24, 다이소"
                       " · 전월 이용금액 30만원 이상 · 통합 월 할인한도 3,000원(30만원 이상) · 6,000원(60만원 이상) · 10,000원(90만원 이상)"},
        ],
        "notes": [
            "결제일할인이라 결제할 때가 아니라 카드 대금을 청구할 때 빠져요. 오디고는 같은 금액이 바로 줄어드는 것으로 보고 추천 순서를 정해요.",
            "발급월+1개월까지는 전월 이용금액이 30만원 미만이어도 30만~60만원 구간 혜택을 받아요 (60만원 이상이면 해당 구간 혜택).",
            "전월 이용금액 30만원 미만일 때의 1% 할인은 추천 계산에 넣지 않았어요. 월 한도도 가장 낮은 구간(6,000원)으로 계산해요.",
            "1% 할인 제외: 무이자할부, 삼성카드 할인이 적용된 일시불·할부 금액, 기프트·선불카드(전자지급수단 포함) 구매·충전, 상품권 구매.",
            "카드 안내문을 정리한 내용이에요. 실제 이용 전에는 카드사 상품설명서를 확인해 주세요.",
        ],
        "source_url": None,
        "checked_at": "2026-11-24",
    },
    "PRD-LOCAL-A": {
        "product_name": "서울페이(서울사랑상품권)",
        "card_kind": "지역화폐",
        "annual_fee": None,
        "issuance": None,
        "headline": "충전해 두고 쓴 만큼 차감돼요",
        "performance_basis": None,
        "tier_label": None,
        "tiers": [],
        "benefits": [
            {"title": "충전식 잔액", "rate": "쓴 만큼 차감", "delivery": True,
             "detail": "지역화폐 가맹점 배달 주문에서 충전한 잔액으로 결제해요 · 할인·적립은 없어요"},
        ],
        "notes": ["시연용 가상 상품이에요.", "잔액이 주문 금액보다 모자라면 결제수단 선택에서 빠져요."],
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
