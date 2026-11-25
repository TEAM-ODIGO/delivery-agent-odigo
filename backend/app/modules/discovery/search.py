"""A-05 음식점·메뉴 탐색과 점수 계산.

순서
1. 가게 거르기: 음식 종류, '지역화폐만' 조건
2. 가게마다 담을 수 있는 메뉴 거르기: 품절·주류·제외 재료·맵기·음식 종류 단어
   + 원하는 맛(taste_preferences)인지 표시: 매운맛은 맵기 2단계 이상, 국물은 국물 유무 데이터, 나머지는 메뉴 이름
3. 인원수만큼 먹을 수 있는 장바구니(메뉴 1~2종)를 만들어 최소주문금액·배달비·예산을 계산
   (원하는 메뉴·맛이 들어간 장바구니를 먼저 고른다)
4. 배달 시간·예산을 벗어나면 violations에 적고, 점수를 매겨 순위를 정함
   (맛을 말했으면 '가게 메뉴 중 그 맛 메뉴 비율'을 주력 점수로 씀 → 매운 메뉴 하나뿐인 가게보다 떡볶이·짬뽕집이 위)
5. 모두 만족하는 후보가 모자라면 조건을 얼마나 풀면 되는지(relaxation_suggestions) 계산

A는 다른 모듈을 부르지 않는다. 지역화폐 여부도 restaurant 테이블 열만 읽는다.
"""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.discovery import drinks
from app.modules.discovery.catalog_models import Menu, Restaurant
from app.modules.discovery.constants import CUISINES, DISH_PROFILES, MENU_NAME_ALIASES, SPICY_NAME_WORDS, TASTES
from app.modules.discovery.hours import is_open

ORDERABLE_TYPES = ("main", "set")
DRINK_TYPES = ("drink",)  # 음료를 말했을 때만 담는다 (이슈 #107)
DRINK_SPECIALIST_POINTS = 10.0  # 음료 주문에서 음료 전문점 가점 (최대)
DRINK_SPECIALIST_SHARE = 0.6  # 메뉴의 이만큼이 음료면 가점 만점
SIDE_TYPES = ("side",)  # 예산이 남으면 곁들임으로 하나 더 담는다 (이슈 #37). 음료는 담지 않는다
SIDE_MAX_SHARE = 0.4  # 곁들임 가격이 장바구니 메뉴값의 40%를 넘으면 담지 않는다
SIDE_MIN_PRICE_KRW = 2500  # 이보다 싸면 공기밥·소스 같은 추가 항목으로 본다
SIDE_SKIP_WORDS = ["공기밥", "즉석밥", "햇반", "추가", "소스", "후라이", "단무지", "피클", "무말랭이", "국물",
                   "파우더", "시즈닝", "와사비", "쌈장", "젓가락", "포장", "비조리",
                   # 음식이 아닌 물건 (배스킨라빈스 파티용품 등)
                   "머리띠", "풍선", "숫자초", "물결초", "캔들", "고깔", "가랜드", "폭죽", "보냉백", "드라이아이스"]
OVER_BUDGET_SLACK_KRW = 3000  # 예산 안 장바구니가 없을 때, 가장 싼 것보다 이만큼 비싸도 구성이 나으면 고른다
MIN_ITEM_PRICE_KRW = 1000  # 이보다 싼 메인은 옵션성 항목으로 보고 담지 않음
MIN_PRICE_PER_SERVING_KRW = 5000  # 1인분은 적어도 이 정도 가격이라고 본다 (핫도그 1개를 한 끼로 세지 않게)
LIGHT_ITEM_PRICE_KRW = 4000  # 이보다 싼 메뉴(김말이·너겟 등)는 단독으로 한 끼를 채우지 않고 곁들이로만 담는다
SIDE_LIKE_PRICE_RATIO = 0.4  # 가게 메인 메뉴 중간 가격의 40% 미만이면 사이드성 메뉴로 보고 뺀다
TOP_ITEMS_PER_RESTAURANT = 10
# 조건에 맞는 가게가 모자랄 때 채우는 근접 후보는 이만큼까지만 벗어난 가게 (예산 2만원 → 2만6천원까지)
NEAR_MISS_FILL_MAX = 0.3
MAX_QUANTITY = 10
VIOLATION_PENALTY = 20.0
SPICY_MIN_LEVEL = 2  # 매운 걸 원할 때 맵기 데이터가 이 단계 이상이어야 매운 메뉴로 본다 (1은 '약간')
TASTE_FULL_SHARE = 0.5  # 담을 수 있는 메뉴의 절반 이상이 원하는 맛이면 주력 점수 만점
TASTE_MAIN_SHARE = 0.2  # 원하는 맛 메뉴가 이 비율 이상이면 '그 맛을 주로 파는 가게'로 보고 먼저 보여 준다
KEYWORD_BONUS = 3.0
TASTE_BONUS = 2.5  # 원하는 맛 메뉴가 가게 상위 10개 메뉴 안에 남도록 선호도에 더한다

# 이름에 이런 단어가 있으면 보통 2인 이상이 먹는 메뉴로 본다 (인분 정보가 없을 때만)
SHARING_WORDS = ["치킨", "마리", "반반", "피자", "족발", "보쌈", "찜닭", "아구찜", "해물찜", "전골", "감자탕"]
ONE_PERSON_WORDS = ["버거", "샌드위치", "1인", "혼밥", "도시락"]
# '2~3인분'이면 앞의 2를 쓴다 (적게 잡아야 인원수만큼 넉넉히 담음)
SERVINGS_IN_NAME = re.compile(r"(\d{1,2})(?:\s*~\s*\d{1,2})?\s*인(?!분주문)")


@dataclass
class Item:
    menu: Menu
    servings: float
    base_preference: float  # 대표·인기·리뷰로만 계산한 선호도 (점수의 '메뉴 선호'에 씀)
    keyword_hit: bool
    taste_hit: bool  # 원하는 맛을 모두 만족하는 메뉴인지 (맛을 말하지 않았으면 False)
    spice_known: bool

    @property
    def preference(self) -> float:
        """장바구니를 고를 때 쓰는 선호도. 사용자가 말한 메뉴·맛이면 더 높게."""
        total = self.base_preference
        if self.keyword_hit:
            total += KEYWORD_BONUS
        if self.taste_hit:
            total += TASTE_BONUS
        return total

    @property
    def is_light(self) -> bool:
        return self.menu.price_with_required_options_krw < LIGHT_ITEM_PRICE_KRW


@dataclass
class Basket:
    lines: list[tuple[Item, int]]  # (메뉴, 수량)
    subtotal_krw: int
    delivery_fee_krw: int

    @property
    def total_krw(self) -> int:
        return self.subtotal_krw + self.delivery_fee_krw

    @property
    def servings(self) -> float:
        """끼니 양. 최소주문을 채우려고 담은 사이드는 세지 않는다 (치즈볼을 한 끼로 보지 않게)."""
        total = 0.0
        for item, quantity in self.lines:
            if item.menu.menu_type in SIDE_TYPES:
                continue
            total += item.servings * quantity
        return total


@dataclass
class Evaluation:
    restaurant: Restaurant
    basket: Basket | None = None
    taste_share: float = 0.0  # 담을 수 있는 메뉴 중 원하는 맛 메뉴 비율
    drink_share: float = 0.0  # 음료 주문일 때: 가게 메뉴 중 음료 비율 (음료 전문점일수록 높다, 이슈 #107)
    violations: list[dict] = field(default_factory=list)
    hard_exclusion: str | None = None  # 값이 있으면 후보로 내보내지 않음
    score: float = 0.0


# ---------------------------------------------------------------- 가게 단위

def delivery_fee_for(restaurant: Restaurant, subtotal_krw: int) -> int | None:
    """주문금액에 맞는 배달비. 최소 구간보다 적으면 None (주문 불가)."""
    tiers = sorted(restaurant.delivery_fee_tiers or [], key=lambda tier: tier["order_amount"], reverse=True)
    for tier in tiers:
        if subtotal_krw >= tier["order_amount"]:
            return int(tier["delivery_fee"])
    if not tiers and subtotal_krw >= restaurant.min_order_krw:
        return restaurant.base_delivery_fee_krw
    return None


def matches_cuisine(restaurant: Restaurant, cuisine: str | None) -> bool:
    if cuisine is None:
        return True
    info = CUISINES.get(cuisine)
    if info is None:
        return True
    for category in info["categories"]:
        if category in restaurant.yogiyo_categories:
            return True
    return False


GENERIC_CATEGORIES = {"포장", "프랜차이즈", "야식", "1인분주문", "다회용기"}


def is_primary_cuisine(restaurant: Restaurant, cuisine: str | None) -> bool:
    """요기요 카테고리 중 첫 번째 '음식' 카테고리가 원하는 종류인지 (치킨을 원할 때 롯데리아보다 치킨집 우선)."""
    info = CUISINES.get(cuisine or "")
    if info is None:
        return True
    for category in restaurant.yogiyo_categories:
        if category in GENERIC_CATEGORIES:
            continue
        return category in info["categories"]
    return False


def cuisine_of(restaurant: Restaurant, requested: str | None) -> str | None:
    """화면 카드 이미지용 음식 종류. 사용자가 고른 종류가 맞으면 그걸 쓰고, 아니면 첫 번째 음식 카테고리로 정한다."""
    if requested in CUISINES and matches_cuisine(restaurant, requested):
        return requested
    for category in restaurant.yogiyo_categories:
        if category in GENERIC_CATEGORIES:
            continue
        for name, info in CUISINES.items():
            if category in info["categories"]:
                return name
    return None


def delivery_minutes_of(restaurant: Restaurant) -> int | None:
    """요기요 '23~38분' 중 늦은 쪽(38분)을 예상 시간으로 쓴다. 조건 판정을 보수적으로 하려고."""
    return restaurant.delivery_minutes_max


# ---------------------------------------------------------------- 메뉴 단위

def estimate_servings(menu: Menu) -> float:
    if menu.menu_type in DRINK_TYPES:  # 음료는 한 잔이 한 사람 몫 (이슈 #107)
        return 1.0
    servings = 1.0
    if menu.servings:
        servings = float(menu.servings)
    else:
        match = SERVINGS_IN_NAME.search(menu.name)
        if match and 1 <= int(match.group(1)) <= 10:
            servings = float(match.group(1))
        elif contains_any(menu.name, ONE_PERSON_WORDS):
            servings = 1.0
        elif contains_any(menu.name, SHARING_WORDS):
            servings = 2.0

    # 가격이 너무 싸면 표시된 인분보다 적게 본다 (예: 4,400원 핫도그 → 0.88인분)
    by_price = menu.price_with_required_options_krw / MIN_PRICE_PER_SERVING_KRW
    return round(max(0.5, min(servings, by_price)), 2)


def contains_any(text: str, words: list[str]) -> str | None:
    for word in words:
        if word and word in text:
            return word
    return None


def name_has_keyword(name: str, keyword: str, menu_type: str | None = None) -> bool:
    """메뉴 이름에 사용자가 말한 메뉴가 있는지. 이름에 음식 이름이 안 나오는 메뉴는 MENU_NAME_ALIASES로 본다.
    음료(커피·차·에이드 …)는 drinks 사전으로 본다 (이슈 #107)."""
    if drinks.is_drink_keyword(keyword):
        return drinks.menu_matches(name, menu_type, keyword)
    return any(word in name for word in MENU_NAME_ALIASES.get(keyword, [keyword]) if word)


def name_has_any_keyword(name: str, keywords: list[str], menu_type: str | None = None) -> bool:
    return any(name_has_keyword(name, keyword, menu_type) for keyword in keywords)


def matches_taste(menu: Menu, code: str) -> bool:
    """이 메뉴가 그 맛인지. 순서: 이름에 확실한 단어 → 수집 데이터 → 이름의 일반 단어 (constants.TASTES 주석)."""
    info = TASTES[code]
    if contains_any(menu.name, info["force_words"]):
        return True
    if contains_any(menu.name, info["block_words"]):
        return False

    if code == "SPICY" and menu.spice_level is not None:
        return menu.spice_level >= SPICY_MIN_LEVEL
    if code == "SOUP" and menu.has_soup is not None:
        return menu.has_soup
    # 담백한 것을 원하면 데이터상 맵기 2단계 이상은 뺀다
    if code == "LIGHT" and menu.spice_level is not None and menu.spice_level >= SPICY_MIN_LEVEL:
        return False

    return contains_any(menu.name, info["menu_words"]) is not None


def matches_all_tastes(menu: Menu, tastes: list[str]) -> bool:
    if not tastes:
        return False
    for code in tastes:
        if not matches_taste(menu, code):
            return False
    return True


def taste_labels(tastes: list[str]) -> str:
    labels = []
    for code in tastes:
        labels.append(TASTES[code]["label"])
    return "·".join(labels)


def taste_evidence(menu: Menu, tastes: list[str]) -> str:
    """추천 이유에 쓸 근거. 예: '매운(맵기 3단계)·국물(국물 있음)'"""
    parts = []
    for code in tastes:
        label = TASTES[code]["label"]
        # 데이터가 판단과 같은 방향일 때만 데이터를 근거로 보여 준다 (이름 규칙으로 뒤집은 경우는 '이름 기준')
        if code == "SPICY" and menu.spice_level is not None and menu.spice_level >= SPICY_MIN_LEVEL:
            parts.append(f"{label}(맵기 {menu.spice_level}단계)")
        elif code == "SOUP" and menu.has_soup:
            parts.append(f"{label}(국물 있음)")
        else:
            parts.append(f"{label}(메뉴 이름 기준)")
    return "·".join(parts)


def build_item(menu: Menu, constraints: dict, side: bool = False) -> Item | None:
    """담을 수 없는 메뉴면 None. side=True면 곁들임(사이드) 메뉴로 본다 (음식 종류 단어는 보지 않음)."""
    if menu.is_sold_out or menu.is_alcohol:
        return None
    # 음료는 음료를 말했을 때만 담는다. 분류에 '메인 없는 주문 불가'라고 적힌 음료는 따로 못 시킨다 (이슈 #107)
    drink_order = drinks.is_drink_request(constraints)
    allowed = SIDE_TYPES if side else (ORDERABLE_TYPES + DRINK_TYPES if drink_order else ORDERABLE_TYPES)
    if menu.menu_type not in allowed:
        return None
    if menu.menu_type in DRINK_TYPES and not drinks.can_order_alone(menu.menu_category):
        return None
    if menu.price_with_required_options_krw < MIN_ITEM_PRICE_KRW:
        return None

    text = menu.name + " " + (menu.description or "")

    # 제외 재료: 이름·설명에 그 단어가 있으면 뺀다 (재료표가 없어 글자로만 판단)
    if contains_any(text, constraints.get("excluded_ingredients") or []):
        return None

    # 맵기: 정보가 있으면 상한과 비교, 없으면 '안 매운' 조건일 때 이름으로 한 번 더 거른다
    max_spice = constraints.get("max_spice_level")
    spice_known = menu.spice_level is not None
    if max_spice is not None:
        if spice_known and menu.spice_level > max_spice:
            return None
        if not spice_known and max_spice == 0 and contains_any(menu.name, SPICY_NAME_WORDS):
            return None

    # 음식 종류 단어: 피자를 원하면 '피자/양식' 가게에서도 피자 메뉴만
    cuisine_info = CUISINES.get(constraints.get("cuisine") or "")
    if cuisine_info and cuisine_info["menu_words"] and not side:
        if not contains_any(menu.name, cuisine_info["menu_words"]):
            return None
        if contains_any(menu.name, cuisine_info.get("not_menu_words", [])):
            return None

    keywords = constraints.get("menu_keywords") or []
    keyword_hit = name_has_any_keyword(menu.name, keywords, menu.menu_type)
    taste_hit = matches_all_tastes(menu, constraints.get("taste_preferences") or [])

    preference = 0.0
    if menu.is_signature:
        preference += 1.5
    if menu.is_popular:
        preference += 1.5
    preference += min(math.log10(menu.review_count + 1), 2.0) * 0.5
    if drink_order:
        preference += drinks.pref_bonus(menu.name, constraints.get("drink_prefs") or [])

    return Item(
        menu=menu,
        servings=estimate_servings(menu),
        base_preference=preference,
        keyword_hit=keyword_hit,
        taste_hit=taste_hit,
        spice_known=spice_known,
    )


def drop_side_like(items: list[Item]) -> list[Item]:
    """가게 메인 메뉴 중간 가격보다 훨씬 싼 '메인'(김말이·할인 쿠폰성 메뉴 등)을 뺀다. 사용자가 말한 메뉴는 남긴다."""
    if len(items) < 3:
        return items
    prices = sorted(item.menu.price_with_required_options_krw for item in items)
    median = prices[len(prices) // 2]
    kept = []
    for item in items:
        if item.keyword_hit or item.menu.price_with_required_options_krw >= median * SIDE_LIKE_PRICE_RATIO:
            kept.append(item)
    return kept


# ---------------------------------------------------------------- 장바구니

def make_basket(restaurant: Restaurant, lines: list[tuple[Item, int]]) -> Basket | None:
    subtotal = 0
    for item, quantity in lines:
        subtotal += item.menu.price_with_required_options_krw * quantity
    if subtotal < restaurant.min_order_krw:
        return None
    fee = delivery_fee_for(restaurant, subtotal)
    if fee is None:
        return None
    return Basket(lines=lines, subtotal_krw=subtotal, delivery_fee_krw=fee)


def _lines_basket(restaurant: Restaurant, lines: list[tuple[Item, int]], people: int) -> Basket | None:
    """여러 메뉴를 담고, 인분이 모자라면 첫 메뉴 수량을 늘려 채운다. 최소주문금액은 첫 메뉴 수량으로 맞춘다."""
    servings = sum(item.servings * quantity for item, quantity in lines)
    first, first_quantity = lines[0]
    if servings < people:
        first_quantity += math.ceil((people - servings) / first.servings)
    for extra in range(3):
        quantity = first_quantity + extra
        if quantity > MAX_QUANTITY:
            return None
        basket = make_basket(restaurant, [(first, quantity)] + lines[1:])
        if basket is not None:
            return basket
    return None


def fill_with_sides(restaurant: Restaurant, lines: list[tuple[Item, int]], sides: list[Item]) -> Basket | None:
    """최소주문금액이 모자라면 본메뉴를 더 담는 대신 사이드로 채운다 (이슈 #58: 혼자 버거 3개 → 버거 + 감자튀김).

    모자란 금액 이상인 사이드 중 가장 싼 것, 없으면 가장 비싼 것부터 담는다 (서로 다른 사이드 최대 3개).
    """
    subtotal = sum(item.menu.price_with_required_options_krw * quantity for item, quantity in lines)
    gap = restaurant.min_order_krw - subtotal
    if gap <= 0:
        return None  # 이미 채워짐: 기본 장바구니가 같은 것을 만든다
    usable = [
        side for side in sides
        if side.menu.price_with_required_options_krw >= SIDE_MIN_PRICE_KRW
        and not contains_any(side.menu.name, SIDE_SKIP_WORDS)
    ]
    added: list[tuple[Item, int]] = []
    while gap > 0 and len(added) < 3:
        left = [side for side in usable if all(side is not chosen for chosen, _ in added)]
        if not left:
            return None
        enough = [side for side in left if side.menu.price_with_required_options_krw >= gap]
        if enough:
            pick = min(enough, key=lambda side: (side.menu.price_with_required_options_krw, -side.base_preference))
        else:
            pick = max(left, key=lambda side: (side.menu.price_with_required_options_krw, side.base_preference))
        added.append((pick, 1))
        gap -= pick.menu.price_with_required_options_krw
    if gap > 0:
        return None
    return make_basket(restaurant, lines + added)


def candidate_baskets(
    restaurant: Restaurant, items: list[Item], people: int, sides: list[Item] | None = None
) -> list[Basket]:
    baskets = []

    # 인원수만큼 담고 최소주문금액은 사이드로 채운 장바구니
    for item in items:
        if item.is_light or not sides:
            continue
        quantity = max(1, math.ceil(people / max(item.servings, 0.5)))
        if quantity > MAX_QUANTITY:
            continue
        basket = fill_with_sides(restaurant, [(item, quantity)], sides)
        if basket is not None:
            baskets.append(basket)

    # 메뉴 1종을 인원수만큼 (최소주문금액이 모자라면 한두 개 더)
    for item in items:
        if item.is_light:
            continue
        basket = _lines_basket(restaurant, [(item, 1)], people)
        if basket is not None:
            baskets.append(basket)

    # 서로 다른 메뉴 2종 (이슈 #37: 1명이어도 본다 — 최소주문을 같은 메뉴 반복 대신 다른 메뉴로 채우게).
    # 어느 쪽 수량을 늘릴지 양쪽 다 본다 (탕수육 1 + 짜장면 2 / 짜장면 1 + 탕수육 2)
    top = items[:8]
    for first in top:
        for second in top:
            if first is second or first.is_light:
                continue
            basket = _lines_basket(restaurant, [(first, 1), (second, 1)], people)
            if basket is not None:
                baskets.append(basket)

    # 3명 이상이면 3종 조합도 본다 (치킨 + 떡볶이 + 사이드처럼 나눠 먹는 구성)
    if people >= 3:
        top = items[:6]
        for i, first in enumerate(top):
            if first.is_light:
                continue
            for j in range(len(top)):
                for k in range(j + 1, len(top)):
                    if i in (j, k):
                        continue
                    basket = _lines_basket(restaurant, [(first, 1), (top[j], 1), (top[k], 1)], people)
                    if basket is not None:
                        baskets.append(basket)

    return baskets


def drink_baskets(
    restaurant: Restaurant, items: list[Item], sides: list[Item], keywords: list[str], cups: int
) -> list[Basket]:
    """음료 주문 (이슈 #107): 말한 음료를 잔 수만큼 담는다 ('커피 2잔' → 아메리카노 2).

    음료가 여러 가지면 잔 수를 나눈다. 음료가 아닌 메뉴(케이크)를 같이 말했으면 하나씩 더 담는다.
    최소주문금액이 모자라면 같은 가게의 다른 음료·디저트·사이드로 채운다.
    말한 것 중 이 가게에 없는 것은 빼고 담는다 (몇 가지를 담았는지는 점수에 반영). 음료가 하나도 없으면 빈 목록.
    """
    def hits_of(word: str) -> list[Item]:
        found = [item for item in items if name_has_keyword(item.menu.name, word, item.menu.menu_type)]
        return sorted(found, key=lambda item: -item.preference)[:3]

    drink_words = [k for k in keywords if drinks.is_drink_keyword(k) and hits_of(k)]
    food_words = [k for k in keywords if not drinks.is_drink_keyword(k) and hits_of(k)]
    if not drink_words:
        return []
    picks = [hits_of(word) for word in drink_words + food_words]
    quantities = drinks.split_cups(cups, len(drink_words)) + [1] * len(food_words)

    baskets = []
    for variant in range(3):
        lines: list[tuple[Item, int]] = []
        for choices, quantity in zip(picks, quantities, strict=True):
            item = choices[min(variant, len(choices) - 1)]
            if any(item is chosen for chosen, _ in lines):
                continue
            lines.append((item, quantity))
        basket = make_basket(restaurant, lines)
        if basket is None:
            # 말한 음료를 한 잔 더 담기보다 다른 메뉴로 채운다 ('커피 2잔'에 세 번째 커피를 넣지 않게)
            fillers = [
                item for item in items + sides
                if all(item is not chosen for chosen, _ in lines)
                and not name_has_any_keyword(item.menu.name, keywords, item.menu.menu_type)
            ]
            basket = fill_with_sides(restaurant, lines, fillers)
        if basket is not None and all(basket is not b and basket.lines != b.lines for b in baskets):
            baskets.append(basket)
    return baskets


def basket_quality(basket: Basket, people: int) -> float:
    """장바구니를 고를 때의 점수: 메뉴 선호 평균 + 다양성 − 남는 양 − 같은 메뉴 반복 (이슈 #37).
    예전에는 선호 평균만 봐서 가장 인기 있는 메뉴 하나 × 인원수가 늘 이겼다."""
    quality = basket_preference(basket)
    kinds = len(basket.lines)
    if people >= 2:
        quality += 1.2 * min(kinds - 1, 2)  # 둘이면 2종, 셋 이상이면 3종까지 다양할수록
    excess = basket.servings - max(people, 1)
    if excess > 0.5:
        quality -= 0.8 * (excess - 0.5)  # 인원보다 많이 담은 양
    for _, quantity in basket.lines:
        if quantity > max(people, 1):
            quality -= 1.0 * (quantity - max(people, 1))  # 2명에 짜장면 3개
        elif people >= 3 and quantity >= 3:
            quality -= 0.4 * (quantity - 2)  # 셋 이상이 같은 메뉴만 3개 이상
    return quality


def keywords_covered(basket: Basket, keywords: list[str]) -> int:
    covered = 0
    for keyword in keywords:
        if any(name_has_keyword(item.menu.name, keyword, item.menu.menu_type) for item, _ in basket.lines):
            covered += 1
    return covered


def basket_preference(basket: Basket) -> float:
    """수량으로 가중한 선호도 평균 (말한 메뉴를 더 많이 담은 쪽이 높게)."""
    total = 0.0
    count = 0
    mains = [(item, quantity) for item, quantity in basket.lines if item.menu.menu_type not in SIDE_TYPES]
    for item, quantity in mains or basket.lines:  # 최소주문을 채운 사이드는 메뉴 선호에 넣지 않는다
        total += item.preference * quantity
        count += quantity
    return total / count


def basket_base_preference(basket: Basket) -> float:
    total = 0.0
    for item, _ in basket.lines:
        total += item.base_preference
    return total / len(basket.lines)


def has_keyword(basket: Basket) -> bool:
    for item, _ in basket.lines:
        if item.keyword_hit:
            return True
    return False


def has_taste(basket: Basket) -> bool:
    for item, _ in basket.lines:
        if item.taste_hit:
            return True
    return False


def choose_basket(baskets: list[Basket], budget_krw: int | None, people: int = 1,
                  keywords: list[str] | None = None) -> Basket | None:
    """예산 안이면 구성 점수(basket_quality)가 높은 것, 예산 안이 하나도 없으면 가장 싼 것 (근접 후보용).
    사용자가 말한 메뉴(menu_keywords)·맛(taste_preferences)이 들어간 장바구니가 있으면 그것들 중에서만 고른다.
    메뉴를 여러 개 말했으면(짜장면 탕수육) 더 많이 담은 장바구니만 본다."""
    if not baskets:
        return None

    with_keyword = [basket for basket in baskets if has_keyword(basket)]
    if with_keyword:
        baskets = with_keyword
        if keywords and len(keywords) > 1:
            best = max(keywords_covered(basket, keywords) for basket in baskets)
            baskets = [basket for basket in baskets if keywords_covered(basket, keywords) == best]

    with_taste = [basket for basket in baskets if has_taste(basket)]
    if with_taste:
        baskets = with_taste

    if budget_krw is None:
        within = baskets
    else:
        within = [basket for basket in baskets if basket.total_krw <= budget_krw]

    if within:
        within.sort(key=lambda basket: (-basket_quality(basket, people), basket.total_krw))
        return within[0]

    # 예산 안이 없으면 거의 가장 싼 것들 중 구성이 나은 것 (혼자인데 버거 3개보다 버거 + 사이드)
    cheapest = min(basket.total_krw for basket in baskets)
    close = [basket for basket in baskets if basket.total_krw <= cheapest + OVER_BUDGET_SLACK_KRW]
    close.sort(key=lambda basket: (-basket_quality(basket, people), basket.total_krw))
    return close[0]


def add_side(restaurant: Restaurant, basket: Basket, sides: list[Item], budget_krw: int | None,
             people: int) -> Basket:
    """2명 이상이고 예산이 남으면 인기 곁들임(사이드)을 하나 더 담는다. 못 담으면 그대로."""
    if people < 2 or budget_krw is None or not sides or len(basket.lines) >= 3:
        return basket
    if basket.total_krw > budget_krw:
        return basket
    for side in sorted(sides, key=lambda item: (-item.base_preference, item.menu.price_with_required_options_krw)):
        price = side.menu.price_with_required_options_krw
        if price < SIDE_MIN_PRICE_KRW or price > basket.subtotal_krw * SIDE_MAX_SHARE:
            continue
        if contains_any(side.menu.name, SIDE_SKIP_WORDS):
            continue
        bigger = make_basket(restaurant, basket.lines + [(side, 1)])
        if bigger is not None and bigger.total_krw <= budget_krw:
            return bigger
    return basket


# ---------------------------------------------------------------- 평가·점수

def evaluate(restaurant: Restaurant, menus: list[Menu], constraints: dict) -> Evaluation:
    evaluation = Evaluation(restaurant=restaurant)

    if not matches_cuisine(restaurant, constraints.get("cuisine")):
        evaluation.hard_exclusion = "CUISINE_MISMATCH"
        return evaluation

    if drinks.is_drink_request(constraints) and menus:
        evaluation.drink_share = sum(1 for menu in menus if menu.menu_type in DRINK_TYPES) / len(menus)

    items = []
    sides = []
    for menu in menus:
        if menu.menu_type in SIDE_TYPES:
            side = build_item(menu, constraints, side=True)
            if side is not None:
                sides.append(side)
            continue
        item = build_item(menu, constraints)
        if item is not None:
            items.append(item)
    items = drop_side_like(items)

    if items and constraints.get("taste_preferences"):
        hits = 0
        for item in items:
            if item.taste_hit:
                hits += 1
        evaluation.taste_share = hits / len(items)

    items.sort(key=lambda item: -item.preference)
    items = items[:TOP_ITEMS_PER_RESTAURANT]

    if not items:
        evaluation.hard_exclusion = "NO_ORDERABLE_MENU"
        return evaluation

    people = constraints.get("people") or 1
    budget = constraints.get("budget_krw")
    keywords = constraints.get("menu_keywords") or []
    baskets = drink_baskets(restaurant, items, sides, keywords, people) if drinks.is_drink_request(constraints) else []
    if not baskets:
        baskets = candidate_baskets(restaurant, items, people, sides)
    basket = choose_basket(baskets, budget, people, keywords)
    if basket is not None:
        basket = add_side(restaurant, basket, sides, budget, people)
    if basket is None:
        evaluation.hard_exclusion = "BELOW_MIN_ORDER"
        return evaluation
    evaluation.basket = basket

    if budget is not None and basket.total_krw > budget:
        evaluation.violations.append({
            "code": "BUDGET_EXCEEDED",
            "message": f"예산 {budget:,}원보다 {basket.total_krw - budget:,}원 많습니다.",
            "limit": budget,
            "actual": basket.total_krw,
        })

    # 메뉴를 여러 개 말했는데 일부가 이 가게에 없으면 조건 벗어남으로 본다 (이슈 #109).
    # '레몬에이드 하나랑 아아 하나'에서 레몬에이드가 없는 가게가 둘 다 있는 가게보다 앞에 나오지 않게
    missing = [k for k in keywords if not keywords_covered(basket, [k])] if len(keywords) > 1 else []
    if missing and len(missing) < len(keywords):
        names = ", ".join(f"'{k}'" for k in missing)
        evaluation.violations.append({
            "code": "MENU_MISSING",
            "message": f"{names}{_eun(missing[-1])} 이 가게에 없어요.",
            "limit": len(keywords),
            "actual": len(keywords) + len(missing),
        })

    max_minutes = constraints.get("max_delivery_minutes")
    minutes = delivery_minutes_of(restaurant)
    if max_minutes is not None and minutes is not None and minutes > max_minutes:
        evaluation.violations.append({
            "code": "DELIVERY_TIME_EXCEEDED",
            "message": f"배달예상 {minutes}분으로 {max_minutes}분을 넘습니다.",
            "limit": max_minutes,
            "actual": minutes,
        })

    # '지역화폐만' 조건이면 false·null 가게는 추천하지 않는다 (완화 제안 계산에만 씀)
    payment = constraints.get("payment_requirements") or {}
    if payment.get("local_currency_only") and restaurant.local_currency_merchant is not True:
        evaluation.hard_exclusion = "LOCAL_CURRENCY_NOT_CONFIRMED"

    evaluation.score = compute_score(evaluation, constraints)
    return evaluation


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def compute_score(evaluation: Evaluation, constraints: dict) -> float:
    """0~100점. 별점 30 · 리뷰 수 10 · 배달 속도 15 · 가격 15 · 메뉴 선호 10 · 주력 음식 10 · 요청 메뉴 10,
    조건 위반마다 -20.
    주력 음식 10점: 음식 종류만 말했으면 그 종류가 가게 주력인지, 맛만 말했으면 가게 메뉴 중 그 맛 비율,
    둘 다 말했으면 5점씩."""
    restaurant = evaluation.restaurant
    basket = evaluation.basket

    rating_score = float(restaurant.adjusted_rating) / 5.0 * 30.0
    review_score = clamp(math.log10(restaurant.review_count + 1) / 3.5, 0.0, 1.0) * 10.0

    minutes = delivery_minutes_of(restaurant) or 60
    speed_score = clamp((60 - minutes) / 40.0, 0.0, 1.0) * 15.0

    budget = constraints.get("budget_krw")
    if budget:
        ratio = basket.total_krw / budget
        if ratio <= 1.0:
            # 예산 안이면 기본 12점, 많이 남길수록 3점까지 더 (너무 싼 간식이 한 끼 식사를 이기지 않게 차이를 작게)
            price_score = 12.0 + 3.0 * clamp((1.0 - ratio) / 0.3, 0.0, 1.0)
        else:
            price_score = 0.0
    else:
        people = constraints.get("people") or 1
        price_score = 15.0 * clamp(1.0 - basket.total_krw / (people * 25000 * 1.5), 0.0, 1.0)

    # 메뉴·맛 가점은 아래 keyword_score·cuisine_score에서 따로 주므로 여기서는 인기·대표·리뷰만 본다
    menu_score = clamp(basket_base_preference(basket) / 3.0, 0.0, 1.0) * 10.0

    keywords = constraints.get("menu_keywords") or []
    if not keywords or has_keyword(basket):
        keyword_score = 10.0
    else:
        keyword_score = 0.0
    if keywords and drinks.is_drink_request(constraints):
        # 음료 여러 가지('레몬에이드랑 아메리카노')는 몇 가지를 담았는지로 (이슈 #107)
        keyword_score = 10.0 * keywords_covered(basket, keywords) / len(keywords)

    if is_primary_cuisine(restaurant, constraints.get("cuisine")):
        kind_score = 10.0
    else:
        kind_score = 4.0
    if constraints.get("taste_preferences"):
        taste_score = clamp(evaluation.taste_share / TASTE_FULL_SHARE, 0.0, 1.0) * 10.0
        if constraints.get("cuisine"):
            cuisine_score = (kind_score + taste_score) / 2
        else:
            cuisine_score = taste_score
    else:
        cuisine_score = kind_score

    total = rating_score + review_score + speed_score + price_score + menu_score + cuisine_score + keyword_score
    if drinks.is_drink_request(constraints):
        # 커피를 시키면 빵집·토스트집보다 커피 전문점을 먼저 (음료 비율 60% 이상이면 만점, 이슈 #107)
        total += DRINK_SPECIALIST_POINTS * clamp(evaluation.drink_share / DRINK_SPECIALIST_SHARE, 0.0, 1.0)
    total -= VIOLATION_PENALTY * len(evaluation.violations)
    return round(clamp(total, 0.0, 100.0), 1)


# ---------------------------------------------------------------- 응답 만들기

def brand_of(name: str) -> str:
    """'노모어피자-대학로점' → '노모어피자'. 같은 브랜드가 순위를 다 차지하지 않게 할 때 쓴다."""
    return name.split("-")[0].strip()


def default_reasons(evaluation: Evaluation, constraints: dict) -> list[str]:
    restaurant = evaluation.restaurant
    basket = evaluation.basket
    minutes = delivery_minutes_of(restaurant)
    budget = constraints.get("budget_krw")

    if budget is not None and basket.total_krw <= budget:
        first = f"예산 안({budget:,}원), {minutes}분 안에 도착 예정"
    elif budget is not None:
        first = f"예산보다 {basket.total_krw - budget:,}원 많은 가장 가까운 후보, {minutes}분 안에 도착 예정"
    else:
        first = f"총 {basket.total_krw:,}원, {minutes}분 안에 도착 예정"

    details = [f"별점 {float(restaurant.rating):.1f}(리뷰 {restaurant.review_count:,}개)"]
    if constraints.get("people"):
        details.append(f"{constraints['people']}명 기준 약 {basket.servings:g}인분")
    for item, _ in basket.lines:
        if item.keyword_hit:
            details.append(f"'{item.menu.name}' 포함")
            break
    tastes = constraints.get("taste_preferences") or []
    for item, _ in basket.lines:
        if item.taste_hit:
            details.append(f"'{item.menu.name}' {taste_evidence(item.menu, tastes)}")
            details.append(f"가게 메뉴 중 {taste_labels(tastes)} 메뉴 {evaluation.taste_share:.0%}")
            break
    max_spice = constraints.get("max_spice_level")
    if max_spice is not None:
        all_known = all(item.spice_known for item, _ in basket.lines)
        if all_known:
            details.append("맵기 조건 충족")
        else:
            details.append("일부 메뉴는 맵기 정보가 없어 이름으로 확인")
    if constraints.get("excluded_ingredients"):
        details.append("제외 재료는 메뉴 이름·설명 기준으로 확인")
    if restaurant.local_currency_merchant is True:
        if restaurant.local_currency_basis == "ASSUMED_FROM_VOUCHER":
            details.append("지역화폐 가능(추정)")
        else:
            details.append("지역화폐 가능")

    return [first, " · ".join(details)]


def main_first(lines: list[tuple[Item, int]]) -> list[tuple[Item, int]]:
    """후보 카드에 보일 순서: 말한 메뉴 → 본메뉴(비싼 순) → 사이드 (이슈 #58).

    장바구니는 최소주문을 채우려고 싼 메뉴를 먼저 담기도 해서('블록팩 1, 파인트 1') 그대로 보여 주면
    채우기용 메뉴가 주인공처럼 보인다.
    """
    return sorted(
        lines,
        key=lambda line: (
            line[0].menu.menu_type in SIDE_TYPES,
            not line[0].keyword_hit,
            -line[0].menu.price_with_required_options_krw * line[1],
        ),
    )


def candidate_body(evaluation: Evaluation, constraints: dict) -> dict:
    restaurant = evaluation.restaurant
    basket = evaluation.basket
    items = []
    for item, quantity in main_first(basket.lines):
        items.append({
            "menu_id": item.menu.menu_id,
            "name": item.menu.name,
            "quantity": quantity,
            "option_ids": [],  # 옵션 데이터가 아직 없어 빈 목록 (필수옵션 가격은 unit_price_krw에 포함)
            "unit_price_krw": item.menu.price_with_required_options_krw,
        })

    return {
        "restaurant": {
            "restaurant_id": restaurant.restaurant_id,
            "name": restaurant.name,
            "rating": float(restaurant.rating),
            "delivery_minutes": delivery_minutes_of(restaurant),
            "delivery_fee_krw": basket.delivery_fee_krw,
            "accepts_livelihood_voucher": restaurant.accepts_livelihood_voucher,
            "local_currency_merchant": restaurant.local_currency_merchant,
            "local_currency_basis": restaurant.local_currency_basis,
            "cuisine": cuisine_of(restaurant, constraints.get("cuisine")),
        },
        "items": items,
        "estimated_total_krw": basket.total_krw,
        "meets_all_constraints": len(evaluation.violations) == 0,
        "violations": evaluation.violations,
        "score": evaluation.score,
        "reasons": default_reasons(evaluation, constraints),
    }


def miss_distance(evaluation: Evaluation) -> float:
    """조건에서 얼마나 벗어났는지 (예산 10% 초과 = 0.1). 근접 후보를 가까운 순으로 세울 때 쓴다."""
    distance = 0.0
    for violation in evaluation.violations:
        limit = violation["limit"] or 1
        distance += (violation["actual"] - limit) / limit
    return distance


def is_taste_main(evaluation: Evaluation, tastes: list[str]) -> bool:
    if not tastes:
        return True
    return evaluation.taste_share >= TASTE_MAIN_SHARE


def pick_top(evaluations: list[Evaluation], top_n: int) -> list[Evaluation]:
    picked = []
    brands = set()
    for evaluation in evaluations:
        brand = brand_of(evaluation.restaurant.name)
        if brand in brands:
            continue
        picked.append(evaluation)
        brands.add(brand)
        if len(picked) == top_n:
            break
    return picked


def relaxation_suggestions(evaluations: list[Evaluation], constraints: dict, needed: int,
                           require_keyword: bool, require_taste: bool) -> list[dict]:
    """조건 하나만 풀면 후보가 되는 가게를 세어, 얼마나 풀면 needed곳을 더 볼 수 있는지 알려 준다."""
    suggestions = []

    budget_needs = []
    minute_needs = []
    local_currency_count = 0
    for evaluation in evaluations:
        if evaluation.basket is None:
            continue
        if require_keyword and not has_keyword(evaluation.basket):
            continue
        if require_taste and not has_taste(evaluation.basket):
            continue
        if evaluation.hard_exclusion == "LOCAL_CURRENCY_NOT_CONFIRMED":
            if not evaluation.violations:
                local_currency_count += 1
            continue
        if evaluation.hard_exclusion is not None or len(evaluation.violations) != 1:
            continue
        violation = evaluation.violations[0]
        if violation["code"] == "BUDGET_EXCEEDED":
            budget_needs.append(violation["actual"])
        elif violation["code"] == "DELIVERY_TIME_EXCEEDED":
            minute_needs.append(violation["actual"])

    if budget_needs:
        budget_needs.sort()
        count = min(needed, len(budget_needs))
        suggested = int(math.ceil(budget_needs[count - 1] / 1000.0) * 1000)
        suggestions.append({
            "slot": "budget_krw",
            "current_value": constraints.get("budget_krw"),
            "suggested_value": suggested,
            "additional_candidates": count,
            "message": f"예산을 {suggested:,}원으로 올리면 {count}곳을 더 볼 수 있어요.",
        })

    if minute_needs:
        minute_needs.sort()
        count = min(needed, len(minute_needs))
        suggested = minute_needs[count - 1]
        suggestions.append({
            "slot": "max_delivery_minutes",
            "current_value": constraints.get("max_delivery_minutes"),
            "suggested_value": suggested,
            "additional_candidates": count,
            "message": f"배달 시간을 {suggested}분까지 늘리면 {count}곳을 더 볼 수 있어요.",
        })

    if local_currency_count > 0:
        count = min(needed, local_currency_count)
        suggestions.append({
            "slot": "payment_requirements.local_currency_only",
            "current_value": True,
            "suggested_value": False,
            "additional_candidates": count,
            "message": f"지역화폐가 아닌 결제수단도 괜찮다면 {count}곳을 더 볼 수 있어요.",
        })

    return suggestions


# ---------------------------------------------------------------- 비슷한 메뉴 단계 (이슈 #37)

@dataclass
class Tier:
    """후보를 찾는 한 단계. 앞 단계에서 모자라면 다음 단계로 채운다."""

    code: str  # EXACT · SIMILAR_WORD_TASTE · SIMILAR_TASTE · SIMILAR_WORD · SAME_KIND
    constraints: dict
    label: str | None  # 후보 카드에 붙일 짧은 이름 (요청 그대로면 None)


def profile_of(keyword: str) -> dict | None:
    """사용자 메뉴 단어 → 핵심 단어·맛 사전. '짜장면'처럼 키가 들어 있어도 찾는다."""
    if keyword in DISH_PROFILES:
        return DISH_PROFILES[keyword]
    for key in sorted(DISH_PROFILES, key=len, reverse=True):
        if key in keyword:
            return DISH_PROFILES[key]
    return None


def cuisine_from_dish(constraints: dict) -> bool:
    """음식 종류가 메뉴 단어에서만 나온 것인지 ('마라탕' → 중식). 그렇다면 비슷한 메뉴는 다른 종류에서도 찾는다."""
    info = CUISINES.get(constraints.get("cuisine") or "")
    if info is None:
        return False
    return any(keyword in info["triggers"] for keyword in constraints.get("menu_keywords") or [])


def build_tiers(constraints: dict) -> list[Tier]:
    keywords = constraints.get("menu_keywords") or []
    if not keywords:
        return [Tier("EXACT", constraints, None)]

    words: list[str] = []
    tastes: list[str] = []
    for keyword in keywords:
        profile = profile_of(keyword) or {"words": [], "tastes": []}
        for word in profile["words"]:
            if word not in words:
                words.append(word)
        for code in profile["tastes"]:
            if code not in tastes:
                tastes.append(code)
    max_spice = constraints.get("max_spice_level")
    if max_spice is not None and max_spice <= 1 and "SPICY" in tastes:
        tastes.remove("SPICY")  # '안 매운 마라탕' → 매운 맛으로 넓히지 않는다
    user_tastes = list(constraints.get("taste_preferences") or [])
    all_tastes = user_tastes + [code for code in tastes if code not in user_tastes]

    def variant(menu_keywords: list[str], taste_codes: list[str], keep_cuisine: bool) -> dict:
        out = dict(constraints, menu_keywords=menu_keywords, taste_preferences=taste_codes)
        if not keep_cuisine:
            out["cuisine"] = None
        return out

    cuisines = [True, False] if cuisine_from_dish(constraints) else [True]
    # 화면 배지는 짧게 (이슈 #68): 어떤 맛인지(바삭한 등)까지는 쓰지 않는다
    taste_label = "비슷한 맛" if tastes else None
    tiers = [Tier("EXACT", constraints, None)]
    if words and tastes:
        for keep in cuisines:
            tiers.append(Tier("SIMILAR_WORD_TASTE", variant(words, all_tastes, keep), "비슷한 메뉴"))
    word_tiers = [Tier("SIMILAR_WORD", variant(words, user_tastes, keep), "비슷한 메뉴") for keep in cuisines] if words else []
    taste_tiers = [Tier("SIMILAR_TASTE", variant([], all_tastes, keep), taste_label) for keep in cuisines] if tastes else []
    # 맛이 둘 이상이면(마라탕 = 매운·국물) 맛이 그 음식의 정체성 → 같은 맛을 먼저
    tiers += taste_tiers + word_tiers if len(tastes) >= 2 else word_tiers + taste_tiers
    tiers.append(Tier("SAME_KIND", variant([], user_tastes, True), "같은 종류"))
    return tiers


def evaluate_all(restaurants: list[Restaurant], menus_by_restaurant: dict[str, list[Menu]],
                 constraints: dict, now: datetime | None) -> list[Evaluation]:
    evaluations = []
    for restaurant in restaurants:
        menus = menus_by_restaurant.get(restaurant.restaurant_id, [])
        evaluation = evaluate(restaurant, menus, constraints)
        # 음식 종류가 다른 가게는 그 이유를 그대로 둔다 (제외 사유 기록이 '영업시간 외'로 부풀지 않게)
        if (
            now is not None
            and evaluation.hard_exclusion != "CUISINE_MISMATCH"
            and is_open(restaurant.opening_hours, now) is False
        ):
            evaluation.hard_exclusion = "CLOSED_NOW"
        evaluations.append(evaluation)
    return evaluations


def has_word_and_taste(basket: Basket) -> bool:
    for item, _ in basket.lines:
        if item.keyword_hit and item.taste_hit:
            return True
    return False


def tier_fits(tier: Tier, basket: Basket) -> bool:
    if tier.code == "SIMILAR_WORD_TASTE":
        return has_word_and_taste(basket)
    if tier.code == "SIMILAR_WORD":
        return has_keyword(basket)
    if tier.code == "SIMILAR_TASTE":
        return has_taste(basket)
    return True


def sort_meeting(evaluations: list[Evaluation], tastes: list[str]) -> list[Evaluation]:
    # 맛을 말했으면 그 맛을 주로 파는 가게를 먼저 세우고, 모자라면 그 맛 메뉴가 있는 다른 가게로 채운다
    # (매운 메뉴가 하나뿐인 타코야끼집이 별점만으로 떡볶이·짬뽕집을 앞서지 않게)
    return sorted(evaluations, key=lambda e: (not is_taste_main(e, tastes), -e.score))


def sort_near_miss(evaluations: list[Evaluation], tastes: list[str]) -> list[Evaluation]:
    return sorted(
        evaluations,
        key=lambda e: (len(e.violations), not is_taste_main(e, tastes), round(miss_distance(e), 2), -e.score),
    )


def _eun(word: str) -> str:
    """'레몬에이드' → '는', '짜장' → '은' (받침 있으면 '은')."""
    code = ord(word[-1]) - 0xAC00 if word else -1
    return "은" if 0 <= code < 11172 and code % 28 else "는"


def _eul(word: str) -> str:
    """목적격 조사: 받침 있으면 '을', 없으면 '를' (한글이 아니면 '을(를)')."""
    last = word.rstrip()[-1:] if word else ""
    if not ("가" <= last <= "힣"):
        return "을(를)"
    return "을" if (ord(last) - 0xAC00) % 28 else "를"


def missing_notice(constraints: dict, exact: list[Evaluation], found_exact: int) -> str | None:
    """요청한 메뉴를 다 못 채웠을 때 화면 위에 보여 줄 한 문장.

    짧게 (이슈 #68): 영업시간이 아닌 가게가 있다는 것만 알리고 가게 이름·영업시간은 쓰지 않는다.
    """
    keywords = constraints.get("menu_keywords") or []
    if not keywords:
        return None
    joined = ", ".join(keywords)
    obj = f"'{joined}'{_eul(joined)}"
    if found_exact:
        return f"{obj} 담을 수 있는 가게는 {found_exact}곳이에요. 나머지는 비슷한 메뉴예요."
    closed = [e for e in exact if e.hard_exclusion == "CLOSED_NOW" and e.basket is not None and has_keyword(e.basket)]
    if closed:
        return f"{obj} 파는 가게가 지금 영업시간이 아니라 비슷한 메뉴로 골랐어요."
    selling = [e for e in exact if e.basket is not None and has_keyword(e.basket)]
    if selling:
        return f"조건 안에서 {obj} 담을 수 있는 가게가 없어 비슷한 메뉴로 골랐어요."
    return f"{obj} 파는 가게가 없어 비슷한 메뉴로 골랐어요."


def similar_reason(tier: Tier, basket: Basket, keywords: list[str]) -> str | None:
    if tier.code == "EXACT":
        return None
    joined = ", ".join(keywords)
    if tier.code == "SAME_KIND":
        return f"요청한 '{joined}' 메뉴는 조건에 맞는 것이 없어 비슷한 가게를 골랐습니다."
    pick = None
    for item, _ in basket.lines:
        if tier_fits(tier, Basket(lines=[(item, 1)], subtotal_krw=0, delivery_fee_krw=0)):
            pick = item.menu.name
            break
    # 어떤 메뉴인지는 카드의 메뉴 줄에 이미 있으니 문장은 짧게 통일한다 (이슈 #68)
    return f"'{joined}' 대신 비슷한 메뉴를 담았어요." if pick else None


def exclusion_trace(evaluations: list[Evaluation], constraints: dict, chosen_ids: set[str],
                    require_keyword: bool, require_taste: bool) -> list[dict]:
    """요청 그대로(EXACT) 평가한 결과에서, 후보가 되지 못한 가게와 그 이유 (이슈 #48, 점주 대시보드용).

    음식 종류가 다른 가게는 남기지 않는다 (요청마다 모든 가게를 쌓지 않게).
    사유: CLOSED_NOW · BELOW_MIN_ORDER · NO_ORDERABLE_MENU · LOCAL_CURRENCY_NOT_CONFIRMED (평가에서 바로 빠짐)
          BUDGET_EXCEEDED · DELIVERY_TIME_EXCEEDED (조건을 벗어남)
          NO_REQUESTED_MENU (말한 메뉴가 없음) · NO_TASTE_MATCH (원하는 맛 메뉴가 없음)
          OUT_OF_RANK (조건은 맞지만 순위에서 밀림)
    """
    keywords = constraints.get("menu_keywords") or []
    tastes = constraints.get("taste_preferences") or []
    ranked = sorted(
        (e for e in evaluations if e.hard_exclusion is None and e.basket is not None and not e.violations),
        key=lambda e: -e.score,
    )
    rank_of = {e.restaurant.restaurant_id: i + 1 for i, e in enumerate(ranked)}
    out = []
    for e in evaluations:
        rid = e.restaurant.restaurant_id
        if rid in chosen_ids or e.hard_exclusion == "CUISINE_MISMATCH":
            continue
        detail: dict = {}
        if e.hard_exclusion is not None:
            reason = e.hard_exclusion
            if reason == "CLOSED_NOW":
                detail = {"opening_hours": e.restaurant.opening_hours}
        elif e.basket is None:
            reason = "NO_ORDERABLE_MENU"
        elif keywords and require_keyword and not has_keyword(e.basket):
            reason = "NO_REQUESTED_MENU"
        elif tastes and require_taste and not has_taste(e.basket):
            reason = "NO_TASTE_MATCH"
        elif e.violations:
            v = e.violations[0]
            reason = v["code"]
            detail = {"limit": v.get("limit"), "actual": v.get("actual")}
        else:
            reason = "OUT_OF_RANK"
            detail = {"rank": rank_of.get(rid)}
        out.append({"restaurant_id": rid, "reason": reason, "detail": detail})
    return out


def search(
    db: Session, constraints: dict, top_n: int, now: datetime | None = None,
    trace: list[dict] | None = None, exclude_restaurant_ids: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """브랜드를 말했으면(constraints.brands) 그 브랜드 가게에서 먼저 찾고, 없으면 전체에서 찾아 그렇다고 알린다."""
    wanted = constraints.get("brands") or []
    if wanted:
        bodies, suggestions = _search(
            db, constraints, top_n, now, trace=None, only_brands=wanted, exclude=exclude_restaurant_ids
        )
        if bodies:
            if trace is not None:  # 제외 기록은 브랜드 가게만이 아니라 전체 기준으로 남긴다
                _search(db, constraints, top_n, now, trace=trace, exclude=exclude_restaurant_ids)
            return bodies, suggestions
    bodies, suggestions = _search(db, constraints, top_n, now, trace=trace, exclude=exclude_restaurant_ids)
    if wanted and bodies:
        names = ", ".join(f"'{b}'" for b in wanted)
        bodies[0]["search_notice"] = f"{names} 매장은 지금 조건에 맞게 주문할 수 없어 다른 가게를 골랐어요."
    return bodies, suggestions


def _search(
    db: Session, constraints: dict, top_n: int, now: datetime | None = None,
    trace: list[dict] | None = None, only_brands: list[str] | None = None,
    exclude: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """후보 JSON 목록과 완화 제안 목록을 돌려준다.

    now가 있으면 그 시각에 영업하지 않는 식당(영업시간을 아는 식당만)을 후보에서 뺀다.
    요청한 메뉴(menu_keywords)를 담을 수 있는 가게가 모자라면 비슷한 메뉴 단계(build_tiers)로 채운다.
    첫 후보에 'search_notice'(왜 비슷한 메뉴인지)가, 비슷한 메뉴 후보에 'match'가 붙는다.
    trace 목록을 넘기면 후보가 되지 못한 가게와 이유를 채운다 (exclusion_trace).
    """
    restaurants = list(db.scalars(select(Restaurant)))
    if only_brands:
        restaurants = [r for r in restaurants if brand_of(r.name) in only_brands]
    if exclude:  # 재추천 (이슈 #68): 이미 보여 준 가게는 빼고
        restaurants = [r for r in restaurants if r.restaurant_id not in exclude]
    menus_by_restaurant: dict[str, list[Menu]] = {}
    types = ORDERABLE_TYPES + SIDE_TYPES + (DRINK_TYPES if drinks.is_drink_request(constraints) else ())
    for menu in db.scalars(select(Menu).where(Menu.menu_type.in_(types))):
        menus_by_restaurant.setdefault(menu.restaurant_id, []).append(menu)

    tiers = build_tiers(constraints)
    keywords = constraints.get("menu_keywords") or []
    exact_evaluations: list[Evaluation] = []
    exact_meet: list[tuple[Tier, Evaluation]] = []
    exact_near: list[tuple[Tier, Evaluation]] = []
    other_meet: list[tuple[Tier, Evaluation]] = []
    other_near: list[tuple[Tier, Evaluation]] = []
    require_keyword = require_taste = False

    def brands(pairs: list[tuple[Tier, Evaluation]]) -> int:
        return len({brand_of(e.restaurant.name) for _, e in pairs})

    for tier in tiers:
        evaluations = evaluate_all(restaurants, menus_by_restaurant, tier.constraints, now)
        showable = [e for e in evaluations if e.hard_exclusion is None and e.basket is not None]
        if tier.code == "EXACT":
            exact_evaluations = evaluations
            # 사용자가 말한 메뉴를 담은 가게만 이 단계의 후보 (없으면 다음 단계가 비슷한 메뉴로 채운다)
            if keywords:
                showable = [e for e in showable if has_keyword(e.basket)]
                require_keyword = bool(showable)
            # 원하는 맛 메뉴를 담은 가게가 하나라도 있으면 그 가게들만
            if tier.constraints.get("taste_preferences"):
                with_taste = [e for e in showable if has_taste(e.basket)]
                if with_taste:
                    showable = with_taste
                    require_taste = True
        else:
            showable = [e for e in showable if tier_fits(tier, e.basket)]
        tastes = tier.constraints.get("taste_preferences") or []
        meet = [(tier, e) for e in sort_meeting([e for e in showable if not e.violations], tastes)]
        near = [(tier, e) for e in sort_near_miss([e for e in showable if e.violations], tastes)]
        if tier.code == "EXACT":
            exact_meet, exact_near = meet, near
        else:
            other_meet += meet
            other_near += near
        # 요청 메뉴(조건 일부 벗어남 포함)와 비슷한 메뉴로 충분히 찼으면 더 넓히지 않는다
        enough = exact_meet + other_meet if not keywords else exact_meet + exact_near + other_meet
        if (len(enough) if only_brands else brands(enough)) >= top_n:
            break

    used_brands: set[str] = set()
    used_stores: set[str] = set()  # 같은 가게가 여러 단계(정확·비슷한 메뉴)에서 나와도 한 번만

    def pick(pairs: list[tuple[Tier, Evaluation]]) -> list[tuple[Tier, Evaluation]]:
        picked = []
        for tier, evaluation in pairs:
            brand = brand_of(evaluation.restaurant.name)
            store = evaluation.restaurant.restaurant_id
            if store in used_stores:
                continue
            if brand in used_brands and not only_brands:  # 브랜드를 말했으면 같은 브랜드 여러 매장을 보여 준다
                continue
            picked.append((tier, evaluation))
            used_brands.add(brand)
            used_stores.add(store)
            if len(picked) == top_n:
                break
        return picked

    if keywords:
        # 말한 메뉴가 먼저: 조건을 조금 벗어나도(예산 초과 등) 비슷한 메뉴보다 앞에 둔다
        chosen = pick(exact_meet + exact_near + other_meet)
        near_pool = other_near
    else:
        chosen = pick(exact_meet)
        near_pool = exact_near
    # 조건에 딱 맞는 가게가 모자라면 조건을 조금(30% 이내) 벗어난 가게로 채운다 — 화면은 '더 보기'로 접어 둔다.
    # 맞는 가게가 하나도 없으면 예전처럼 가장 가까운 가게를 (한도 없이) 보여 준다
    if chosen:
        near_pool = [(t, e) for t, e in near_pool if miss_distance(e) <= NEAR_MISS_FILL_MAX]
    filled: set[str] = set()
    if len(chosen) < top_n:
        fill_marks = bool(chosen)  # 맞는 가게가 있을 때 채운 것만 '더 보기'로 접는다
        for pair in pick(near_pool):
            if len(chosen) == top_n:
                break
            chosen.append(pair)
            if fill_marks:
                filled.add(pair[1].restaurant.restaurant_id)
    meeting_count = sum(1 for _, e in chosen if not e.violations)
    found_exact = sum(1 for tier, _ in chosen if tier.code == "EXACT")

    user_tastes = constraints.get("taste_preferences") or []
    suggestions = []
    if keywords and not require_keyword:
        suggestions.extend(
            s for s in keyword_suggestions(restaurants, menus_by_restaurant, constraints)
            if s["slot"] != "menu_keywords"  # '파는 가게가 없어요'는 search_notice가 더 정확히 알려 준다
        )
    if user_tastes and not require_taste:
        suggestions.append(taste_suggestion(constraints))
    if meeting_count < top_n:
        suggestions.extend(
            relaxation_suggestions(exact_evaluations, constraints, top_n - meeting_count, require_keyword, require_taste)
        )

    used_fallback = any(tier.code != "EXACT" for tier, _ in chosen)
    notice = missing_notice(constraints, exact_evaluations, found_exact) if used_fallback else None
    # 맞는 가게 + 채운 근접 후보 개수 안내는 화면이 실제로 보이는 개수로 만든다 (실시간 확인으로 숨는 후보가 있어서)
    near_count = len(chosen) - meeting_count
    if notice is None and near_count and not meeting_count:
        notice = "조건에 딱 맞는 가게가 없어 가장 가까운 가게를 골랐어요."
    bodies = []
    for tier, evaluation in chosen:
        body = candidate_body(evaluation, constraints)
        if tier.code != "EXACT":
            body["match"] = {"level": tier.code, "label": tier.label}
            reason = similar_reason(tier, evaluation.basket, keywords)
            if reason:
                body["reasons"].append(reason)
        elif user_tastes and not require_taste:
            body["reasons"].append(f"조건에 맞는 {taste_labels(user_tastes)} 메뉴가 없어 비슷한 가게를 골랐습니다.")
        if evaluation.restaurant.restaurant_id in filled:
            body["near_miss_fill"] = True  # 조건에 맞는 가게가 모자라 채운 근접 후보 (화면은 '더 보기'로 접음)
        bodies.append(body)
    if bodies and notice:
        bodies[0]["search_notice"] = notice
    if trace is not None:
        chosen_ids = {e.restaurant.restaurant_id for _, e in chosen}
        trace.extend(exclusion_trace(exact_evaluations, constraints, chosen_ids, require_keyword, require_taste))
    return bodies, suggestions


def taste_suggestion(constraints: dict) -> dict:
    """원하는 맛 메뉴를 담을 수 있는 가게가 없을 때. 맛을 하나로 줄이거나 다른 조건을 풀어 보라고 알린다."""
    tastes = constraints["taste_preferences"]
    labels = taste_labels(tastes)
    if len(tastes) > 1:
        message = f"'{labels}'을 모두 만족하는 메뉴를 찾지 못했어요. 맛을 하나만 골라 보세요."
    else:
        message = f"조건에 맞는 {labels} 메뉴를 찾지 못했어요. 음식 종류·예산을 바꾸거나 맛 조건을 빼 보세요."
    return {
        "slot": "taste_preferences",
        "current_value": tastes,
        "suggested_value": [],
        "additional_candidates": 0,
        "message": message,
    }


def keyword_suggestions(restaurants: list[Restaurant], menus_by_restaurant: dict[str, list[Menu]],
                        constraints: dict) -> list[dict]:
    """요청 메뉴(예: 떡볶이)를 담지 못했을 때, 맵기 조건 때문인지 아예 없는지 알려 준다."""
    keywords = constraints["menu_keywords"]
    max_spice = constraints.get("max_spice_level")

    spicy_levels = []
    stores = set()
    for restaurant in restaurants:
        if not matches_cuisine(restaurant, constraints.get("cuisine")):
            continue
        for menu in menus_by_restaurant.get(restaurant.restaurant_id, []):
            if menu.is_sold_out or not name_has_any_keyword(menu.name, keywords):
                continue
            if max_spice is not None and menu.spice_level is not None and menu.spice_level > max_spice:
                spicy_levels.append(menu.spice_level)
                stores.add(restaurant.restaurant_id)

    joined = ", ".join(keywords)
    if spicy_levels:
        level = min(spicy_levels)
        return [{
            "slot": "max_spice_level",
            "current_value": max_spice,
            "suggested_value": level,
            "additional_candidates": len(stores),
            "message": f"맵기를 {level}단계까지 허용하면 '{joined}'를 파는 {len(stores)}곳을 볼 수 있어요.",
        }]
    return [{
        "slot": "menu_keywords",
        "current_value": keywords,
        "suggested_value": [],
        "additional_candidates": 0,
        "message": f"'{joined}' 메뉴를 파는 가게를 찾지 못했어요. 다른 메뉴로 바꿔 보세요.",
    }]
