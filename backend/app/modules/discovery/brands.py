"""브랜드(가게 이름)로 말한 요청 (이슈 #58): "맘스터치 시켜줘", "베라 먹고 싶어" → 그 브랜드 가게에서 찾는다.

- 브랜드는 가게 이름의 '-' 앞부분이다 ('배스킨라빈스-광화문점' → '배스킨라빈스'). 수집 데이터에서 바로 만든다.
- 문장에 브랜드 이름(띄어쓰기·대소문자 무시)이나 줄임말(BRAND_ALIASES)이 있으면 그 브랜드를 고른다.
- '교촌치킨'처럼 끝이 음식 이름이면 앞부분('교촌')만 말해도 알아듣는다.
- 음식 이름과 같은 브랜드('피자피자' 등)나 한 글자 브랜드는 쓰지 않는다 (일반 요청을 브랜드로 오해하지 않게).
"""

import re

from app.modules.discovery.constants import CUISINES, DISH_KEYWORDS

# 줄임말·다른 표기 → 브랜드 이름 (데이터의 브랜드 이름에 들어 있는 단어)
BRAND_ALIASES = {
    "베라": "배스킨라빈스", "배라": "배스킨라빈스", "배스킨": "배스킨라빈스", "베스킨": "배스킨라빈스",
    "맥날": "맥도날드", "맥도": "맥도날드", "버킹": "버거킹", "롯리": "롯데리아", "파바": "파리바게뜨",
    "빠바": "파리바게뜨", "뚜쥬": "뚜레쥬르", "서브웨이": "써브웨이", "쉑쉑": "쉐이크쉑", "엽떡": "동대문엽기떡볶이",
    "엽기떡볶이": "동대문엽기떡볶이", "김천": "김밥천국", "비비큐": "BBQ", "비에이치씨": "BHC", "케이에프씨": "KFC",
    "요거트아이스크림의정석": "요아정", "요아정": "요아정", "설빙": "설빙", "투썸": "투썸플레이스",
    "알볼로": "피자알볼로", "아웃백": "아웃백",
}
FOOD_SUFFIXES = ["치킨", "피자", "떡볶이", "버거", "반점", "김밥", "족발", "보쌈", "마라탕", "도시락", "커피"]
_GENERIC = set(DISH_KEYWORDS) | set(CUISINES) | {t for info in CUISINES.values() for t in info["triggers"]}


def brand_of(name: str) -> str:
    return name.split("-")[0].strip()


def _norm(text: str) -> str:
    return re.sub(r"[\s·,.!?~()\[\]]+", "", text or "").lower()


def brand_words(brand: str) -> list[str]:
    """문장에서 이 브랜드로 볼 단어들: 브랜드 이름, 음식 이름을 뗀 앞부분('교촌치킨' → '교촌')."""
    words = [brand]
    for suffix in FOOD_SUFFIXES:
        if brand.endswith(suffix) and len(brand) - len(suffix) >= 2:
            words.append(brand[: -len(suffix)])
    return [w for w in words if len(_norm(w)) >= 2 and w not in _GENERIC]


def find_brands(text: str, names: list[str]) -> list[str]:
    """문장에 나온 브랜드 (데이터의 브랜드 이름 그대로, 나온 순서). 없으면 []."""
    norm = _norm(text)
    brands = sorted({brand_of(n) for n in names if brand_of(n)})
    found: list[tuple[int, str]] = []
    for alias, target in BRAND_ALIASES.items():
        pos = norm.find(_norm(alias))
        if pos >= 0:
            found += [(pos, b) for b in brands if _norm(target) in _norm(b)]
    for brand in brands:
        for word in brand_words(brand):
            pos = norm.find(_norm(word))
            if pos >= 0:
                found.append((pos, brand))
                break
    out: list[str] = []
    for _, brand in sorted(found):
        if brand not in out:
            out.append(brand)
    return out


def matches_brand(name: str, brands: list[str]) -> bool:
    return brand_of(name) in brands
