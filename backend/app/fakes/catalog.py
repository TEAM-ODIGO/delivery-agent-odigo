"""가짜 공용 데이터 (요기요 수집 DB 대역): 식당·메뉴·옵션.

A는 후보를 고를 때, B는 가격·품절을 확인할 때, C는 지역화폐 가능 여부를 볼 때 이 값을 읽는다.
값은 명세 예시(예시치킨 역삼점, 후라이드치킨 17,000원 등)에 맞췄다.
"""

from dataclasses import dataclass, field

CATALOG_VERSION = "CAT-V1"


@dataclass(frozen=True)
class Option:
    option_id: str
    name: str
    price_krw: int


@dataclass(frozen=True)
class Menu:
    menu_id: str
    name: str
    description: str
    price_krw: int
    spice_level: int  # 0 = 안 매움
    serves: int  # 몇 명이 먹는 양인가 (수량 계산용)
    options: tuple[Option, ...] = ()


@dataclass(frozen=True)
class Restaurant:
    restaurant_id: str
    name: str
    cuisine: str
    rating: float
    delivery_minutes: int
    delivery_fee_krw: int
    accepts_livelihood_voucher: bool
    local_currency_merchant: bool | None  # None = 확인 불가
    menus: tuple[Menu, ...] = field(default_factory=tuple)

    @property
    def local_currency_basis(self) -> str | None:
        return "ASSUMED_FROM_VOUCHER" if self.local_currency_merchant is not None else None

    def summary(self) -> dict:
        """A-05·A-06 응답의 restaurant 블록."""
        return {
            "restaurant_id": self.restaurant_id,
            "name": self.name,
            "rating": self.rating,
            "delivery_minutes": self.delivery_minutes,
            "delivery_fee_krw": self.delivery_fee_krw,
            "accepts_livelihood_voucher": self.accepts_livelihood_voucher,
            "local_currency_merchant": self.local_currency_merchant,
            "local_currency_basis": self.local_currency_basis,
        }


RESTAURANTS: tuple[Restaurant, ...] = (
    Restaurant(
        "STORE101",
        "예시치킨 역삼점",
        "치킨",
        4.7,
        35,
        2000,
        True,
        True,
        (
            Menu(
                "MENU011",
                "후라이드치킨",
                "국내산 닭, 순한맛",
                17000,
                0,
                2,
                (Option("OPT001", "순살 변경", 1000),),
            ),
            Menu("MENU012", "양념치킨", "매콤달콤 양념", 18000, 1, 2),
            Menu("MENU013", "땅콩강정", "땅콩 소스를 버무린 강정", 19000, 1, 2),
        ),
    ),
    Restaurant(
        "STORE102",
        "예시치킨 강남점",
        "치킨",
        4.5,
        30,
        1500,
        True,
        True,
        (
            Menu("MENU021", "양념치킨", "매콤달콤 양념", 18000, 1, 2),
            Menu("MENU022", "간장반반치킨", "후라이드 반, 간장 반", 18000, 0, 2),
        ),
    ),
    Restaurant(
        "STORE103",
        "바삭치킨 을지로점",
        "치킨",
        4.2,
        45,
        3000,
        False,
        False,
        (Menu("MENU031", "크리스피치킨", "얇은 튀김옷, 순한맛", 16000, 0, 2),),
    ),
    Restaurant(
        "STORE201",
        "예시피자 을지로점",
        "피자",
        4.6,
        35,
        2000,
        True,
        True,
        (
            Menu(
                "MENU041",
                "페퍼로니 피자 L",
                "페퍼로니, 모차렐라",
                21000,
                0,
                3,
                (Option("OPT041", "치즈 크러스트", 3000),),
            ),
            Menu("MENU042", "마르게리타 피자 M", "토마토, 바질, 모차렐라", 16000, 0, 2),
        ),
    ),
    Restaurant(
        "STORE301",
        "홍짬뽕 을지로점",
        "중식",
        4.5,
        30,
        2000,
        False,
        None,
        (
            Menu("MENU061", "짬뽕", "얼큰한 해물 국물", 9000, 3, 1),
            Menu("MENU062", "짜장면", "춘장 소스", 7000, 0, 1),
            Menu("MENU063", "탕수육 소", "돼지고기 튀김, 새콤달콤 소스", 15000, 0, 2),
        ),
    ),
)

_BY_ID = {r.restaurant_id: r for r in RESTAURANTS}


def restaurant(restaurant_id: str) -> Restaurant | None:
    return _BY_ID.get(restaurant_id)


def menu(restaurant_id: str, menu_id: str) -> Menu | None:
    r = restaurant(restaurant_id)
    return next((m for m in r.menus if m.menu_id == menu_id), None) if r else None
