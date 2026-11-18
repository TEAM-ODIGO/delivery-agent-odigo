"""실제 A·B를 연결해 품절 뒤 다른 후보를 선택하는 흐름을 확인한다."""

from sqlalchemy.orm import sessionmaker

from app.core.db import Base, init_db, make_engine
from app.fakes.benefit import FakeBenefit
from app.modules.delegation import DelegationService
from app.modules.delegation.benefits import CBenefitPort
from app.modules.discovery.catalog_models import Menu
from app.modules.discovery.facade import DiscoveryFacade
from app.modules.orchestrator.models import Run
from app.modules.orchestrator.schemas import RunCreate
from app.modules.orchestrator.service import OrchestratorService
from app.modules.ordering.facade import OrderingFacade
from tests.discovery.conftest import seed
from tests.orchestrator.conftest import ADDRESS, DELEGATION, OK_MESSAGE, REPLY


def test_real_a_and_b_allow_next_candidate_after_sold_out(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")
    engine = make_engine(f"sqlite:///{tmp_path / 'real_ab.db'}")
    Base.metadata.drop_all(engine)
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        seed(db)

    benefit = FakeBenefit()

    class NoApprovalRead:
        def get_approval(self, approval_id):
            raise AssertionError("후보 선택 단계에서 승인을 읽어서는 안 됩니다")

    class NoPayment:
        def request_payment(self, body):
            raise AssertionError("결제 단계에 도달해서는 안 됩니다")

    with sessions() as o_db, sessions() as d_db:
        svc = OrchestratorService(
            o_db,
            discovery=DiscoveryFacade(sessions),
            ordering=OrderingFacade(sessions, benefit, NoApprovalRead()),
            benefit=benefit,
            payment=NoPayment(),
            delegation=DelegationService(
                d_db,
                CBenefitPort(calculate=benefit.calculate, list_methods=benefit.list_methods),
            ),
        )
        start = svc.start_run(
            "U001",
            RunCreate.model_validate(
                {"message": OK_MESSAGE, "delivery_address": ADDRESS, "delegation": DELEGATION}
            ),
            None,
        )
        candidates = start["prompt"]["candidates"]
        assert len(candidates) >= 2
        stored_candidates = o_db.get(Run, start["request_id"]).context["candidates"]
        first_id = stored_candidates[0]["items"][0]["menu_id"]
        with sessions() as db:
            db.get(Menu, first_id).is_sold_out = True
            db.commit()

        def pick(view, index):
            return svc.reply(
                "U001",
                view["request_id"],
                REPLY.validate_python(
                    {
                        "type": "SELECT_CANDIDATE",
                        "candidate_index": index,
                        "expected_state_version": view["state_version"],
                    }
                ),
                None,
            )

        after_sold_out = pick(start, 0)
        assert after_sold_out["state"] == "CANDIDATES_READY"
        second_index = after_sold_out["prompt"]["candidates"][0]["index"]
        selected = pick(after_sold_out, second_index)
        assert selected["state"] == "PAYMENT_METHOD_SELECTION"

    engine.dispose()
