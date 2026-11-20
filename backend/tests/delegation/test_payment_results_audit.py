"""D-08 결제 결과 기록 · D-09 감사 로그 조회 (MVP 2)."""

import pytest

from app.core.errors import AppError
from app.modules.delegation import audit
from app.modules.delegation.constants import EventType
from app.modules.delegation.schemas import AuthorizationCheckRequest, PaymentResultRequest
from tests.conftest import approve


def paid(svc):
    """승인 → D-06 통과까지 진행하고 execution_id를 돌려준다."""
    v, c = approve(svc)
    res = svc.check_authorization(
        AuthorizationCheckRequest(
            execution_id=c.execution_id,
            approval_id=v.approval_id,
            quote_id="QT001",
            proposal_hash=v.proposal_hash,
            charge_krw=33000,
            payment_method_id="CARD001",
        )
    )
    assert res.allowed
    return c.execution_id


def result(execution_id, status="APPROVED", payment_id="PAY001", charge=33000):
    return PaymentResultRequest(
        execution_id=execution_id, payment_id=payment_id, status=status, charge_krw=charge
    )


# ------------------------------------------------------------------ D-08
def test_records_result_and_replay_is_idempotent(svc, session):
    ex = paid(svc)
    out = svc.record_payment_result(result(ex))
    assert out.recorded and out.status == "APPROVED"
    before = svc.list_audit_logs(event_type=EventType.PAYMENT_RESULT).count
    again = svc.record_payment_result(result(ex))  # 같은 결과 다시 → 그대로 성공, 로그 추가 없음
    assert again.recorded
    assert svc.list_audit_logs(event_type=EventType.PAYMENT_RESULT).count == before == 1


def test_unknown_can_become_final(svc):
    ex = paid(svc)
    assert svc.record_payment_result(result(ex, "UNKNOWN")).status == "UNKNOWN"
    assert svc.record_payment_result(result(ex, "APPROVED")).status == "APPROVED"  # E-03·E-10 확정


@pytest.mark.parametrize("second", ["FAILED", "UNKNOWN", "PENDING"])
def test_final_result_cannot_change(svc, second):
    ex = paid(svc)
    svc.record_payment_result(result(ex, "APPROVED"))
    with pytest.raises(AppError) as e:
        svc.record_payment_result(result(ex, second))
    assert e.value.status == 409 and e.value.code == "RESULT_CONFLICT"
    assert e.value.details["recorded_status"] == "APPROVED"


def test_other_payment_id_conflicts(svc):
    ex = paid(svc)
    svc.record_payment_result(result(ex, "PENDING"))
    with pytest.raises(AppError) as e:
        svc.record_payment_result(result(ex, "APPROVED", payment_id="PAY999"))
    assert e.value.code == "RESULT_CONFLICT"


def test_unknown_execution_is_404(svc):
    with pytest.raises(AppError) as e:
        svc.record_payment_result(result("EXE-NOPE"))
    assert e.value.status == 404 and e.value.code == "EXECUTION_NOT_FOUND"


def test_amount_mismatch_is_recorded_and_flagged(svc, session):
    ex = paid(svc)
    svc.record_payment_result(result(ex, charge=34000))
    log = svc.list_audit_logs(event_type=EventType.PAYMENT_RESULT).results[0]
    assert log.result == "MISMATCH"
    assert log.reason_codes == ["PAYMENT_AMOUNT_MISMATCH"]
    assert log.details["approved_krw"] == 33000 and log.details["charged_krw"] == 34000
    assert audit.verify_chain(session) == (True, None)


# ------------------------------------------------------------------ D-09
def test_audit_logs_filter_and_page(svc):
    ex = paid(svc)
    svc.record_payment_result(result(ex))
    everything = svc.list_audit_logs(request_id="REQ001")
    assert everything.count == 5
    assert [r.event_type for r in everything.results][-1] == EventType.PAYMENT_RESULT

    page2 = svc.list_audit_logs(request_id="REQ001", page=2, page_size=2)
    assert page2.count == 5 and len(page2.results) == 2
    assert page2.results[0].log_id == everything.results[2].log_id

    only_exec = svc.list_audit_logs(execution_id=ex)
    assert {r.event_type for r in only_exec.results} == {
        EventType.APPROVAL_CONFIRMED,  # execution_id가 발급된 순간부터 같은 ID로 묶인다
        EventType.PAYMENT_CHECK,
        EventType.PAYMENT_RESULT,
    }
    assert svc.list_audit_logs(request_id="OTHER").count == 0


def test_audit_logs_never_show_masked_fields(svc):
    approve(svc)
    for row in svc.list_audit_logs(request_id="REQ001").results:
        assert "original_message" not in str(row.details) or "[REDACTED]" in str(row.details)
        assert (
            "delivery_address" not in row.details or row.details["delivery_address"] == "[REDACTED]"
        )
