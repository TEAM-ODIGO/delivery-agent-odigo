"""A-05 음식점·메뉴 탐색과 점수 계산.

순서
1. 가게 거르기: 음식 종류, '지역화폐만' 조건
2. 가게마다 담을 수 있는 메뉴 거르기: 품절·주류·제외 재료·맵기·음식 종류 단어
3. 인원수만큼 먹을 수 있는 장바구니(메뉴 1~2종)를 만들어 최소주문금액·배달비·예산을 계산
4. 배달 시간·예산을 벗어나면 violations에 적고, 점수를 매겨 순위를 정함
5. 모두 만족하는 후보가 모자라면 조건을 얼마나 풀면 되는지(relaxation_suggestions) 계산

A는 다른 모듈을 부르지 않는다. 지역화폐 여부도 restaurant 테이블 열만 읽는다.
"""

import math
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.discovery.catalog_models import Menu, Restaurant
from app.modules.discovery.constants import CUISINES, SPICY_NAME_WORDS

ORDERABLE_TYPES = ("main", "set")
MIN_ITEM_PRICE_KRW = 1000  # 이보다 싼 메인은 옵션성 항목으로 보고 담지 않음
MIN_PRICE_PER_SERVING_KRW = 5000  # 1인분은 적어도 이 정도 가격이라고 본다 (핫도그 1개를 한 끼로 세지 않게)
LIGHT_ITEM_PRICE_KRW = 4000  # 이보다 싼 메뉴(김말이·너겟 등)는 단독으로 한 끼를 채우지 않고 곁들이로만 담는다
SIDE_LIKE_PRICE_RATIO = 0.4  # 가게 메인 메뉴 중간 가격의 40% 미만이면 사이드성 메뉴로 보고 뺀다
TOP_ITEMS_PER_RESTAURANT = 10
MAX_QUANTITY = 10
VIOLATION_PENALTY = 20.0

# 이름에 이런 단어가 있으면 보통 2인 이상이 먹는 메뉴로 본다 (인분 정보가 없을 때만)
SHARING_WORDS = ["치킨", "마리", "반반", "피자", "족발", "보쌈", "찜닭", "아구찜", "해물찜", "전골", "감자탕"]
ONE_PERSON_WORDS = ["버거", "샌드위치", "1인", "혼밥", "도시락"]
# '2~3인분'이면 앞의 2를 쓴다 (적게 잡아야 인원수만큼 넉넉히 담음)
SERVINGS_IN_NAME = re.compile(r"(\d{1,2})(?:\s*~\s*\d{1,2})?\s*인(?!분주문)")


@dataclass
class Item:
    menu: Menu
    servings: float
    preference: float
    keyword_hit: bool
    spice_known: bool

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
        total = 0.0
        for item, quantity in self.lines:
            total += item.servings * quantity
        return total


@dataclass
class Evaluation:
    restaurant: Restaurant
    basket: Basket | None = None
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


def delivery_minutes_of(restaurant: Restaurant) -> int | None:
    """요기요 '23~38분' 중 늦은 쪽(38분)을 예상 시간으로 쓴다. 조건 판정을 보수적으로 하려고."""
    return restaurant.delivery_minutes_max


# ---------------------------------------------------------------- 메뉴 단위

def estimate_servings(menu: Menu) -> float:
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


def build_item(menu: Menu, constraints: dict) -> Item | None:
    """담을 수 없는 메뉴면 None."""
    if menu.is_sold_out or menu.is_alcohol:
        return None
    if menu.menu_type not in ORDERABLE_TYPES:
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
    if cuisine_info and cuisine_info["menu_words"]:
        if not contains_any(menu.name, cuisine_info["menu_words"]):
            return None
        if contains_any(menu.name, cuisine_info.get("not_menu_words", [])):
            return None

    keywords = constraints.get("menu_keywords") or []
    keyword_hit = contains_any(menu.name, keywords) is not None

    preference = 0.0
    if keyword_hit:
        preference += 3.0
    if menu.is_signature:
        preference += 1.5
    if menu.is_popular:
        preference += 1.5
    preference += min(math.log10(menu.review_count + 1), 2.0) * 0.5

    return Item(
        menu=menu,
        servings=estimate_servings(menu),
        preference=preference,
        keyword_hit=keyword_hit,
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


def candidate_baskets(restaurant: Restaurant, items: list[Item], people: int) -> list[Basket]:
    baskets = []

    # 메뉴 1종을 인원수만큼 (최소주문금액이 모자라면 한두 개 더)
    for item in items:
        if item.is_light:
            continue
        quantity = max(1, math.ceil(people / item.servings))
        for extra in range(3):
            if quantity + extra > MAX_QUANTITY:
                break
            basket = make_basket(restaurant, [(item, quantity + extra)])
            if basket is not None:
                baskets.append(basket)
                break

    # 2명 이상이면 서로 다른 메뉴 2종 조합도 본다
    if people >= 2:
        top = items[:8]
        for first_index in range(len(top)):
            for second_index in range(first_index + 1, len(top)):
                first = top[first_index]
                second = top[second_index]
                if first.is_light:
                    first, second = second, first  # 곁들이는 1개만 담고 주메뉴 수량으로 인원을 채운다
                if first.is_light:
                    continue
                remaining = people - second.servings
                first_quantity = max(1, math.ceil(remaining / first.servings))
                if first_quantity > MAX_QUANTITY:
                    continue
                basket = make_basket(restaurant, [(first, first_quantity), (second, 1)])
                if basket is not None and basket.servings >= people:
                    baskets.append(basket)

    return baskets


def basket_preference(basket: Basket) -> float:
    total = 0.0
    for item, _ in basket.lines:
        total += item.preference
    return total / len(basket.lines)


def has_keyword(basket: Basket) -> bool:
    for item, _ in basket.lines:
        if item.keyword_hit:
            return True
    return False


def choose_basket(baskets: list[Basket], budget_krw: int | None) -> Basket | None:
    """예산 안이면 선호도가 높은 것, 예산 안이 하나도 없으면 가장 싼 것 (근접 후보용).
    사용자가 말한 메뉴(menu_keywords)가 들어간 장바구니가 있으면 그것들 중에서만 고른다."""
    if not baskets:
        return None

    with_keyword = [basket for basket in baskets if has_keyword(basket)]
    if with_keyword:
        baskets = with_keyword

    if budget_krw is None:
        within = baskets
    else:
        within = [basket for basket in baskets if basket.total_krw <= budget_krw]

    if within:
        within.sort(key=lambda basket: (-basket_preference(basket), basket.total_krw))
        return within[0]

    baskets.sort(key=lambda basket: basket.total_krw)
    return baskets[0]


# ---------------------------------------------------------------- 평가·점수

def evaluate(restaurant: Restaurant, menus: list[Menu], constraints: dict) -> Evaluation:
    evaluation = Evaluation(restaurant=restaurant)

    if not matches_cuisine(restaurant, constraints.get("cuisine")):
        evaluation.hard_exclusion = "CUISINE_MISMATCH"
        return evaluation

    items = []
    for menu in menus:
        item = build_item(menu, constraints)
        if item is not None:
            items.append(item)
    items = drop_side_like(items)
    items.sort(key=lambda item: -item.preference)
    items = items[:TOP_ITEMS_PER_RESTAURANT]

    if not items:
        evaluation.hard_exclusion = "NO_ORDERABLE_MENU"
        return evaluation

    people = constraints.get("people") or 1
    budget = constraints.get("budget_krw")
    basket = choose_basket(candidate_baskets(restaurant, items, people), budget)
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
    조건 위반마다 -20."""
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

    # 키워드 가점(3점)은 아래 keyword_score에서 따로 주므로 여기서는 인기·대표·리뷰만 본다
    preference = basket_preference(basket)
    if has_keyword(basket):
        preference -= 3.0
    menu_score = clamp(preference / 3.0, 0.0, 1.0) * 10.0

    keywords = constraints.get("menu_keywords") or []
    if not keywords or has_keyword(basket):
        keyword_score = 10.0
    else:
        keyword_score = 0.0

    if is_primary_cuisine(restaurant, constraints.get("cuisine")):
        cuisine_score = 10.0
    else:
        cuisine_score = 4.0

    total = rating_score + review_score + speed_score + price_score + menu_score + cuisine_score + keyword_score
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
        first = f"예산 안({budget:,}원)이고 {minutes}분 안에 도착합니다."
    elif budget is not None:
        first = f"예산보다 {basket.total_krw - budget:,}원 많지만 가장 가까운 후보입니다. {minutes}분 안에 도착합니다."
    else:
        first = f"총 {basket.total_krw:,}원이고 {minutes}분 안에 도착합니다."

    details = [f"별점 {float(restaurant.rating):.1f}(리뷰 {restaurant.review_count:,}개)"]
    if constraints.get("people"):
        details.append(f"{constraints['people']}명 기준 약 {basket.servings:g}인분")
    for item, _ in basket.lines:
        if item.keyword_hit:
            details.append(f"'{item.menu.name}' 포함")
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


def candidate_body(evaluation: Evaluation, constraints: dict) -> dict:
    restaurant = evaluation.restaurant
    basket = evaluation.basket
    items = []
    for item, quantity in basket.lines:
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
                           require_keyword: bool) -> list[dict]:
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


def search(db: Session, constraints: dict, top_n: int) -> tuple[list[dict], list[dict]]:
    """후보 JSON 목록과 완화 제안 목록을 돌려준다."""
    restaurants = list(db.scalars(select(Restaurant)))
    menus_by_restaurant: dict[str, list[Menu]] = {}
    for menu in db.scalars(select(Menu).where(Menu.menu_type.in_(ORDERABLE_TYPES))):
        menus_by_restaurant.setdefault(menu.restaurant_id, []).append(menu)

    evaluations = []
    for restaurant in restaurants:
        menus = menus_by_restaurant.get(restaurant.restaurant_id, [])
        evaluations.append(evaluate(restaurant, menus, constraints))

    showable = [e for e in evaluations if e.hard_exclusion is None and e.basket is not None]

    # 사용자가 말한 메뉴(예: 족발)를 파는 가게가 하나라도 있으면 그 가게들만 후보로 둔다
    require_keyword = False
    if constraints.get("menu_keywords"):
        with_keyword = [e for e in showable if has_keyword(e.basket)]
        if with_keyword:
            showable = with_keyword
            require_keyword = True

    meeting = [e for e in showable if not e.violations]
    near_miss = [e for e in showable if e.violations]

    meeting.sort(key=lambda e: -e.score)
    near_miss.sort(key=lambda e: (len(e.violations), round(miss_distance(e), 2), -e.score))

    meeting_chosen = pick_top(meeting, top_n)
    if meeting_chosen:
        chosen = meeting_chosen
    else:
        # 조건을 모두 만족하는 후보가 없으면 가장 가까운 후보를 벗어난 조건과 함께 보여 준다
        chosen = pick_top(near_miss, top_n)

    suggestions = []
    if constraints.get("menu_keywords") and not require_keyword:
        suggestions.extend(keyword_suggestions(restaurants, menus_by_restaurant, constraints))
    if len(meeting_chosen) < top_n:
        suggestions.extend(
            relaxation_suggestions(evaluations, constraints, top_n - len(meeting_chosen), require_keyword)
        )

    bodies = [candidate_body(e, constraints) for e in chosen]
    if constraints.get("menu_keywords") and not require_keyword:
        missing = ", ".join(constraints["menu_keywords"])
        for body in bodies:
            body["reasons"].append(f"요청한 '{missing}' 메뉴는 조건에 맞는 것이 없어 비슷한 가게를 골랐습니다.")
    return bodies, suggestions


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
            if menu.is_sold_out or not contains_any(menu.name, keywords):
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
