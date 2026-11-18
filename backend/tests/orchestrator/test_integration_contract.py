"""A 후보와 B 견적의 판본을 O가 각각 올바른 다음 모듈에 전달하는지 확인한다."""

from app.modules.orchestrator.models import Run
from app.modules.orchestrator.schemas import RunCreate
from tests.orchestrator.conftest import ADDRESS, DELEGATION, OK_MESSAGE, REPLY


def test_catalog_version_flows_from_a_to_b_and_b_to_c(env):
    class DiscoveryWithVersion:
        def __init__(self, inner):
            self.inner = inner

        def search(self, request_id, body):
            result = self.inner.search(request_id, body)
            for candidate in result["candidates"]:
                candidate["catalog_version"] = "CAT-A"
            return result

        def __getattr__(self, name):
            return getattr(self.inner, name)

    class OrderingWithNewerVersion:
        def __init__(self, inner):
            self.inner = inner
            self.requested_version = None

        def create_order_plan(self, body, *, idempotency_key):
            self.requested_version = body["catalog_version"]
            result = self.inner.create_order_plan(body, idempotency_key=idempotency_key)
            result["order_plans"][0]["quote"]["catalog_version"] = "CAT-B"
            return result

        def __getattr__(self, name):
            return getattr(self.inner, name)

    class RecommendationSpy:
        def __init__(self, inner):
            self.inner = inner
            self.catalog_version = None

        def recommend(self, body, *, idempotency_key=None):
            self.catalog_version = body["catalog_version"]
            return self.inner.recommend(body, idempotency_key=idempotency_key)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    discovery = DiscoveryWithVersion(env.world.discovery)
    ordering = OrderingWithNewerVersion(env.world.ordering)
    benefit = RecommendationSpy(env.world.benefit)
    svc = env.svc(discovery=discovery, ordering=ordering, benefit=benefit)
    start = svc.start_run(
        "U001",
        RunCreate.model_validate(
            {"message": OK_MESSAGE, "delivery_address": ADDRESS, "delegation": DELEGATION}
        ),
        None,
    )
    chosen = svc.reply(
        "U001",
        start["request_id"],
        REPLY.validate_python(
            {
                "type": "SELECT_CANDIDATE",
                "candidate_index": 0,
                "expected_state_version": start["state_version"],
            }
        ),
        None,
    )

    assert chosen["state"] == "PAYMENT_METHOD_SELECTION"
    assert ordering.requested_version == "CAT-A"
    assert benefit.catalog_version == "CAT-B"
    assert svc.session.get(Run, start["request_id"]).context["quote"]["catalog_version"] == "CAT-B"
