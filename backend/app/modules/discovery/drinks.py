"""커피·차·음료 주문 (이슈 #107).

'커피 2잔'처럼 음료를 시키면 카페/디저트 가게에서 그 음료를 담는다. 수집 메뉴에서 음료는 menu_type이
'drink'라 평소에는 담지 않는데(음료만 시키는 주문이 아니면 곁들임도 아님), 음료를 말했을 때만 담는다.

- 메뉴 단어(menu_keywords): 사용자가 말한 음료를 대표 이름으로 ('카페 라떼' → 카페라떼, '아아' → 아메리카노).
  '커피'·'차'·'음료'처럼 넓은 말은 그 계열 전체를 찾는다.
- 온도·디카페인('아이스', '따뜻한', '디카페인')은 메뉴 단어가 아니라 고를 때의 선호로만 쓴다 (search_prefs).
- 'N잔'은 인원(수량)으로 본다.
"""

import re

# 대표 이름 → 메뉴 이름에서 찾을 단어
COFFEE_WORDS = ["커피", "아메리카노", "라떼", "라테", "카푸치노", "카페모카", "모카", "마키아또", "마키아토", "콜드브루",
                "콜드 브루", "에스프레소", "플랫화이트", "아인슈페너", "더치", "디카페인"]
TEA_WORDS = ["녹차", "그린티", "말차", "홍차", "블랙티", "얼그레이", "밀크티", "아이스티", "유자차", "유자티", "레몬차",
             "레몬티", "자몽차", "자몽티", "청귤차", "생강차", "대추차", "쌍화차", "보이차", "자스민", "재스민", "캐모마일",
             "카모마일", "페퍼민트", "루이보스", "히비스커스", "허브티", "매실차", "복숭아티", "피치티", "얼그레이티"]
ADE_JUICE_WORDS = ["에이드", "주스", "쥬스", "착즙"]
BLENDED_WORDS = ["스무디", "프라페", "프라푸치노", "쉐이크", "셰이크"]
BUBBLE_WORDS = ["버블티", "타피오카", "흑당"]

DRINK_KEYWORDS: dict[str, list[str]] = {
    # 계열 (넓은 말)
    "커피": COFFEE_WORDS,
    "차": TEA_WORDS,
    "음료": COFFEE_WORDS + TEA_WORDS + ADE_JUICE_WORDS + BLENDED_WORDS + BUBBLE_WORDS,
    # 커피
    "아메리카노": ["아메리카노"],
    "카페라떼": ["카페라떼", "카페라테", "카페 라떼"],
    "바닐라라떼": ["바닐라라떼", "바닐라 라떼", "바닐라라테"],
    "라떼": ["라떼", "라테"],
    "녹차라떼": ["녹차라떼", "녹차 라떼", "말차라떼", "말차 라떼", "그린티라떼", "그린티 라떼"],
    "카푸치노": ["카푸치노"],
    "카페모카": ["카페모카", "모카"],
    "콜드브루": ["콜드브루", "콜드 브루", "더치"],
    "에스프레소": ["에스프레소"],
    "아인슈페너": ["아인슈페너"],
    # 차
    "녹차": ["녹차", "그린티", "말차"],
    "말차": ["말차", "녹차"],
    "홍차": ["홍차", "블랙티", "얼그레이"],
    "얼그레이": ["얼그레이"],
    "밀크티": ["밀크티"],
    "아이스티": ["아이스티"],
    "유자차": ["유자차", "유자티"],
    "레몬차": ["레몬차", "레몬티"],
    "자몽차": ["자몽차", "자몽티"],
    "생강차": ["생강차"],
    "허브티": ["허브티", "캐모마일", "카모마일", "페퍼민트", "루이보스", "히비스커스"],
    "캐모마일": ["캐모마일", "카모마일"],
    # 에이드·주스·스무디·버블티
    "에이드": ["에이드"],
    "레몬에이드": ["레몬에이드"],
    "자몽에이드": ["자몽에이드"],
    "청포도에이드": ["청포도에이드"],
    "주스": ["주스", "쥬스", "착즙"],
    "스무디": ["스무디"],
    "프라페": ["프라페", "프라푸치노"],
    "쉐이크": ["쉐이크", "셰이크"],
    "버블티": ["버블티", "타피오카", "흑당"],
}

# 사용자가 쓰는 말 → 대표 이름 (긴 말부터 찾고, 찾은 자리는 지워서 '카페라떼'를 '라떼'로 한 번 더 세지 않는다)
USER_WORDS: list[tuple[str, str]] = sorted(
    [
        ("아이스아메리카노", "아메리카노"), ("아이스 아메리카노", "아메리카노"), ("아아", "아메리카노"),
        ("뜨아", "아메리카노"), ("아메리카노", "아메리카노"), ("카페라떼", "카페라떼"), ("카페 라떼", "카페라떼"),
        ("카페라테", "카페라떼"), ("바닐라라떼", "바닐라라떼"), ("바닐라 라떼", "바닐라라떼"), ("라떼", "라떼"),
        ("라테", "라떼"), ("카푸치노", "카푸치노"), ("카페모카", "카페모카"), ("모카", "카페모카"), ("콜드브루", "콜드브루"),
        ("콜드 브루", "콜드브루"), ("더치커피", "콜드브루"), ("에스프레소", "에스프레소"), ("아인슈페너", "아인슈페너"),
        ("커피", "커피"),
        ("녹차라떼", "녹차라떼"), ("녹차 라떼", "녹차라떼"), ("말차라떼", "녹차라떼"), ("말차 라떼", "녹차라떼"),
        ("그린티라떼", "녹차라떼"), ("그린티 라떼", "녹차라떼"), ("녹차", "녹차"), ("그린티", "녹차"), ("말차", "말차"), ("홍차", "홍차"), ("얼그레이", "얼그레이"),
        ("밀크티", "밀크티"), ("아이스티", "아이스티"), ("유자차", "유자차"), ("유자티", "유자차"), ("레몬차", "레몬차"),
        ("레몬티", "레몬차"), ("자몽차", "자몽차"), ("자몽티", "자몽차"), ("생강차", "생강차"), ("허브티", "허브티"),
        ("캐모마일", "캐모마일"), ("카모마일", "캐모마일"),
        ("레몬에이드", "레몬에이드"), ("자몽에이드", "자몽에이드"), ("청포도에이드", "청포도에이드"), ("에이드", "에이드"),
        ("주스", "주스"), ("쥬스", "주스"), ("스무디", "스무디"), ("프라페", "프라페"), ("프라푸치노", "프라페"),
        ("쉐이크", "쉐이크"), ("셰이크", "쉐이크"), ("버블티", "버블티"), ("타피오카", "버블티"), ("흑당", "버블티"),
        ("음료수", "음료"), ("음료", "음료"), ("마실 거", "음료"), ("마실거", "음료"), ("마실 것", "음료"),
        ("마실것", "음료"), ("드링크", "음료"),
    ],
    key=lambda pair: -len(pair[0]),
)
# '차'·'티' 한 글자: 앞뒤가 띄어 있거나 수량·조사가 붙을 때만 ('자동차', '차가운'은 아님)
TEA_ALONE = re.compile(r"(?:^|[\s,])(?:따뜻한\s*|따듯한\s*|뜨거운\s*|시원한\s*|차가운\s*)?(?:차|티)(?=$|[\s,.!?]|\d|한|두|세|네|를|랑|하고|좀|하나|도)")
# 지우고 찾는 말 (음료가 아닌데 음료 단어가 들어 있는 것)
NOT_DRINK_IN_TEXT = ["아이스크림", "아이스 크림", "녹차빙수", "말차빙수", "커피빈"]

CUPS = re.compile(r"(\d+)\s*(?:잔|개|컵)")
CUPS_WORD = re.compile(r"(한|두|세|네|다섯|여섯)\s*(?:잔|개|컵)|(하나)")
CUPS_WORDS = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "하나": 1}

ICE_IN_TEXT = ["아이스", "아아", "차가운", "시원한", "시원하게", "ice", "ICE"]
HOT_IN_TEXT = ["뜨아", "따뜻한", "따뜻하게", "따듯한", "뜨거운", "뜨겁게", "hot", "HOT"]
DECAF_IN_TEXT = ["디카페인", "디카"]

ICE_IN_NAME = ["ICE", "Ice", "ice", "아이스", "(I)", "Iced", "ICED"]
HOT_IN_NAME = ["HOT", "Hot", "(H)", "따뜻한", "뜨거운"]
# 음료 단어가 들어 있어도 음료가 아닌 메뉴 (녹차 아이스크림, 커피번, 말차 케이크 …)
NOT_DRINK_IN_NAME = ["아이스크림", "빙수", "케이크", "케익", "쿠키", "마카롱", "와플", "크로플", "빵", "롤케",
                     "젤라또", "젤라토", "푸딩", "티라미수", "초콜릿", "파운드", "스콘", "머핀", "베이글", "아이스모찌",
                     "파인트", "레디팩", "블록팩", "핸드팩", "쿼터"]
# 메뉴 분류에 이런 말이 있으면 음료만 따로 시킬 수 없다 ('음료 메뉴 (메인없는 주문은 불가합니다)')
NOT_ALONE_IN_CATEGORY = ["메인없는", "메인 없는", "단독", "메인메뉴와", "메인 메뉴와", "추가 주문", "추가주문"]
SODA_WORDS = ["콜라", "사이다", "제로", "펩시", "환타", "스프라이트", "생수", "탄산수", "토닉"]
TEA_ENDING = re.compile(r"(?:차|티)(?:$|[\s()\[\]/,.]|\d|[A-Za-z])")


def is_drink_keyword(word: str) -> bool:
    return word in DRINK_KEYWORDS


def is_drink_request(constraints: dict) -> bool:
    return any(is_drink_keyword(k) for k in constraints.get("menu_keywords") or [])


def _blank(text: str, words: list[str]) -> str:
    for word in words:
        text = text.replace(word, " " * len(word))
    return text


def find_drink_keywords(text: str) -> list[str]:
    """문장에 나온 음료를 대표 이름으로. 같은 음료를 두 번 세지 않는다."""
    rest = _blank(text, NOT_DRINK_IN_TEXT)
    found: list[str] = []
    for word, keyword in USER_WORDS:
        if word in rest:
            rest = rest.replace(word, " " * len(word))
            if keyword not in found:
                found.append(keyword)
    if TEA_ALONE.search(rest) and "차" not in found and not any(k in found for k in ("녹차", "홍차", "밀크티")):
        found.append("차")
    # 넓은 말은 구체적인 음료가 같이 있으면 뺀다 ('커피 중에 라떼' → 라떼)
    specific = [k for k in found if k not in ("커피", "차", "음료")]
    if specific:
        found = [k for k in found if k not in ("음료",)]
        if any(k in DRINK_KEYWORDS["커피"] or k in ("카페라떼", "바닐라라떼", "카페모카", "콜드브루") for k in specific):
            found = [k for k in found if k != "커피"]
    return found


def find_cups(text: str) -> int | None:
    """'커피 2잔', '라떼 두 잔', '밀크티 2개', '녹차라떼 하나' → 수량 (음료를 말했을 때만 부른다)"""
    match = CUPS.search(text)
    if match and 1 <= int(match.group(1)) <= 20:
        return int(match.group(1))
    match = CUPS_WORD.search(text)
    if match:
        return CUPS_WORDS[match.group(1) or match.group(2)]
    return None


def find_prefs(text: str) -> list[str]:
    """고를 때 쓸 선호: ICE · HOT · DECAF (음료를 말했을 때만 의미가 있다)."""
    rest = _blank(text, NOT_DRINK_IN_TEXT + ["아이스티"])
    prefs = []
    if any(word in rest for word in HOT_IN_TEXT):
        prefs.append("HOT")
    elif any(word in rest for word in ICE_IN_TEXT):
        prefs.append("ICE")
    if any(word in text for word in DECAF_IN_TEXT):
        prefs.append("DECAF")
    return prefs


def search_prefs(constraints: dict, message: str) -> list[str]:
    return find_prefs(message) if is_drink_request(constraints) else []


# '라떼'라고 하면 보통 카페라떼다. 커피가 안 들어간 라떼(딸기라떼·고구마라떼 …)는 '커피'·'라떼'로 치지 않는다
NON_COFFEE_LATTE = ["딸기", "고구마", "초코", "초콜릿", "녹차", "말차", "그린티", "곡물", "오곡", "미숫가루", "흑임자",
                    "밀크티", "단호박", "바나나", "쑥", "블루베리", "망고", "인절미", "콩", "홍차", "얼그레이", "자색",
                    "옥수수", "밤", "레드벨벳", "민트", "로얄", "타로"]
COFFEE_SURE = ["커피", "아메리카노", "에스프레소", "카페라떼", "카페라테", "바닐라라떼", "카라멜", "카페모카", "콜드브루",
               "카푸치노", "디카페인", "샷"]


def _non_coffee_latte(name: str) -> bool:
    return ("라떼" in name or "라테" in name) and any(w in name for w in NON_COFFEE_LATTE) and not any(
        w in name for w in COFFEE_SURE
    )


def menu_matches(name: str, menu_type: str | None, keyword: str) -> bool:
    """메뉴가 그 음료인지. menu_type을 알면('drink') '레몬티'·'청귤차'처럼 사전에 없는 차도 찾는다."""
    words = DRINK_KEYWORDS.get(keyword)
    if words is None:
        return False
    if any(word in name for word in NOT_DRINK_IN_NAME) and menu_type != "drink":
        return False
    if keyword == "음료" and menu_type == "drink":
        return not any(word in name for word in SODA_WORDS)
    if keyword in ("커피", "라떼") and _non_coffee_latte(name):
        return False
    if any(word in name for word in words):
        return True
    if keyword in ("차", "음료") and menu_type == "drink":
        return TEA_ENDING.search(name) is not None
    return False


def can_order_alone(menu_category: str | None) -> bool:
    return not any(word in (menu_category or "") for word in NOT_ALONE_IN_CATEGORY)


def pref_bonus(name: str, prefs: list[str]) -> float:
    """온도·디카페인 선호에 맞는 메뉴를 먼저 고르게 하는 점수."""
    bonus = 0.0
    iced = any(word in name for word in ICE_IN_NAME) and "아이스크림" not in name
    hot = any(word in name for word in HOT_IN_NAME)
    if "ICE" in prefs:
        bonus += 2.0 if iced else (-1.5 if hot else 0.0)
    if "HOT" in prefs:
        bonus += 2.0 if hot else (-2.0 if iced else 0.5)
    if "DECAF" in prefs:
        bonus += 3.0 if "디카페인" in name else -1.0
    elif "디카페인" in name:
        bonus -= 0.5  # 말하지 않았으면 일반 커피를 먼저
    return bonus


def split_cups(cups: int, kinds: int) -> list[int]:
    """'아메리카노랑 레몬에이드 3잔' → [2, 1]. 종류마다 적어도 1잔."""
    if kinds <= 0:
        return []
    base, extra = divmod(max(cups, kinds), kinds)
    return [base + (1 if i < extra else 0) for i in range(kinds)]
