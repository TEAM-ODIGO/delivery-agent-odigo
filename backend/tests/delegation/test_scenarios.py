"""시연·대시보드 시나리오를 service 단위로 끝까지 돌려 보는 테스트.

S1 정상 + S2~S7 대시보드 차단 시나리오 + S8~S10 OWASP 보완 시나리오, 그리고 경계 상황.
"""

import pytest
from sqlalchemy import func, select

from app.core.errors import AppError
from app.modules.delegation import audit
from app.modules.delegation.constants import ApprovalStatus, Reason
from app.modules.delegation.models import Approval, AuditLog, Execution
from app.modules.delegation.schemas import AuthorizationCheckRequest, ConfirmRequest, RevokeRequest
from tests.conftest import approve, make_policy, make_quote, make_validation


def pay_req(validated, confirmed, **over) -> AuthorizationCheckRequest:
    data = dict(
        execution_id=confirmed.execution_id,
        approval_id=validated.approval_id,
        quote_id="QT001",
        proposal_hash=validated.proposal_hash,
        charge_krw=33000,
        payment_method_id="CARD001",
    )
    data.update(over)
    return AuthorizationCheckRequest(**data)


def events(session) -> list[tuple[str, str]]:
    return [
        (r.event_type, r.result) for r in session.scalars(select(AuditLog).order_by(AuditLog.seq))
    ]


# ✅ S1 정상 ---------------------------------------------------------------------------
def test_s1_normal_flow(svc, session):
    validated, confirmed = approve(svc)
    result = svc.check_authorization(pay_req(validated, confirmed))

    assert result.allowed and result.reason_codes == []
    assert svc.get_approval(validated.approval_id).status == ApprovalStatus.CONSUMED
    assert session.scalar(select(func.count()).select_from(Execution)) == 1
    assert events(session) == [
        ("POLICY_REGISTERED", "OK"),
        ("ORDER_VALIDATED", "PASSED"),
        ("APPROVAL_CONFIRMED", "OK"),
        ("PAYMENT_CHECK", "ALLOWED"),
    ]
    assert audit.verify_chain(session) == (True, None)


# ⛔ S2 예산 초과 -----------------------------------------------------------------------
def test_s2_budget_exceeded(svc, session):
    policy = svc.register_policy(make_policy())
    q = make_quote(
        items=[{"menu_id": "M", "name": "치킨", "quantity": 2, "unit_price_krw": 22000}],
        menu_subtotal_krw=44000,
        charge_krw=45000,
    )
    result = svc.validate_order(make_validation(policy, q))

    assert not result.valid and result.reason_codes == ["BUDGET_EXCEEDED"]
    assert result.approval_id is None
    assert session.scalar(select(func.count()).select_from(Approval)) == 0  # 승인 화면으로 못 감
    assert events(session)[-1] == ("ORDER_VALIDATED", "FAILED")


# ⛔ S3 할인 부풀림 ---------------------------------------------------------------------
def test_s3_inflated_discount(svc):
    policy = svc.register_policy(make_policy())
    result = svc.validate_order(
        make_validation(policy, make_quote(instant_discount_krw=5000, charge_krw=30000))
    )
    assert result.reason_codes == ["AMOUNT_MISMATCH"]
    assert result.details["discount_expected"] == 2000


# ⛔ S4 승인 전 결제 ---------------------------------------------------------------------
def test_s4_payment_before_user_approval(svc):
    policy = svc.register_policy(make_policy())
    validated = svc.validate_order(make_validation(policy))
    req = AuthorizationCheckRequest(
        execution_id="EXE-guess",
        approval_id=validated.approval_id,
        quote_id="QT001",
        proposal_hash=validated.proposal_hash,
        charge_krw=33000,
        payment_method_id="CARD001",
    )
    assert svc.check_authorization(req).reason_codes == ["APPROVAL_NOT_CONFIRMED"]


# ⛔ S5 승인 후 금액 변조 ----------------------------------------------------------------
def test_s5_amount_tampered_after_approval(svc):
    validated, confirmed = approve(svc)
    blocked = svc.check_authorization(pay_req(validated, confirmed, charge_krw=30000))
    assert blocked.reason_codes == ["PAYMENT_AMOUNT_MISMATCH"]
    # 차단됐다고 승인이 소모되지는 않는다 → 올바른 요청은 여전히 통과
    assert svc.get_approval(validated.approval_id).status == ApprovalStatus.APPROVED
    assert svc.check_authorization(pay_req(validated, confirmed)).allowed


# ⛔ S6 조건 변경 후 옛 승인 -------------------------------------------------------------
def test_s6_old_approval_after_policy_change(svc):
    validated, confirmed = approve(svc)
    new_policy = svc.register_policy(make_policy(constraints={"budget_krw": 30000}))

    assert new_policy.policy_version == 2
    assert new_policy.revoked_approval_ids == [validated.approval_id]
    assert svc.check_authorization(pay_req(validated, confirmed)).reason_codes == [
        "APPROVAL_REVOKED"
    ]


# ⛔ S7 같은 승인 재사용 -----------------------------------------------------------------
def test_s7_reuse_same_approval(svc, session):
    validated, confirmed = approve(svc)
    assert svc.check_authorization(pay_req(validated, confirmed)).allowed

    # E가 네트워크 문제로 '같은 실행'을 다시 물으면 같은 답 (결제는 E가 execution_id로 한 번만)
    replay = svc.check_authorization(pay_req(validated, confirmed))
    assert replay.allowed
    # 다른 실행 ID로 같은 승인을 또 쓰려 하면 차단
    second = svc.check_authorization(pay_req(validated, confirmed, execution_id="EXE-second"))
    assert second.reason_codes == ["ALREADY_EXECUTED"]
    assert session.scalar(select(func.count()).select_from(Execution)) == 1


# ⛔ S8 승인 후 결제수단 바꿔치기 (OWASP: 파라미터가 바뀌면 새 승인) ----------------------
def test_s8_payment_method_swapped(svc):
    validated, confirmed = approve(svc)
    result = svc.check_authorization(pay_req(validated, confirmed, payment_method_id="LOCAL001"))
    assert result.reason_codes == ["PAYMENT_METHOD_MISMATCH"]


# ⛔ S9 결제를 맡기지 않은 사용자 (OWASP: 맡기지 않은 행동은 차단) ------------------------
def test_s9_payment_not_delegated(svc):
    validated, confirmed = approve(
        svc, policy_overrides={"delegation": {"allowed_actions": ["SEARCH", "BUILD_ORDER"]}}
    )
    assert svc.check_authorization(pay_req(validated, confirmed)).reason_codes == [
        "ACTION_NOT_ALLOWED"
    ]


# ⛔ S10 감사 로그를 못 남기면 결제도 없다 (OWASP: 로그 실패 시 fail-closed) ---------------
def test_s10_audit_failure_blocks_payment(svc, monkeypatch):
    validated, confirmed = approve(svc)

    def broken(*args, **kwargs):
        raise RuntimeError("로그 저장소 장애")

    monkeypatch.setattr(audit, "record", broken)
    with pytest.raises(RuntimeError):
        svc.check_authorization(pay_req(validated, confirmed))
    monkeypatch.undo()

    # 트랜잭션 전체가 취소되어 승인은 소모되지 않았다
    assert svc.get_approval(validated.approval_id).status == ApprovalStatus.APPROVED


# --- 경계 상황 ------------------------------------------------------------------------
def test_c_outage_blocks_validation(svc, benefits):
    policy = svc.register_policy(make_policy())
    benefits.available = False
    assert svc.validate_order(make_validation(policy)).reason_codes == ["QUOTE_DATA_UNVERIFIED"]


def test_approval_expires_after_ttl(svc, clock):
    policy = svc.register_policy(make_policy())
    validated = svc.validate_order(make_validation(policy))
    clock.advance(minutes=10)  # TTL = min(견적 만료 30분, 10분) = 10분

    with pytest.raises(AppError) as e:
        svc.confirm(
            validated.approval_id,
            ConfirmRequest(user_id="U001", proposal_hash=validated.proposal_hash),
        )
    assert e.value.code == "APPROVAL_EXPIRED"
    assert svc.get_approval(validated.approval_id).status == ApprovalStatus.EXPIRED


def test_approved_but_expired_before_payment(svc, clock):
    validated, confirmed = approve(svc)
    clock.advance(minutes=11)
    assert svc.check_authorization(pay_req(validated, confirmed)).reason_codes == [
        "APPROVAL_EXPIRED"
    ]


def test_new_quote_revokes_previous_approval(svc):
    policy = svc.register_policy(make_policy())
    first = svc.validate_order(make_validation(policy))
    second = svc.validate_order(
        make_validation(policy, make_quote(quote_id="QT002"), order_version=2)
    )
    assert second.valid and second.approval_id != first.approval_id
    old = svc.get_approval(first.approval_id)
    assert old.status == ApprovalStatus.REVOKED and old.revoke_reason == "QUOTE_CHANGED"


def test_same_quote_validation_is_idempotent(svc):
    policy = svc.register_policy(make_policy())
    a = svc.validate_order(make_validation(policy))
    b = svc.validate_order(make_validation(policy))
    assert a.approval_id == b.approval_id


def test_confirm_twice_returns_same_execution_id(svc):
    validated, confirmed = approve(svc)
    again = svc.confirm(
        validated.approval_id, ConfirmRequest(user_id="U001", proposal_hash=validated.proposal_hash)
    )
    assert again.execution_id == confirmed.execution_id


def test_confirm_rejections_are_logged(svc, session):
    policy = svc.register_policy(make_policy())
    validated = svc.validate_order(make_validation(policy))
    with pytest.raises(AppError) as e:
        svc.confirm(
            validated.approval_id,
            ConfirmRequest(user_id="U001", proposal_hash="sha256:" + "0" * 64),
        )
    assert e.value.code == "PROPOSAL_HASH_MISMATCH"
    assert events(session)[-1] == ("APPROVAL_CONFIRMED", "REJECTED")

    with pytest.raises(AppError) as e:
        svc.confirm(
            validated.approval_id,
            ConfirmRequest(user_id="U999", proposal_hash=validated.proposal_hash),
        )
    assert e.value.code == "FORBIDDEN_USER"


def test_user_cancel_then_payment_blocked(svc):
    validated, confirmed = approve(svc)
    out = svc.revoke(validated.approval_id, RevokeRequest(reason_code="USER_CANCELLED"))
    assert out.status == ApprovalStatus.REVOKED
    assert (
        svc.revoke(validated.approval_id, RevokeRequest(reason_code="USER_CANCELLED")).status
        == "REVOKED"
    )
    assert svc.check_authorization(pay_req(validated, confirmed)).reason_codes == [
        "APPROVAL_REVOKED"
    ]


def test_cannot_revoke_after_payment(svc):
    validated, confirmed = approve(svc)
    svc.check_authorization(pay_req(validated, confirmed))
    with pytest.raises(AppError) as e:
        svc.revoke(validated.approval_id, RevokeRequest(reason_code="USER_CANCELLED"))
    assert e.value.code == "ALREADY_EXECUTED"


def test_other_user_cannot_take_over_request(svc):
    svc.register_policy(make_policy())
    with pytest.raises(AppError) as e:
        svc.register_policy(make_policy(user_id="U999"))
    assert e.value.code == "FORBIDDEN_USER"


def test_every_decision_is_chained(svc, session):
    validated, confirmed = approve(svc)
    svc.check_authorization(pay_req(validated, confirmed, charge_krw=1))
    svc.check_authorization(pay_req(validated, confirmed))
    rows = session.scalars(select(AuditLog)).all()
    assert all(r.request_id == "REQ001" for r in rows)
    assert audit.verify_chain(session) == (True, None)
    assert Reason.PAYMENT_AMOUNT_MISMATCH in rows[-2].reason_codes


def test_ineligible_method_must_have_zero_discount(svc):
    """C-05가 '혜택 대상 아님'(eligible=false → 할인 0)인데 B가 할인을 붙였다면 차단"""
    policy = svc.register_policy(make_policy())
    inflated = make_quote(payment_method_id="LOCAL001")  # LOCAL001은 즉시할인 규칙 없음
    assert svc.validate_order(make_validation(policy, inflated)).reason_codes == ["AMOUNT_MISMATCH"]
    honest = make_quote(payment_method_id="LOCAL001", instant_discount_krw=0, charge_krw=35000)
    assert svc.validate_order(make_validation(policy, honest, order_version=2)).valid
