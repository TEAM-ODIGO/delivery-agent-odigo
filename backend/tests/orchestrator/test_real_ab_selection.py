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


def test_real_b_requote_from_yogiyo_cart(tmp_path, monkeypatch):
    """진짜 A·B·D로: 요기요 장바구니 가격으로 재견적(B-10) → 새 승인 대상·승인 (이슈 #86)."""
    import uuid

    from app.modules.cart.models import CartJob

    monkeypatch.setenv("LLM_ENABLED", "false")
    engine = make_engine(f"sqlite:///{tmp_path / 'real_rq.db'}")
    Base.metadata.drop_all(engine)
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        seed(db)
    benefit = FakeBenefit()

    class NoApprovalRead:
        def get_approval(self, approval_id):
            raise AssertionError("승인 전 단계에서 B-07을 부르면 안 됩니다")

    with sessions() as o_db, sessions() as d_db:
        svc = OrchestratorService(
            o_db,
            discovery=DiscoveryFacade(sessions),
            ordering=OrderingFacade(sessions, benefit, NoApprovalRead()),
            benefit=benefit,
            payment=None,
            delegation=DelegationService(
                d_db, CBenefitPort(calculate=benefit.calculate, list_methods=benefit.list_methods)
            ),
        )

        def reply(view, **body):
            return svc.reply(
                "U001",
                view["request_id"],
                REPLY.validate_python({**body, "expected_state_version": view["state_version"]}),
                None,
            )

        v = svc.start_run(
            "U001",
            RunCreate.model_validate(
                {"message": OK_MESSAGE, "delivery_address": ADDRESS, "delegation": DELEGATION}
            ),
            None,
        )
        v = reply(v, type="SELECT_CANDIDATE", candidate_index=0)
        method = v["prompt"]["options"][0]["payment_method_id"]
        v = reply(v, type="SELECT_PAYMENT_METHOD", payment_method_id=method)
        assert v["state"] == "AWAITING_APPROVAL"
        ctx = o_db.get(Run, v["request_id"]).context
        q, old_approval = ctx["target_quote"], ctx["approval_id"]
        with sessions() as db:
            db.add(
                CartJob(
                    job_id="CART-" + uuid.uuid4().hex[:8],
                    request_id=v["request_id"],
                    user_id="U001",
                    status="CART_MISMATCH",
                    restaurant_id=q["restaurant_id"],
                    expected={"items": q["items"]},
                    cart_items=[
                        {"menu_id": i["menu_id"], "quantity": i["quantity"], "unit_price_krw": i["unit_price_krw"] + 500}
                        for i in q["items"]
                    ],
                    menu_subtotal_krw=None,
                    delivery_fee_krw=q["delivery_fee_krw"],
                    mismatches=["메뉴 합계가 견적과 다름"],
                    mode="fake",
                    created_at=ctx and o_db.get(Run, v["request_id"]).updated_at,
                )
            )
            db.commit()
        v2 = reply(v, type="REQUOTE_FROM_CART")
        assert v2["state"] == "AWAITING_APPROVAL", v2
        ctx2 = o_db.get(Run, v["request_id"]).context
        added = 500 * sum(i["quantity"] for i in q["items"])
        assert ctx2["target_quote"]["menu_subtotal_krw"] == q["menu_subtotal_krw"] + added
        assert ctx2["target_quote"]["catalog_version"].endswith(":YOGIYO_CART")
        assert ctx2["approval_id"] != old_approval and ctx2["order_version"] == ctx["order_version"] + 1
        assert v2["prompt"]["requote"]["previous"]["charge_krw"] == q["charge_krw"]

    engine.dispose()
