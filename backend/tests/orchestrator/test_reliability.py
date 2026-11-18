"""O 안정성: 중복·동시 제출, 일시 장애, 결제 결과 모름, 결제 직전 값 비교.

R1  같은 Idempotency-Key = 처음 응답 그대로 / 다른 내용 = 409
R2  오래된 화면(expected_state_version 불일치)·맞지 않는 응답 종류 = 409
R3  동시에 같은 버전으로 저장하면 나중 것은 409 (row_version)
R4  하위 모듈 일시 장애: 2회 재시도 → 503, 화면 상태·버전은 그대로 → 같은 버전으로 다시 누르면 진행
R5  O-01에서 A 장애: 503 DEPENDENCY_UNAVAILABLE, 호출 3번 기록
R6  결제 미확정(UNKNOWN) → PAYMENT_UNKNOWN → O-03 조회 때 E-03으로 확정
R7  E-01 응답 유실 → 다시 결제하지 않고 E-03으로 확인
R8  PAYING 저장 직후 서버가 멈춤 → reconcile이 E-03(404) 확인 후 같은 execution_id로 재요청
R9  B-07이 준 결제 값이 승인 내용과 다르면 E를 부르지 않고 차단
R10 남의 주문은 404
"""

import pytest

from app.core.errors import AppError
from app.modules.orchestrator.models import Run
from tests.orchestrator.conftest import OK_MESSAGE


def _err(fn):
    with pytest.raises(AppError) as info:
        fn()
    return info.value


def test_r1_idempotency_key(env):
    first = env.start(key="client-1")
    again = env.start(key="client-1")
    assert again == first
    with env.sf() as s:
        assert s.query(Run).count() == 1
    exc = _err(lambda: env.start("피자 시켜줘", key="client-1"))
    assert exc.status == 409 and exc.code == "IDEMPOTENCY_CONFLICT"

    v = env.reply(first, key="r-1", type="SELECT_CANDIDATE", candidate_index=0)
    same = env.reply(first, key="r-1", type="SELECT_CANDIDATE", candidate_index=0)
    assert same == v  # 두 번 눌러도 주문안 1개


def test_r2_stale_version_and_wrong_type(env):
    v = env.start()
    exc = _err(
        lambda: env.reply(
            v, version=v["state_version"] - 1, type="SELECT_CANDIDATE", candidate_index=0
        )
    )
    assert exc.status == 409 and exc.code == "INVALID_STATE_FOR_REPLY"
    assert exc.details == {"state": "CANDIDATES_READY", "state_version": v["state_version"]}

    exc = _err(lambda: env.reply(v, type="APPROVE"))
    assert exc.status == 409


def test_r3_concurrent_save_loses(env):
    v = env.start()
    slow = env.svc()
    run = slow.session.get(Run, v["request_id"])  # 먼저 읽어 둠
    env.reply(v, type="SELECT_CANDIDATE", candidate_index=0)  # 다른 요청이 먼저 저장
    run.state = "CANCELLED"
    exc = _err(slow._commit)
    assert exc.status == 409


class FlakyRecommendation:
    """C-01이 fail_times번 503을 낸 뒤 정상 응답."""

    def __init__(self, real, fail_times):
        self.real, self.left, self.calls = real, fail_times, 0

    def recommend(self, body, *, idempotency_key=None):
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise AppError(503, "BENEFIT_UNAVAILABLE", "C 응답 없음")
        return self.real.recommend(body, idempotency_key=idempotency_key)


def test_r4_transient_failure_keeps_screen_state(env):
    v = env.start()
    flaky = FlakyRecommendation(env.world.benefit, fail_times=3)
    exc = _err(
        lambda: env.svc(benefit=flaky).reply(
            "U001",
            v["request_id"],
            __import__("tests.orchestrator.conftest", fromlist=["REPLY"]).REPLY.validate_python(
                {
                    "type": "SELECT_CANDIDATE",
                    "candidate_index": 0,
                    "expected_state_version": v["state_version"],
                }
            ),
            None,
        )
    )
    assert exc.status == 503 and exc.code == "DEPENDENCY_UNAVAILABLE"
    assert flaky.calls == 3  # 1회 + 재시도 2회
    now = env.get(v)
    assert now["state"] == "CANDIDATES_READY" and now["state_version"] == v["state_version"]

    # 같은 버전으로 다시 누르면 (B-01은 같은 멱등 키라 주문안이 새로 생기지 않음) 진행된다
    env_svc = env.svc(benefit=flaky)
    from tests.orchestrator.conftest import REPLY

    ok = env_svc.reply(
        "U001",
        v["request_id"],
        REPLY.validate_python(
            {
                "type": "SELECT_CANDIDATE",
                "candidate_index": 0,
                "expected_state_version": v["state_version"],
            }
        ),
        None,
    )
    assert ok["state"] == "PAYMENT_METHOD_SELECTION"
    assert len(env.world.ordering.plans) == 1


def test_c01_response_loss_reuses_real_c_recommendation(env):
    """C가 추천을 저장한 뒤 응답만 잃어도 O의 재시도는 기존 추천을 받는다."""
    from app.modules.benefit import facade, service
    from app.modules.benefit.catalog_source import use_shared_restaurants

    service.reset_state()
    use_shared_restaurants()

    class LostOnce:
        def __init__(self):
            self.first_id = None
            self.keys = []

        def recommend(self, body, *, idempotency_key=None):
            self.keys.append(idempotency_key)
            result = facade.recommend(body, idempotency_key=idempotency_key)
            if self.first_id is None:
                self.first_id = result["recommendation_id"]
                raise AppError(503, "RESPONSE_LOST", "C 응답 유실")
            return result

        def list_methods(self, user_id):
            return env.world.benefit.list_methods(user_id)

    c = LostOnce()
    try:
        v = env.start()
        from tests.orchestrator.conftest import REPLY

        selected = env.svc(benefit=c).reply(
            "U001",
            v["request_id"],
            REPLY.validate_python(
                {
                    "type": "SELECT_CANDIDATE",
                    "candidate_index": 0,
                    "expected_state_version": v["state_version"],
                }
            ),
            None,
        )
        assert selected["state"] == "PAYMENT_METHOD_SELECTION"
        assert selected["prompt"]["options"]
        assert selected["request_id"] == v["request_id"]
        assert len(c.keys) == 2 and c.keys[0] == c.keys[1] and c.keys[0]
        assert service.view_recommendation(c.first_id)["status"] == "ACTIVE"
    finally:
        service.reset_state()


def test_r5_discovery_down_on_start(env):
    exc = _err(lambda: env.start("치킨 #A장애"))
    assert exc.status == 503 and exc.code == "DEPENDENCY_UNAVAILABLE"
    rid = exc.details["request_id"]
    events = env.svc().list_events(rid)["events"]
    assert [e["detail"].get("attempt") for e in events if e["event_type"] == "SERVICE_ERROR"] == [
        1,
        2,
        3,
    ]
    with env.sf() as s:
        assert s.get(Run, rid).state == "FAILED"


def test_r6_unknown_payment_resolved_on_status_check(env):
    v = env.flow("COMPLETED", OK_MESSAGE + " #결제미확정")
    assert v["state"] == "PAYMENT_UNKNOWN"
    assert v["result"]["payment_status"] == "UNKNOWN"
    v = env.get(v)  # 화면 폴링 → O가 E-03 확인
    assert v["state"] == "COMPLETED"
    pays = [
        e
        for e in env.svc().list_events(v["request_id"])["events"]
        if e["event_type"] == "PAYMENT_RESULT"
    ]
    assert [p["detail"]["status"] for p in pays] == ["UNKNOWN", "APPROVED"]
    assert all(p["sync_b08"] == "DONE" for p in pays)
    assert len(env.world.payment.ledger) == 1  # 결제는 한 번


def test_r7_lost_response_never_pays_twice(env):
    v = env.flow("COMPLETED", OK_MESSAGE + " #응답유실")
    assert v["state"] == "PAYMENT_UNKNOWN"
    assert len(env.world.payment.ledger) == 1  # E는 이미 결제함
    v = env.get(v)
    assert v["state"] == "COMPLETED"
    assert len(env.world.payment.ledger) == 1  # 다시 결제하지 않음


class CrashBeforeE:
    """E-01을 보내기 직전에 서버가 멈춘 상황 (요청이 E에 닿지 않음)."""

    def __init__(self, real):
        self.real = real

    def request_payment(self, body):
        raise SystemExit("서버 중단")

    def get_by_execution(self, execution_id):
        return self.real.get_by_execution(execution_id)


def test_r8_crash_after_paying_saved_is_recovered(env):
    v = env.flow("AWAITING_APPROVAL")
    crash = CrashBeforeE(env.world.payment)
    from tests.orchestrator.conftest import REPLY

    with pytest.raises(SystemExit):
        env.svc(payment=crash).reply(
            "U001",
            v["request_id"],
            REPLY.validate_python(
                {"type": "APPROVE", "expected_state_version": v["state_version"]}
            ),
            None,
        )
    with env.sf() as s:
        assert s.get(Run, v["request_id"]).state == "PAYING"  # 결제 전에 저장돼 있음

    summary = env.svc().reconcile_pending()  # 서버 재시작
    assert summary["checked"] == 1
    now = env.get(v)
    assert now["state"] == "COMPLETED"
    assert len(env.world.payment.ledger) == 1


def test_r9_execution_input_mismatch_blocks_before_payment(env):
    v = env.flow("AWAITING_APPROVAL")
    real = env.ordering.check_execution

    def tampered(order_plan_id, body):
        res = real(order_plan_id, body)
        res["execution_input"]["charge_krw"] += 1000
        return res

    env.ordering.check_execution = tampered
    v = env.reply(v, type="APPROVE")
    assert v["state"] == "BLOCKED"
    assert v["blocked"]["reason_codes"] == ["EXECUTION_INPUT_MISMATCH"]
    assert env.world.payment.ledger == {}  # E는 불리지 않았다


def test_r10_other_user_cannot_see_run(env):
    v = env.start()
    exc = _err(lambda: env.get(v, user="U002"))
    assert exc.status == 404 and exc.code == "RUN_NOT_FOUND"


class LedgerLost:
    """E-01은 처리됐는데(D-06 통과) E 원장 조회가 404 — 결제 여부를 알 수 없는 상황."""

    def __init__(self, real):
        self.real = real

    def request_payment(self, body):
        self.real.request_payment(body)
        from app.modules.orchestrator.ports import PaymentResponseLost

        raise PaymentResponseLost("응답 유실")

    def get_by_execution(self, execution_id):
        raise AppError(404, "PAYMENT_NOT_FOUND", "없음")


def test_r11_consumed_approval_without_ledger_is_not_called_failed(env):
    v = env.flow("AWAITING_APPROVAL")
    env.payment = LedgerLost(env.world.payment)
    v = env.reply(v, type="APPROVE")
    assert v["state"] == "PAYMENT_UNKNOWN"
    v = env.get(v)
    # D 승인은 이미 '사용됨' → 결제됐을 수 있으므로 실패로 단정하지 않고, 다시 결제하지도 않는다
    assert v["state"] == "PAYMENT_UNKNOWN"
    errors = [
        e["detail"]
        for e in env.svc().list_events(v["request_id"])["events"]
        if e["detail"].get("code") == "PAYMENT_RECORD_MISSING"
    ]
    assert errors and errors[0]["needs_manual_check"] is True
    assert len(env.world.payment.ledger) == 1


def test_r12_expired_approval_never_reached_e_is_failed(env, clock):
    v = env.flow("AWAITING_APPROVAL")
    crash = CrashBeforeE(env.world.payment)
    from tests.orchestrator.conftest import REPLY

    with pytest.raises(SystemExit):
        env.svc(payment=crash).reply(
            "U001",
            v["request_id"],
            REPLY.validate_python(
                {"type": "APPROVE", "expected_state_version": v["state_version"]}
            ),
            None,
        )
    clock.advance(minutes=30)  # 승인 만료 뒤에 서버가 다시 켜짐
    env.svc().reconcile_pending()
    now = env.get(v)
    assert now["state"] == "FAILED"
    assert now["error"]["code"] == "PAYMENT_NOT_STARTED"
    assert env.world.payment.ledger == {}
