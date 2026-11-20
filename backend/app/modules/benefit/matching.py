"""C-08 식당 ↔ 조폐공사 가맹점 매칭. 상호·주소(도로명 정규화)·좌표 세 신호 중 두 개 이상 맞으면 같은 가게로 본다."""
import math
import re

NEAR_METERS = 100

_REGION_ALIASES = (("서울특별시", "서울"), ("부산광역시", "부산"), ("대구광역시", "대구"), ("인천광역시", "인천"),
                   ("광주광역시", "광주"), ("대전광역시", "대전"), ("울산광역시", "울산"),
                   ("경기도", "경기"), ("강원특별자치도", "강원"), ("제주특별자치도", "제주"))


def normalize_name(name: str | None) -> str:
    return re.sub(r"[\s()\[\]·.\-_/]", "", (name or "")).lower()


def normalize_address(address: str | None) -> str:
    text = re.sub(r"\([^)]*\)", "", address or "")            # 괄호 안 참고 정보 제거
    for full, short in _REGION_ALIASES:
        text = text.replace(full, short)
    return re.sub(r"[\s,.\-]", "", text).lower()


def distance_m(lat1, lng1, lat2, lng2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def is_match(restaurant, merchant: dict) -> bool:
    name_eq = bool(restaurant.name) and normalize_name(restaurant.name) == normalize_name(merchant.get("name"))
    addr_eq = bool(restaurant.address) and normalize_address(restaurant.address) == normalize_address(merchant.get("address"))
    near = (None not in (restaurant.lat, restaurant.lng, merchant.get("lat"), merchant.get("lng"))
            and distance_m(restaurant.lat, restaurant.lng, merchant["lat"], merchant["lng"]) <= NEAR_METERS)
    return sum((name_eq, addr_eq, near)) >= 2
