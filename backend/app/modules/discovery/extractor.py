"""규칙 기반 조건 추출기.

LLM(Ollama)이 꺼져 있거나 실패해도 주문 흐름이 멈추지 않도록 정규식·단어 사전으로 조건을 뽑는다.
LLM이 성공했을 때도 LLM이 놓친 항목을 채우는 데 쓴다.

결과는 (조건 dict, 해석 메모 dict) 이다.
 - 조건 dict: Constraints와 같은 키 (delivery_address 제외, 서비스에서 넣는다)
 - 해석 메모 dict: {조건 이름: "'빨리' → 배달예상 40분 이내 (기본 규칙)"} 처럼 어떤 말을 어떻게 바꿨는지
"""

import re

from app.modules.discovery.constants import (
    CUISINES,
    DEFAULT_FAST_DELIVERY_MINUTES,
    DISH_KEYWORDS,
    TASTE_ORDER,
    TASTES,
)

KOREAN_DIGITS = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
PEOPLE_WORDS = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4, "다섯": 5, "여섯": 6}

# ---------- 인원 ----------
PEOPLE_DIGIT = re.compile(r"(\d+)\s*(?:명|인분|인)(?!당)")
PEOPLE_WORD = re.compile(r"(한|두|세|네|다섯|여섯)\s*(?:명|사람)(?!당)")
PEOPLE_TOGETHER = re.compile(r"(둘|셋|넷|다섯|여섯)\s*(?:이서|이)")
PEOPLE_ALONE = re.compile(r"혼자|혼밥|나\s*혼자")

# ---------- 예산 ----------
PER_PERSON = re.compile(r"(?:1인당|인당|한\s*명당|한\s*사람당|1명당)\s*$")
# 만 앞뒤에 숫자·한글 숫자가 섞여도 읽는다: 2만5천원, 2만오천원, 만5천원, 이만오천원
AMOUNT_MAN = re.compile(r"(\d+(?:\.\d+)?)\s*만\s*(?:(\d|[일이삼사오육칠팔구])\s*천)?\s*원?")
AMOUNT_WON = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,8})\s*원")
AMOUNT_CHEON = re.compile(r"(\d+)\s*천\s*원")
AMOUNT_KOREAN_CHEON = re.compile(r"(?<![가-힣])([일이삼사오육칠팔구])\s*천\s*원")
AMOUNT_KOREAN = re.compile(r"(?<![가-힣\d])([일이삼사오육칠팔구])?\s*만\s*(?:(\d|[일이삼사오육칠팔구])\s*천)?\s*원")

# ---------- 맵기 ----------
NOT_SPICY = re.compile(
    r"안\s*맵|맵지\s*않|안\s*매운|순한|맵찔|매운\s*(?:거|걸|건|것|음식)\s*(?:은|는)?\s*(?:못|안)\s*먹|하나도\s*안\s*매"
    r"|매운\s*(?:거|걸|건|것|음식|맛)?\s*(?:은|는)?\s*(?:말고|빼고|싫|별로)"
)
LESS_SPICY = re.compile(r"덜\s*매운|덜\s*맵|조금만\s*매운|살짝\s*매운|약간\s*매운|적당히\s*매운")
WANT_SPICY = re.compile(r"매운|맵게|얼큰|매콤|칼칼|얼얼|화끈")

# ---------- 맛 취향 (매운맛은 위의 맵기 규칙으로 정한다) ----------
TASTE_TRIGGERS = {
    "SWEET": re.compile(r"달달|달콤|달짝|단\s*(?:거|게|걸|것|음식|맛)|디저트|후식|당\s*(?:땡|떨어|충전)"),
    "SOUP": re.compile(r"국물|뜨끈|따끈|뜨뜻|얼큰|해장|(?:시원|따뜻|뜨거운)한?\s*(?:국|거|음식|걸)"),
    "CRISPY": re.compile(r"바삭|튀긴|튀김|크리스피|겉바"),
    "LIGHT": re.compile(r"담백|가벼운|가볍게|건강한|건강식|깔끔한|다이어트|속\s*편한|자극적이지\s*않|안\s*느끼"),
    "CHEESY": re.compile(r"꾸덕|고소한|느끼한|크리미|치즈\s*(?:듬뿍|가득|많이)|진한\s*(?:거|맛|음식)"),
}
# '느끼한 건 싫어', '단 거 말고'처럼 뒤에서 부정하거나, '안 느끼한'처럼 앞에서 부정하면 그 맛은 원하지 않는 것
TASTE_NEGATION_AFTER = re.compile(
    r"^\s*(?:한|하지\s*않은)?\s*(?:거|걸|건|것|게|음식|맛)?\s*(?:은|는|이|가)?\s*(?:말고|빼고|싫|별로|제외|못\s*먹|안\s*먹|아닌)"
)
TASTE_NEGATION_BEFORE = re.compile(r"(?:안|덜)\s*$")

# ---------- 배달 시간 ----------
MINUTES_LIMIT = re.compile(r"(\d+)\s*분\s*(?:안|이내|내|까지|이하|만에)")
FAST_WORDS = re.compile(r"빨리|빠르게|빠른|급해|급하게|얼른|금방")

# ---------- 지역화폐 ----------
LOCAL_CURRENCY = re.compile(r"지역\s*화폐|지역사랑\s*상품권|서울사랑\s*상품권|사랑상품권|온누리")

# ---------- 제외 재료 ----------
EXCLUDE_TRIGGER = re.compile(
    r"\s*(?:은|는|을|를)?\s*(?:빼고|빼줘|빼주세요|빼서|제외|없이|없는\s*걸로|알러지|알레르기|못\s*먹|안\s*먹|넣지\s*말)"
)
LIST_CONNECTORS = ("랑", "하고", "와", "과")  # "땅콩이랑" → "땅콩이" → 조사 제거로 "땅콩"
EXCLUDE_STOPWORDS = {"거", "것", "걸", "건", "게", "그거", "매운", "매운거", "매운건", "음식", "메뉴", "맵", "너무"}


def _strip_particle(word: str) -> str:
    """'오이는' → '오이', '땅콩이' → '땅콩'. 두 글자 단어의 '이'(예: 오이)는 지우지 않는다."""
    word = word.strip(" ,.!?~")
    for particle in ("은", "는", "을", "를"):
        if word.endswith(particle) and len(word) > 2:
            return word[:-1]
    for particle in ("이", "가"):
        if word.endswith(particle) and len(word) > 2:
            return word[:-1]
    return word


def _strip_connector(word: str) -> tuple[str, bool]:
    """'오이랑' → ('오이', True). 연결어가 없으면 (word, False)."""
    if word.endswith(","):
        return word[:-1], True
    for connector in LIST_CONNECTORS:
        if word.endswith(connector) and len(word) > len(connector):
            return word[: -len(connector)], True
    return word, False


def extract_people(text: str) -> tuple[int | None, str | None]:
    match = PEOPLE_DIGIT.search(text)
    if match:
        return int(match.group(1)), None

    match = PEOPLE_WORD.search(text)
    if match:
        value = PEOPLE_WORDS[match.group(1)]
        return value, f"'{match.group(0).strip()}' → people {value}"

    match = PEOPLE_TOGETHER.search(text)
    if match:
        value = PEOPLE_WORDS[match.group(1)]
        return value, f"'{match.group(0).strip()}' → people {value}"

    match = PEOPLE_ALONE.search(text)
    if match:
        return 1, f"'{match.group(0).strip()}' → people 1"

    return None, None


def _digit(value: str) -> int:
    """'5' 또는 '오' → 5"""
    if value.isdigit():
        return int(value)
    return KOREAN_DIGITS[value]


def _find_amounts(text: str) -> list[tuple[int, int, str]]:
    """문장 속 금액을 (시작 위치, 금액, 원문) 목록으로 찾는다."""
    found = []

    for match in AMOUNT_MAN.finditer(text):
        value = float(match.group(1)) * 10000
        if match.group(2):
            value += _digit(match.group(2)) * 1000
        found.append((match.start(), int(value), match.group(0).strip()))

    for match in AMOUNT_WON.finditer(text):
        value = int(match.group(1).replace(",", ""))
        found.append((match.start(), value, match.group(0).strip()))

    for match in AMOUNT_KOREAN.finditer(text):
        man = KOREAN_DIGITS.get(match.group(1), 1)
        value = man * 10000
        if match.group(2):
            value += _digit(match.group(2)) * 1000
        found.append((match.start(), value, match.group(0).strip()))

    for match in AMOUNT_CHEON.finditer(text):
        found.append((match.start(), int(match.group(1)) * 1000, match.group(0).strip()))

    for match in AMOUNT_KOREAN_CHEON.finditer(text):
        found.append((match.start(), KOREAN_DIGITS[match.group(1)] * 1000, match.group(0).strip()))

    # '2만5천원'처럼 겹치면 가장 앞에서 시작한 것(더 긴 표현)을 쓴다
    found.sort()
    return found


def extract_budget(text: str, people: int | None) -> tuple[int | None, str | None]:
    amounts = _find_amounts(text)
    if not amounts:
        return None, None

    start, value, raw = amounts[0]
    if value <= 0:
        return None, None

    before = text[:start]
    if PER_PERSON.search(before):
        if people is None:
            # 인원을 모르면 총예산을 정할 수 없으니 재질문으로 넘긴다
            return None, f"'인당 {raw}' → 인원이 정해지면 총예산을 다시 확인해야 함"
        total = value * people
        return total, f"'인당 {raw}' → budget_krw {total} ({people}명)"

    return value, None


def extract_spice(text: str) -> tuple[int | None, str | None]:
    match = NOT_SPICY.search(text)
    if match:
        return 0, f"'{match.group(0).strip()}' → max_spice_level 0"

    match = LESS_SPICY.search(text)
    if match:
        return 1, f"'{match.group(0).strip()}' → max_spice_level 1"

    # '매운 거'는 상한이 아니라 매운맛을 원하는 것 → extract_tastes가 SPICY로 남긴다 (상한은 비워 둠)
    return None, None


def wants_spicy(text: str) -> bool:
    """'매운 거'처럼 매운맛을 원하는지. '안 매운', '덜 매운', '매운 거 말고'는 원하지 않는 것으로 본다."""
    if NOT_SPICY.search(text) or LESS_SPICY.search(text):
        return False
    return WANT_SPICY.search(text) is not None


def _is_negated(text: str, match: re.Match) -> bool:
    return bool(TASTE_NEGATION_BEFORE.search(text[:match.start()]) or TASTE_NEGATION_AFTER.search(text[match.end():]))


def negated_tastes(text: str) -> set[str]:
    """'느끼한 건 싫어', '매운 거 말고'처럼 싫다고 말한 맛."""
    result = set()
    if WANT_SPICY.search(text) and not wants_spicy(text):
        result.add("SPICY")
    for code, pattern in TASTE_TRIGGERS.items():
        for match in pattern.finditer(text):
            if _is_negated(text, match):
                result.add(code)
    return result


def extract_tastes(text: str) -> tuple[list[str], str | None]:
    """'매운', '달달한' 같은 맛 표현 → ["SPICY", "SWEET"] (TASTE_ORDER 순서)."""
    found = {}
    if wants_spicy(text):
        found["SPICY"] = WANT_SPICY.search(text).group(0)

    for code, pattern in TASTE_TRIGGERS.items():
        for match in pattern.finditer(text):
            if _is_negated(text, match):
                continue
            found[code] = match.group(0)
            break

    tastes = []
    words = []
    for code in TASTE_ORDER:
        if code in found:
            tastes.append(code)
            words.append(f"'{found[code].strip()}' → 맛 취향 {TASTES[code]['label']}")
    if not tastes:
        return [], None
    return tastes, ", ".join(words)


def extract_delivery_minutes(text: str) -> tuple[int | None, str | None]:
    match = MINUTES_LIMIT.search(text)
    if match:
        minutes = int(match.group(1))
        return minutes, f"'{match.group(0).strip()}' → 배달예상 {minutes}분 이내"

    match = FAST_WORDS.search(text)
    if match:
        minutes = DEFAULT_FAST_DELIVERY_MINUTES
        return minutes, f"'{match.group(0)}' → 배달예상 {minutes}분 이내 (기본 규칙)"

    return None, None


def extract_local_currency(text: str) -> tuple[bool, str | None]:
    match = LOCAL_CURRENCY.search(text)
    if match:
        return True, f"'{match.group(0)}' → 지역화폐 가능 가게만 (local_currency_only)"
    return False, None


def extract_excluded_ingredients(text: str) -> list[str]:
    result = []
    for match in EXCLUDE_TRIGGER.finditer(text):
        before = text[: match.start()].rstrip()
        if not before:
            continue

        words = re.split(r"\s+", before)
        last_word = words[-1]

        # '오이,당근 빼고' 처럼 띄어쓰기 없이 쉼표로 이어 쓴 경우
        targets = [part for part in last_word.split(",") if part]

        # '오이랑 당근 빼고', '오이, 당근 빼고' 처럼 앞 단어가 연결어로 이어진 경우
        index = len(words) - 2
        while index >= 0:
            word, connected = _strip_connector(words[index])
            if not connected:
                break
            targets.insert(0, word)
            index -= 1

        for target in targets:
            ingredient = _strip_particle(target)
            if len(ingredient) < 1 or ingredient in EXCLUDE_STOPWORDS:
                continue
            if ingredient not in result:
                result.append(ingredient)
    return result


def find_cuisine(text: str) -> tuple[str | None, str | None]:
    """문장에서 가장 먼저 나온 음식 종류 단어로 cuisine을 정한다."""
    best_name = None
    best_position = None
    best_trigger = None

    for name, info in CUISINES.items():
        for trigger in info["triggers"]:
            position = text.find(trigger)
            if position < 0:
                continue
            is_earlier = best_position is None or position < best_position
            is_longer_at_same_place = position == best_position and len(trigger) > len(best_trigger)
            if is_earlier or is_longer_at_same_place:
                best_name = name
                best_position = position
                best_trigger = trigger

    if best_name is None:
        return None, None
    if best_trigger == best_name:
        return best_name, None
    return best_name, f"'{best_trigger}' → cuisine {best_name}"


def normalize_cuisine(value: str | None) -> str | None:
    """LLM이나 사용자가 보낸 음식 종류를 사전의 이름으로 맞춘다. 모르는 값이면 None."""
    if not value:
        return None
    value = value.strip()
    if value in CUISINES:
        return value
    name, _ = find_cuisine(value)
    return name


def find_menu_keywords(text: str) -> list[str]:
    result = []
    for keyword in DISH_KEYWORDS:
        if keyword in text and keyword not in result:
            result.append(keyword)
    return result


def extract_by_rules(message: str) -> tuple[dict, dict]:
    text = message.strip()
    notes = {}

    people, note = extract_people(text)
    if note:
        notes["people"] = note

    budget, note = extract_budget(text, people)
    if note:
        notes["budget_krw"] = note

    spice, note = extract_spice(text)
    if note:
        notes["max_spice_level"] = note

    minutes, note = extract_delivery_minutes(text)
    if note:
        notes["max_delivery_minutes"] = note

    local_only, note = extract_local_currency(text)
    if note:
        notes["payment_requirements"] = note

    tastes, note = extract_tastes(text)
    if note:
        notes["taste_preferences"] = note

    excluded = extract_excluded_ingredients(text)

    # 제외 재료 단어는 음식 종류·메뉴 검색어로 잡히지 않게 지운 뒤 찾는다 ('고기 빼고' → 고기/구이 아님)
    cleaned = text
    for ingredient in excluded:
        cleaned = cleaned.replace(ingredient, " ")

    cuisine, note = find_cuisine(cleaned)
    if note:
        notes["cuisine"] = note

    constraints = {
        "budget_krw": budget,
        "people": people,
        "cuisine": cuisine,
        "excluded_ingredients": excluded,
        "max_spice_level": spice,
        "payment_requirements": {"local_currency_only": local_only},
        "max_delivery_minutes": minutes,
        "menu_keywords": find_menu_keywords(cleaned),
        "taste_preferences": tastes,
    }
    return constraints, notes


# ---------------------------------------------------------------- LLM 결과 확인
# LLM(qwen2.5 7B)은 '만오천원' → 5000, '국물' → 분식·메뉴 '국물'처럼 문장에 없는 값을 지어내기도 한다.
# 그래서 LLM 값은 문장에 그 값의 근거가 되는 단어가 있을 때만 받는다 (service._merge에서 사용).

LLM_HINTS = {
    "budget_krw": re.compile(r"\d|원|[일이삼사오육칠팔구십백]\s*(?:만|천)|만\s*원"),
    "people": re.compile(r"명|사람|인분|혼자|혼밥|둘|셋|넷|다섯|여섯|이서|\d\s*인|식구|가족|친구|커플|우리|분이서"),
    "max_delivery_minutes": re.compile(r"분|빨리|빠르|빠른|급|얼른|금방|서둘"),
    "max_spice_level": re.compile(r"맵|매운|매콤|순한|자극"),
    "local_currency_only": re.compile(r"지역|화폐|상품권|온누리"),
}
# 규칙 정규식보다 넓은 '맛 근거' 단어. LLM이 규칙이 놓친 표현을 맛으로 읽었을 때 받아 줄지 본다
TASTE_HINTS = {
    "SPICY": re.compile(r"맵|매운|매콤|얼큰|칼칼|화끈|얼얼|자극적인"),
    "SWEET": re.compile(r"달|단\s*(?:거|게|걸|것|음식|맛)|디저트|후식|당\s*땡|스위트"),
    "SOUP": re.compile(r"국물|국\b|국밥|탕|찌개|뜨끈|따끈|따뜻|해장|시원한"),
    "CRISPY": re.compile(r"바삭|튀|겉바|크리스피|크런치"),
    "LIGHT": re.compile(r"담백|가벼|가볍|건강|깔끔|다이어트|편한|자극적이지"),
    "CHEESY": re.compile(r"치즈|크림|꾸덕|고소|느끼|진한|크리미"),
}
# 메뉴 검색어로 쓰면 안 되는 일반 단어 ('국물'을 검색어로 쓰면 '국물통뼈닭발'만 찾게 된다)
GENERIC_MENU_WORDS = {"음식", "메뉴", "거", "것", "식사", "밥", "요리", "국물", "배달", "아무거나", "야식", "간식",
                      "점심", "저녁", "아침", "한끼", "한 끼", "디저트", "후식"}


def has_hint(slot: str, text: str) -> bool:
    return LLM_HINTS[slot].search(text) is not None


def grounded_cuisine(text: str, cuisine: str | None) -> str | None:
    """LLM이 고른 음식 종류는 그 이름이나 대표 단어가 문장에 있을 때만 받는다."""
    name = normalize_cuisine(cuisine)
    if name is None:
        return None
    words = [name] + CUISINES[name]["triggers"]
    for word in words:
        if word in text:
            return name
    return None


def _is_taste_word(word: str) -> bool:
    if WANT_SPICY.search(word):
        return True
    for pattern in TASTE_TRIGGERS.values():
        if pattern.search(word):
            return True
    return False


def _is_cuisine_word(word: str) -> bool:
    for name, info in CUISINES.items():
        if word == name or word in info["triggers"]:
            return True
    return False


def grounded_menu_keywords(text: str, words: list[str]) -> list[str]:
    """문장에 그대로 나온 구체적인 메뉴 이름만. 맛·음식 종류·일반 단어는 뺀다."""
    result = []
    for word in words:
        word = str(word).strip()
        if len(word) < 2 or word not in text:
            continue
        if word in GENERIC_MENU_WORDS or _is_taste_word(word) or _is_cuisine_word(word):
            continue
        result.append(word)
    return result


def grounded_exclusions(text: str, words: list[str]) -> list[str]:
    """문장에 나오고, 바로 뒤에 '빼고·없이·알레르기' 같은 말이 있는 재료만."""
    result = []
    for word in words:
        word = str(word).strip()
        position = text.find(word)
        if not word or position < 0:
            continue
        after = text[position + len(word):position + len(word) + 15]
        if EXCLUDE_TRIGGER.search(after):
            result.append(word)
    return result


def grounded_tastes(text: str, codes: list[str]) -> list[str]:
    """맛 근거 단어가 있고, 싫다고 말하지 않은 맛만."""
    negated = negated_tastes(text)
    result = []
    for code in codes:
        if code in TASTE_HINTS and code not in negated and TASTE_HINTS[code].search(text):
            result.append(code)
    return result
