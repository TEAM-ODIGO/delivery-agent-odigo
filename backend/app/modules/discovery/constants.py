"""A 모듈에서 쓰는 상태 이름, 필수 조건, 음식 종류 사전."""

# 주문 요청 상태
NEED_CLARIFICATION = "NEED_CLARIFICATION"
CONFIRMED = "CONFIRMED"
RECOMMENDED = "RECOMMENDED"
SELECTED = "SELECTED"

# 조건 출처 (조건 이력 source)
SOURCE_LLM = "LLM"
SOURCE_RULE = "RULE"
SOURCE_USER = "USER"

# 필수 조건: 비어 있으면 재질문한다 (delivery_address는 O-01에서 받으므로 재질문 대상 아님)
REQUIRED_SLOTS = ["people", "budget_krw"]

CLARIFICATION_TEMPLATES = {
    "people": {
        "slot": "people",
        "question": "몇 명이 드실 건가요?",
        "input_type": "SELECT",
        "options": [1, 2, 3, 4],
        "required": True,
    },
    "budget_krw": {
        "slot": "budget_krw",
        "question": "예산은 얼마까지인가요?",
        "input_type": "TEXT",
        "options": [],
        "required": True,
    },
}

# '빨리' 같은 말을 숫자로 바꾸는 기본 규칙
DEFAULT_FAST_DELIVERY_MINUTES = 40

# 음식 종류(cuisine) 사전
#  - categories: 이 중 하나라도 요기요 카테고리에 있으면 그 종류의 가게로 본다
#  - menu_words: 비어 있지 않으면 메인 메뉴 이름에 이 단어가 있어야 담는다
#                (예: '피자/양식' 가게에 샌드위치·파스타만 파는 곳이 섞여 있어서)
#  - not_menu_words: menu_words가 있어도 이 단어가 있으면 담지 않는다 (치킨버거는 치킨이 아님)
#  - triggers: 사용자 문장에서 이 단어가 보이면 이 종류로 본다
CUISINES = {
    "치킨": {
        "categories": ["치킨"],
        "menu_words": ["치킨", "닭", "윙", "순살", "후라이드", "양념", "강정", "봉"],
        "not_menu_words": ["버거", "샌드위치", "랩", "샐러드", "너겟"],
        "triggers": ["치킨", "통닭", "닭강정", "후라이드", "양념치킨"],
    },
    "피자": {
        "categories": ["피자/양식"],
        "menu_words": ["피자"],
        "triggers": ["피자"],
    },
    "양식": {
        "categories": ["피자/양식"],
        "menu_words": [],
        "triggers": ["양식", "파스타", "스테이크", "리조또"],
    },
    "버거": {
        "categories": ["버거"],
        "menu_words": ["버거"],
        "triggers": ["버거", "햄버거"],
    },
    "중식": {
        "categories": ["중국집"],
        "menu_words": [],
        "triggers": ["중식", "중국집", "중국음식", "짜장", "짜장면", "자장면", "짬뽕", "탕수육", "마라탕", "마라"],
    },
    "일식": {
        "categories": ["일식/돈까스"],
        "menu_words": [],
        "triggers": ["일식", "돈까스", "돈가스", "라멘", "우동", "덮밥", "규동", "카츠"],
    },
    "회/초밥": {
        "categories": ["회/초밥"],
        "menu_words": [],
        "triggers": ["초밥", "스시", "회덮밥", "물회", "모듬회", "횟집", "연어", "육회", "사시미"],
    },
    "한식": {
        "categories": ["한식"],
        "menu_words": [],
        "triggers": ["한식", "국밥", "백반", "찌개", "김치찌개", "된장찌개", "비빔밥", "제육", "불고기", "삼계탕"],
    },
    "분식": {
        "categories": ["분식"],
        "menu_words": [],
        "triggers": ["분식", "떡볶이", "김밥", "라면", "순대", "튀김", "핫도그"],
    },
    "찜/탕": {
        "categories": ["찜/탕"],
        "menu_words": [],
        "triggers": ["찜닭", "아구찜", "해물찜", "감자탕", "부대찌개", "곱창전골", "해물탕"],
    },
    "족발/보쌈": {
        "categories": ["족발/보쌈"],
        "menu_words": [],
        "triggers": ["족발", "보쌈"],
    },
    "고기/구이": {
        "categories": ["고기/구이"],
        "menu_words": [],
        "triggers": ["고기", "삼겹살", "구이", "곱창", "막창"],
    },
    "도시락/죽": {
        "categories": ["도시락/죽"],
        "menu_words": [],
        "triggers": ["도시락", "전복죽", "야채죽", "호박죽", "본죽"],
    },
    "샐러드": {
        "categories": ["샐러드"],
        "menu_words": [],
        "triggers": ["샐러드", "포케"],
    },
    "샌드위치": {
        "categories": ["샌드위치"],
        "menu_words": [],
        "triggers": ["샌드위치", "써브웨이", "서브웨이"],
    },
    "아시안": {
        "categories": ["아시안"],
        "menu_words": [],
        "triggers": ["아시안", "쌀국수", "커리", "카레", "케밥", "타코"],
    },
    "카페/디저트": {
        "categories": ["카페/디저트"],
        "menu_words": [],
        "triggers": ["디저트", "카페", "커피", "케이크", "빵집"],
    },
}

# 문장에 있으면 메뉴 이름 검색어로도 쓰는 구체적인 음식 이름 (음식 종류 이름 자체는 제외)
DISH_KEYWORDS = [
    "짜장", "짬뽕", "탕수육", "마라탕", "떡볶이", "김밥", "라면", "순대", "돈까스", "돈가스", "라멘", "우동",
    "초밥", "연어", "육회", "국밥", "김치찌개", "된장찌개", "부대찌개", "비빔밥", "제육", "불고기", "삼계탕",
    "찜닭", "닭강정", "족발", "보쌈", "삼겹살", "곱창", "파스타", "스테이크", "덮밥", "쌀국수", "커리", "카레",
    "케밥", "타코", "냉면", "칼국수", "만두", "핫도그", "포케", "후라이드", "양념치킨",
]

# 맵기 정보가 없는 메뉴를 '안 매운' 조건에서 걸러 낼 때 쓰는 단어
SPICY_NAME_WORDS = ["매운", "매콤", "불닭", "마라", "땡초", "청양", "얼큰", "짬뽕", "핵불", "화끈", "디진다", "엽기"]
