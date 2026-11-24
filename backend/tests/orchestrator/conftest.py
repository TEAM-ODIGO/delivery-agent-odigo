"""O 테스트 준비물: 가짜 A·B·C·E + 진짜 D + O를 테스트 DB 하나로 묶는다.

- 모든 모듈이 같은 가짜 시계(FakeClock)를 본다 → 만료 시각이 매번 같다.
- D는 O와 다른 세션을 쓴다 (실제 서버와 같은 구조).
- flow(): 주문 1건을 원하는 단계까지 진행하는 도우미.
"""

import os
from typing import Any

import pytest
from pydantic import TypeAdapter

from app.fakes import FakeWorld
from app.modules.delegation import DelegationService
from app.modules.delegation.benefits import CBenefitPort
from app.modules.delegation.schemas import AuthorizationCheckRequest
from app.modules.orchestrator.schemas import Reply, RunCreate
from app.modules.orchestrator.service import OrchestratorService

REPLY = TypeAdapter(Reply)
OK_MESSAGE = "둘이서 2만원 이하로 안 매운 치킨 빨리 시켜줘"
ADDRESS = {"region_code": "11140", "lat": 37.566, "lng": 126.982}
DELEGATION = {
    "allowed_actions": ["SEARCH", "BUILD_ORDER", "REQUEST_PAYMENT"],
    "allowed_payment_method_ids": ["CARD001", "LOCAL001"],
    "max_charge_krw": 20000,
}


def _real_c():
    """TEST_REAL_C=1이면 가짜 C 대신 진짜 C(app/modules/benefit)로 같은 시나리오를 돌린다."""
    if os.getenv("TEST_REAL_C") != "1":
        return None
    from app.modules.benefit import facade, service
    from app.modules.benefit.catalog_source import use_shared_restaurants

    service.reset_state()
    use_shared_restaurants()

    class RealC:
        recommend = staticmethod(facade.recommend)
        calculate = staticmethod(facade.calculate)
        list_methods = staticmethod(facade.list_payment_methods)
        record_usage = staticmethod(facade.record_usage)  # 로컬 확장: 결제 승인 → 남은 혜택 차감
        reverse_usage = staticmethod(facade.reverse_usage)  # 로컬 확장: 환불 → 되돌림

    return RealC()


class Env:
    def __init__(self, session_factory, clock):
        self.sf = session_factory
        self.clock = clock
        self.world = FakeWorld(
            read_approval=self._read_approval,
            check_authorization=self._check,
            benefit=_real_c(),
            now=clock,
        )
        self.payment = self.world.payment  # 테스트에서 바꿔 끼울 수 있게
        self.ordering = self.world.ordering

    # D (진짜) ------------------------------------------------------------------
    def d(self, session=None) -> DelegationService:
        port = CBenefitPort(
            calculate=self.world.benefit.calculate, list_methods=self.world.benefit.list_methods
        )
        return DelegationService(session or self.sf(), port, now=self.clock)

    def _read_approval(self, approval_id):
        with self.sf() as s:
            return self.d(s).get_approval(approval_id).model_dump(mode="json")

    def _check(self, body, request_id):
        with self.sf() as s:
            return (
                self.d(s)
                .check_authorization(
                    AuthorizationCheckRequest.model_validate(body), request_id_hint=request_id
                )
                .model_dump(mode="json")
            )

    # O -------------------------------------------------------------------------
    def svc(self, **over) -> OrchestratorService:
        kw = dict(
            discovery=self.world.discovery,
            ordering=self.ordering,
            benefit=self.world.benefit,
            payment=self.payment,
            delegation=self.d(),
            now=self.clock,
        )
        kw.update(over)
        return OrchestratorService(self.sf(), **kw)

    def start(self, message=OK_MESSAGE, key=None, user="U001", delegation=None) -> dict:
        body = RunCreate.model_validate(
            {
                "message": message,
                "delivery_address": ADDRESS,
                "delegation": delegation or DELEGATION,
            }
        )
        return self.svc().start_run(user, body, key)

    def reply(self, view: dict, key=None, user="U001", version=None, **body: Any) -> dict:
        data = {
            **body,
            "expected_state_version": view["state_version"] if version is None else version,
        }
        return self.svc().reply(user, view["request_id"], REPLY.validate_python(data), key)

    def get(self, view_or_id, user="U001") -> dict:
        rid = view_or_id if isinstance(view_or_id, str) else view_or_id["request_id"]
        return self.svc().get_run(user, rid)

    def events(self, view) -> list[dict]:
        return self.svc().list_events(view["request_id"])["events"]

    def flow(self, until: str, message=OK_MESSAGE, **kw) -> dict:
        """주문 1건을 until 상태까지 진행 (각 단계는 첫 번째 선택지)."""
        v = self.start(message, **kw)
        order = [
            "NEED_CLARIFICATION",
            "CANDIDATES_READY",
            "PAYMENT_METHOD_SELECTION",
            "AWAITING_APPROVAL",
            "COMPLETED",
        ]
        while v["state"] != until and v["state"] in order:
            if v["state"] == "NEED_CLARIFICATION":
                v = self.reply(
                    v,
                    type="CLARIFICATION",
                    answers=[
                        {"slot": "people", "value": 2},
                        {"slot": "budget_krw", "value": "20000"},
                    ],
                )
            elif v["state"] == "CANDIDATES_READY":
                v = self.reply(
                    v,
                    type="SELECT_CANDIDATE",
                    candidate_index=v["prompt"]["candidates"][0]["index"],
                )
            elif v["state"] == "PAYMENT_METHOD_SELECTION":
                v = self.reply(
                    v,
                    type="SELECT_PAYMENT_METHOD",
                    payment_method_id=v["prompt"]["options"][0]["payment_method_id"],
                )
            elif v["state"] == "AWAITING_APPROVAL":
                v = self.reply(v, type="APPROVE")
            else:
                break
        return v


@pytest.fixture
def env(session_factory, clock):
    return Env(session_factory, clock)
