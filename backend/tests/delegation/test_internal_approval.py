"""B-07에 제공하는 D 내부 승인 조회 계약."""

from tests.conftest import approve


def test_internal_approval_includes_hash_without_changing_d04(svc):
    validated, confirmed = approve(svc)
    public = svc.get_approval(confirmed.approval_id).model_dump(mode="json")
    internal = svc.get_approval_for_execution(confirmed.approval_id)

    assert "proposal_hash" not in public
    assert internal == {**public, "proposal_hash": validated.proposal_hash}
