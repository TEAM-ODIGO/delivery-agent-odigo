"""실시간 식당 확인 도구 (이슈 ①): 판정 규칙과 동시 확인·조기 종료.

브라우저 없이 시험한다. 페이지에서 읽은 값(Observed)과 가짜 checker를 쓴다.
"""

import asyncio

from app.modules.livecheck.checker import (
    CheckItem,
    CheckResult,
    CheckTarget,
    Observed,
    closed_reason,
    delivery_fee,
    judge,
    parse_menu_card,
    read_items,
    run_checks,
)


def _target(rid="r1", price=6800, fee=3000, min_order=14000):
    return CheckTarget(
        restaurant_id=rid,
        name=f"식당 {rid}",
        items=[CheckItem("m1", price, "홍콩식프렌치토스트")],
        expected_delivery_fee_krw=fee,
        expected_min_order_krw=min_order,
    )


def _obs(**kw):
    base = dict(
        url="https://www.yogiyo.co.kr/mobile/#/r1/",
        menus_total=10,
        items={"m1": {"found": True, "price_krw": 6800, "sold_out": False}},
        delivery_fee_krw=3000,
        min_order_krw=14000,
    )
    base.update(kw)
    return Observed(**base)


# ---------------------------------------------------------------- 메뉴 카드 읽기
def test_parse_menu_card_takes_last_price_and_sold_out():
    assert parse_menu_card("홍콩식프렌치토스트 버터 듬뿍 6,800원") == {
        "price_krw": 6800,
        "sold_out": False,
    }
    assert parse_menu_card("세트 (2만원 이상 주문 시) 12,900원 품절")["price_krw"] == 12900
    assert parse_menu_card("세트 12,900원 품절")["sold_out"] is True
    assert parse_menu_card("가격 없음")["price_krw"] is None


def test_read_items_marks_missing_and_uses_first_card():
    menus = [
        {"id": "m1", "text": "토스트 6,800원"},
        {"id": "m1", "text": "토스트 9,999원"},
        {"id": "m2", "text": "커피 3,000원"},
    ]
    got = read_items(menus, [CheckItem("m1"), CheckItem("m9")])
    assert got["m1"]["found"] is True and got["m1"]["price_krw"] == 6800
    assert got["m9"] == {"found": False}


def test_read_items_falls_back_to_name_when_id_changed():
    menus = [
        {"id": "x9", "text": "김말이 4개 2,500원"},
        {"id": "x8", "text": "꼬마김말이5개 2,500원"},
    ]
    got = read_items(menus, [CheckItem("old1", 2500, "김말이4개")])
    assert got["old1"]["found"] is True and got["old1"]["new_id"] == "x9"
    assert read_items(menus, [CheckItem("old2", 2500, "튀김")])["old2"] == {"found": False}


def test_delivery_fee_reads_free_and_falls_back_to_page():
    assert delivery_fee("주문표 배달요금 무료", "") == 0
    assert delivery_fee("주문표", "배달요금 3,300원 별도") == 3300
    assert delivery_fee("", "") is None


# ---------------------------------------------------------------- 판정
def test_judge_ok_when_everything_matches():
    assert judge(_target(), _obs()) == ("OK", [], None)


def test_judge_changed_lists_price_fee_and_min_order():
    obs = _obs(
        items={"m1": {"found": True, "price_krw": 7200, "sold_out": False}},
        delivery_fee_krw=2500,
        min_order_krw=15000,
    )
    status, changes, reason = judge(_target(), obs)
    assert status == "CHANGED" and reason is None
    assert changes == [
        "홍콩식프렌치토스트 6,800원 → 7,200원",
        "배달비 3,000원 → 2,500원",
        "최소 주문 금액 14,000원 → 15,000원",
    ]


def test_judge_unavailable_cases():
    assert judge(_target(), _obs(not_serving=True))[0] == "UNAVAILABLE"
    assert "없어요" in judge(_target(), _obs(items={"m1": {"found": False}}))[2]
    sold = _obs(items={"m1": {"found": True, "price_krw": 6800, "sold_out": True}})
    assert judge(_target(), sold) == ("UNAVAILABLE", [], "홍콩식프렌치토스트 품절")


def test_judge_redirect_and_changed_menu_id():
    assert judge(_target(), _obs(redirected=True, menus_total=0))[0] == "UNAVAILABLE"
    moved = _obs(
        items={"m1": {"found": True, "price_krw": 6800, "sold_out": False, "new_id": "m7"}}
    )
    assert judge(_target(), moved) == (
        "CHANGED",
        ["홍콩식프렌치토스트 메뉴 번호 바뀜 (m1 → m7)"],
        None,
    )


def test_judge_blocked_and_error():
    assert judge(_target(), _obs(denied=True))[0] == "BLOCKED"
    assert judge(_target(), _obs(menus_total=0))[0] == "ERROR"


def test_judge_ignores_values_it_could_not_read():
    obs = _obs(
        items={"m1": {"found": True, "price_krw": None, "sold_out": False}},
        delivery_fee_krw=None,
        min_order_krw=None,
    )
    assert judge(_target(), obs)[0] == "OK"


# ---------------------------------------------------------------- 동시 확인·조기 종료
class FakeChecker:
    """식당 id → 판정 순서. 동시에 몇 곳을 열었는지 기록한다."""

    def __init__(self, plan: dict[str, list[str]], delay=0.01):
        self.plan = {k: list(v) for k, v in plan.items()}
        self.delay = delay
        self.calls: list[str] = []
        self.open = 0
        self.max_open = 0

    async def __call__(self, t: CheckTarget) -> CheckResult:
        self.calls.append(t.restaurant_id)
        self.open += 1
        self.max_open = max(self.max_open, self.open)
        await asyncio.sleep(self.delay)
        self.open -= 1
        status = self.plan[t.restaurant_id].pop(0)
        return CheckResult(t.restaurant_id, status, seconds=self.delay)


def _run(plan, **kw):
    targets = [_target(rid) for rid in plan]
    checker = FakeChecker(plan)
    seen = []
    results = asyncio.run(run_checks(targets, checker, on_result=lambda i, r: seen.append(i), **kw))
    return results, checker, seen


def test_stops_after_want_passed_and_keeps_order():
    plan = {f"r{i}": ["OK"] for i in range(6)}
    results, checker, seen = _run(plan, concurrency=3, want=3)
    assert [r.restaurant_id for r in results] == list(plan)
    assert [r.status for r in results] == ["OK"] * 3 + ["SKIPPED"] * 3
    assert checker.calls == ["r0", "r1", "r2"]
    assert sorted(seen) == list(range(6))


def test_unavailable_candidates_are_replaced_by_next_ones():
    plan = {"r0": ["UNAVAILABLE"], "r1": ["OK"], "r2": ["UNAVAILABLE"], "r3": ["CHANGED"]}
    plan |= {"r4": ["OK"], "r5": ["OK"]}
    results, checker, _ = _run(plan, concurrency=1, want=3)
    assert [r.status for r in results] == [
        "UNAVAILABLE",
        "OK",
        "UNAVAILABLE",
        "CHANGED",
        "OK",
        "SKIPPED",
    ]


def test_never_opens_more_tabs_than_concurrency():
    plan = {f"r{i}": ["UNAVAILABLE"] for i in range(7)}
    results, checker, _ = _run(plan, concurrency=3, want=3)
    assert checker.max_open == 3
    assert len(checker.calls) == 7


def test_blocked_stops_the_rest():
    plan = {"r0": ["BLOCKED"], "r1": ["OK"], "r2": ["OK"], "r3": ["OK"]}
    results, checker, _ = _run(plan, concurrency=1, want=3)
    assert [r.status for r in results] == ["BLOCKED", "SKIPPED", "SKIPPED", "SKIPPED"]
    assert "막아서" in results[1].reason


def test_error_is_retried_once():
    plan = {"r0": ["ERROR", "OK"], "r1": ["ERROR", "ERROR"]}
    results, checker, _ = _run(plan, concurrency=1, want=3)
    assert [(r.status, r.attempts) for r in results] == [("OK", 2), ("ERROR", 2)]
    assert checker.calls == ["r0", "r0", "r1", "r1"]


# ---------------------------------------------------------------- 요기요 영업 상태로 사유 보여 주기
def test_closed_reason_from_yogiyo_open_status():
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 21, 11, 17, tzinfo=timezone(timedelta(hours=9)))
    assert closed_reason({"current": "TEMP_HOLIDAY"}, now) == "오늘 임시 휴무예요"
    assert closed_reason({"current": "HOLIDAY"}, now) == "오늘은 정기 휴무일이에요"
    later = {"current": "CLOSED", "next_opening_at": "2026-11-21T08:00:00.000Z"}  # 17:00 KST
    assert closed_reason(later, now) == "지금은 영업시간이 아니에요 (17:00에 영업 시작)"
    tomorrow = {"current": "PAUSE", "next_opening_at": "2026-11-22T02:00:00.000Z"}
    assert closed_reason(tomorrow, now) == "가게가 잠시 주문을 받지 않아요 (내일 11:00에 영업 시작)"
    assert closed_reason({"current": "OPENING_TIME"}, now) is None
    assert closed_reason({"current": "SOMETHING_NEW"}, now) is None
    assert closed_reason(None, now) is None


def test_judge_uses_open_status_even_without_not_serving_text():
    status, _, reason = judge(_target(), _obs(open_status={"current": "TEMP_HOLIDAY"}))
    assert (status, reason) == ("UNAVAILABLE", "오늘 임시 휴무예요")
    status, _, reason = judge(_target(), _obs(not_serving=True))
    assert reason == "지금 요기요 주문을 받지 않아요 (서비스 미제공)"  # 상태를 못 읽으면 예전 문구
    assert judge(_target(), _obs(open_status={"current": "OPENING_TIME"}))[0] == "OK"
