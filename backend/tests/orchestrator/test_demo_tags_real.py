"""시연 태그를 진짜 A·B 구성에서도 흉내 내기 (이슈 #46).

예전에는 #품절·#모두품절·#혜택불일치·#B08장애·#A장애가 가짜 A·B일 때만 동작해서,
진짜 데이터로 띄운 서버에서 '시연 시나리오 넣기'를 눌러도 아무 일도 없었다.
"""

import pytest

from app.core.errors import AppError
from app.fakes.scenario import ScenarioBook
from app.modules.orchestrator import wiring


class InnerB:
    def __init__(self):
        self.calls = []

    def create_order_plan(self, body, *, idempotency_key):
        self.calls.append("B-01")
        return {
            "request_id": body["request_id"],
            "order_plans": [{"order_plan_id": "op1"}],
            "issues": [],
        }

    def bind_benefit(self, order_plan_id, body, *, idempotency_key):
        self.calls.append("B-05")
        return {"ok": True}

    def record_payment_event(self, order_plan_id, body, *, idempotency_key):
        self.calls.append("B-08")
        return {"ok": True}

    def check_execution(self, order_plan_id, body):
        return {"passed": True}


def make(flags):
    book = ScenarioBook()
    book.set("R1", flags)
    inner = InnerB()
    return wiring._ScenarioOrdering(inner, book), inner


BODY = {"request_id": "R1", "candidate_id": "CAND001", "items": [{"menu_id": "M1", "quantity": 1}]}


def test_soldout_first_only_first_b01():
    b, inner = make({"SOLDOUT_FIRST"})
    first = b.create_order_plan(BODY, idempotency_key="k1")
    assert first["order_plans"] == [] and first["issues"][0]["code"] == "MENU_SOLD_OUT"
    assert first["next_action"] == "RESEARCH_REQUIRED"
    assert b.create_order_plan(BODY, idempotency_key="k1") is first  # 같은 키 = 같은 응답
    second = b.create_order_plan(BODY, idempotency_key="k2")
    assert second["order_plans"] and inner.calls == ["B-01"]


def test_soldout_all_every_time():
    b, inner = make({"SOLDOUT_ALL"})
    for key in ("k1", "k2", "k3"):
        assert b.create_order_plan(BODY, idempotency_key=key)["order_plans"] == []
    assert inner.calls == []


def test_stale_benefit_once_and_b08_fail_once():
    b, inner = make({"B05_STALE", "B08_FAIL_ONCE"})
    b.create_order_plan(BODY, idempotency_key="k1")
    with pytest.raises(AppError) as e:
        b.bind_benefit("op1", {}, idempotency_key="b1")
    assert (e.value.status, e.value.code) == (409, "STALE_CALCULATION")
    assert b.bind_benefit("op1", {}, idempotency_key="b2") == {"ok": True}
    with pytest.raises(AppError) as e:
        b.record_payment_event("op1", {}, idempotency_key="e1")
    assert e.value.status == 503
    assert b.record_payment_event("op1", {}, idempotency_key="e2") == {"ok": True}
    assert b.check_execution("op1", {}) == {"passed": True}  # 나머지는 그대로 넘김


def test_no_tags_passes_through():
    b, inner = make(set())
    assert b.create_order_plan(BODY, idempotency_key="k1")["order_plans"]
    assert b.bind_benefit("op1", {}, idempotency_key="b1") == {"ok": True}
    assert inner.calls == ["B-01", "B-05"]


class InnerA:
    def create_request(self, body):
        return {"message": body["message"]}


def test_a_down_tag_only_in_demo_mode(monkeypatch):
    book = ScenarioBook()
    a = wiring._WithScenarioTags(InnerA(), book)
    monkeypatch.delenv("O_DEMO_SCENARIOS", raising=False)
    monkeypatch.delenv("O_DEMO_E_OVERRIDE", raising=False)
    assert a.create_request({"request_id": "R1", "message": "치킨 #A장애"}) == {"message": "치킨"}
    monkeypatch.setenv("O_DEMO_SCENARIOS", "1")
    with pytest.raises(AppError) as e:
        a.create_request({"request_id": "R2", "message": "치킨 #A장애"})
    assert e.value.status == 503


def test_demo_flags(monkeypatch):
    monkeypatch.delenv("O_DEMO_SCENARIOS", raising=False)
    monkeypatch.delenv("O_DEMO_E_OVERRIDE", raising=False)
    assert wiring.demo_scenarios_enabled() is False
    monkeypatch.setenv("O_DEMO_E_OVERRIDE", "1")  # 예전 이름도 인정
    assert wiring.demo_scenarios_enabled() is True


def test_demo_tags_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setenv("O_DEMO_SCENARIOS", "1")
    assert TestClient(create_app()).get("/orchestrator/demo-tags").json() == {"available": True}
