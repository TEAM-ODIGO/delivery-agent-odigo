"""해시와 감사 로그 테스트."""

from sqlalchemy import text

from app.modules.delegation import audit
from app.modules.delegation.constants import Actor, EventType
from app.modules.delegation.hashing import canonical_json, proposal_hash
from app.modules.delegation.schemas import OrderValidationRequest
from tests.conftest import T0, make_quote


def _req(quote) -> OrderValidationRequest:
    return OrderValidationRequest.model_validate(
        {
            "request_id": "REQ001",
            "user_id": "U001",
            "policy_id": "POL1",
            "policy_version": 1,
            "approval_target_id": "target_001",
            "order_version": 1,
            "quote": quote,
        }
    )


# --- 해시 ---------------------------------------------------------------------------
def test_canonical_json_ignores_key_order():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_same_proposal_same_hash_and_one_won_changes_it():
    h1 = proposal_hash(_req(make_quote()))
    h2 = proposal_hash(_req(dict(reversed(list(make_quote().items())))))  # 키 순서만 다름
    h3 = proposal_hash(_req(make_quote(delivery_fee_krw=3001, charge_krw=33001)))
    assert h1 == h2
    assert h1 != h3
    assert h1.startswith("sha256:") and len(h1) == 7 + 64


def test_unknown_quote_fields_do_not_change_hash():
    # B가 D가 모르는 필드를 덧붙여도 D가 검증한 내용이 같으면 같은 해시
    assert proposal_hash(_req(make_quote())) == proposal_hash(_req(make_quote(promo_banner="x")))


# --- 로그 정리 -----------------------------------------------------------------------
def test_sanitize_redacts_and_strips_newlines():
    out = audit.sanitize(
        {
            "access_token": "secret",
            "address": "서울 중구",
            "note": "줄1\r\n[가짜 로그] ALLOWED",
            "long": "가" * 1000,
            "nested": {"card_number": "1234"},
        }
    )
    assert out["access_token"] == "[REDACTED]"
    assert out["address"] == "[REDACTED]"
    assert out["nested"]["card_number"] == "[REDACTED]"
    assert "\n" not in out["note"] and "\r" not in out["note"]
    assert len(out["long"]) <= audit.MAX_TEXT + 1


# --- 해시 사슬 -----------------------------------------------------------------------
def _write_logs(session, n=3):
    for i in range(n):
        audit.record(
            session,
            now=T0,
            event_type=EventType.POLICY_REGISTERED,
            actor=Actor.ORCHESTRATOR,
            result="OK",
            request_id=f"REQ{i}",
            details={"i": i},
        )
    session.commit()


def test_chain_is_valid_after_writes(session):
    _write_logs(session)
    assert audit.verify_chain(session) == (True, None)


def test_tampering_is_detected(session):
    _write_logs(session)
    # 누군가 DB에서 두 번째 로그의 결과를 몰래 바꿨다
    session.execute(text("UPDATE delegation_audit_log SET result='BLOCKED' WHERE seq=2"))
    session.commit()
    session.expire_all()
    assert audit.verify_chain(session) == (False, 2)


def test_deleting_a_row_is_detected(session):
    _write_logs(session)
    session.execute(text("DELETE FROM delegation_audit_log WHERE seq=2"))
    session.commit()
    session.expire_all()
    ok, broken_at = audit.verify_chain(session)
    assert not ok and broken_at == 3
