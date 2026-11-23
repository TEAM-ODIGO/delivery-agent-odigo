"""새로 수집한 메뉴에 분류 값(유형·맵기·국물·인분·대표메뉴·인기)을 규칙으로 붙인다 (이슈 #58).

기존 메뉴는 따로 분류해 둔 값이 있어 건드리지 않는다. 새 식당의 메뉴만 이 규칙을 쓴다.
기존 9천여 개 메뉴로 맞춰 본 일치율은 scripts/collect_catalog.py --check-classifier 로 볼 수 있다.

- 유형: main(본메뉴) · side · set · drink · alcohol. 추천 조합은 이 값을 보고 메뉴를 고르므로 가장 중요하다.
  요기요 메뉴 묶음 이름(사이드·음료·세트…)을 먼저 보고, 없으면 메뉴 이름을 본다.
- 맵기·국물: 이름에 분명한 단어가 있을 때만 넣고, 모르면 빈 값 (검색이 이름 규칙으로 다시 본다).
- 인분: 이름·설명에 'N인분'이 있을 때만. 없으면 빈 값 (검색이 이름·가격으로 어림한다).
- 대표메뉴: 요기요 '대표메뉴' 묶음에 든 메뉴. 인기: 식당 안에서 메뉴 리뷰가 많은 본메뉴 상위 3개.
"""

import re

from app.modules.discovery.constants import COLD_SOUP_WORDS, DRINK_WORDS, SPICY_FORCE_WORDS

ALCOHOL_WORDS = ["소주", "맥주", "하이볼", "막걸리", "와인", "참이슬", "처음처럼", "카스", "테라", "켈리",
                 "하이네켄", "칭따오", "아사히", "진로", "청하", "고량주", "생맥", "준마이"]
DRINK_NAME_WORDS = DRINK_WORDS + ["콜라", "사이다", "펩시", "코크", "환타", "스프라이트", "닥터페퍼", "제로",
                                   "탄산", "생수", "아이스티", "녹차", "우롱", "보리차", "식혜", "밀키스",
                                   "웰치스", "토레타", "포카리", "게토레이", "핫식스", "레모네이드", "밀크티"]
DRINK_SECTION = ["음료", "드링크", "DRINK", "BEVERAGE", "커피", "에이드", "티 ", "주스", "쥬스", "JUICE", "라떼", "스무디",
                 "쉐이크", "보틀", "COFFEE", "TEA"]
NOT_DRINK_WORDS = ["케이크", "빵", "쿠키", "브레드", "샌드", "토스트", "와플", "마카롱", "타르트", "롤케"]  # 이름에 라떼가 있어도 음식
SIDE_SECTION = ["사이드", "추가", "토핑", "소스", "SIDE", "곁들", "사리", "용품", "포장재"]
# 음식이 아닌 물건 (배스킨라빈스 파티용품 등): 사이드로 두면 추천 본메뉴에 안 들어간다
GOODS_WORDS = ["머리띠", "숫자초", "물결초", "캔들", "초 세트", "폭죽", "고깔", "가랜드", "풍선", "보냉백", "드라이아이스"]
SET_SECTION = ["세트", "콤보", "SET", "COMBO", "패키지", "커플", "혼밥", "1인 세트"]
SIDE_NAME_WORDS = ["공기밥", "밥 추가", "추가", "소스", "단무지", "치킨무", "무 ", "사리", "토핑", "드레싱",
                   "감자튀김", "프렌치프라이", "치즈볼", "콘샐러드", "코울슬로", "계란후라이", "계란 후라이",
                   "김가루", "쌈무", "어묵 추가", "떡 추가", "치즈 추가", "피클", "와사비", "머스타드", "케찹"]
SET_NAME_RE = re.compile(r"세트|콤보|패키지")
SOUP_WORDS = ["탕", "국", "찌개", "전골", "짬뽕", "라멘", "라면", "우동", "국수", "쌀국수", "칼국수", "수제비",
              "해장", "곰탕", "설렁탕", "육개장", "샤브", "마라탕", "훠궈", "스프", "수프"]
DRY_WORDS = ["볶음", "비빔", "구이", "튀김", "까스", "카츠", "덮밥", "볶음밥", "짜장", "자장", "마제", "야끼",
             "스테이크", "피자", "치킨", "버거", "샐러드", "김밥", "초밥", "롤", "떡볶이", "족발", "보쌈"]
NOT_SOUP_WORDS = ["국물없는", "국물 없는", "드라이", "볶음짬뽕", "짜장"]
MILD_WORDS = ["안매운", "안 매운", "순한", "마일드", "MILD"]
SOFT_MILD_WORDS = ["백짬뽕", "하얀", "크림"]  # 매운 단어가 없을 때만 0
HOT_WORDS = ["핵불", "디진다", "엽기", "지옥", "극강"]
SPICY_EXTRA = ["불족발", "불막창", "불곱창", "불오징어", "불쭈꾸미", "불주꾸미", "고추", "땡초"]  # 검색 단어 목록에 없는 매운 이름
SERVINGS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:~\s*\d+(?:\.\d+)?\s*)?인(?:분|용)?(?![가-힣])")


def _has(text: str, words: list[str]) -> bool:
    up = text.upper()
    return any(w.upper() in up for w in words)


def menu_type(name: str, section: str, liquor: bool, price: int) -> str:
    """main · side · set · drink · alcohol."""
    if liquor or _has(name, ALCOHOL_WORDS):
        return "alcohol"
    if _has(name, GOODS_WORDS):
        return "side"
    food_name = _has(name, NOT_DRINK_WORDS)
    if _has(section, DRINK_SECTION) and not _has(section, SET_SECTION) and not food_name:
        return "drink"
    if _has(section, SIDE_SECTION):
        if _has(name, DRINK_NAME_WORDS) and price <= 6000:
            return "drink"
        # '사이드' 묶음에 든 반 마리·반 족 같은 큰 메뉴는 본메뉴로 ('추가'는 그대로 사이드)
        return "main" if price >= 18000 and "추가" not in name else "side"
    if _has(section, SET_SECTION) or (SET_NAME_RE.search(name) and price >= 9000):
        return "set"
    if _has(name, DRINK_NAME_WORDS) and not food_name and price <= 12000:
        return "drink"
    if _has(name, SIDE_NAME_WORDS) and price <= 7000:
        return "side"
    if price and price < 3500:
        return "side"
    return "main"


def spice_level(name: str, description: str) -> int | None:
    text = f"{name} {description}"
    if _has(text, MILD_WORDS):
        return 0
    if _has(name, HOT_WORDS):
        return 3
    if _has(name, SPICY_FORCE_WORDS) or _has(name, SPICY_EXTRA):
        return 2
    if _has(name, SOFT_MILD_WORDS):
        return 0
    return None


def has_soup(name: str) -> bool | None:
    if _has(name, NOT_SOUP_WORDS):
        return False
    if _has(name, COLD_SOUP_WORDS) or _has(name, SOUP_WORDS):
        return True
    if _has(name, DRY_WORDS):
        return False
    return None


def servings(name: str, description: str) -> float | None:
    m = SERVINGS_RE.search(name) or SERVINGS_RE.search(description)
    if not m:
        return None
    value = float(m.group(1))
    return value if 0.5 <= value <= 10 else None


def classify_restaurant(menus: list[dict]) -> list[dict]:
    """한 식당의 메뉴들 → 분류 값.

    menus: {name, description, section, liquor, price, review_count, signature} 목록 (같은 순서로 돌려준다).
    """
    out = []
    for m in menus:
        kind = menu_type(m["name"], m.get("section") or "", bool(m.get("liquor")), int(m.get("price") or 0))
        food = kind in ("main", "set")
        out.append({
            "유형": kind,
            "맵기": "" if not food or (s := spice_level(m["name"], m.get("description") or "")) is None else str(s),
            "국물": "" if not food or (h := has_soup(m["name"])) is None else ("t" if h else "f"),
            "인분": "" if not food or (v := servings(m["name"], m.get("description") or "")) is None else f"{v:.1f}",
            "대표메뉴": "t" if m.get("signature") else "f",
            "인기": "f",
        })
    mains = sorted(
        (i for i, c in enumerate(out) if c["유형"] in ("main", "set") and int(menus[i].get("review_count") or 0) >= 5),
        key=lambda i: -int(menus[i].get("review_count") or 0),
    )
    for i in mains[:3]:
        out[i]["인기"] = "t"
    return out
