"""O 흐름 시나리오: 가짜 A·B·C·E + 진짜 D.

O1  정상: 후보 → 결제수단 → 승인 → 결제 완료, B-08 전달, D 승인 '사용됨'
O2  재질문: 조건이 확정되기 전에는 D-01을 부르지 않는다
O3  품절: 남은 후보로 다시 선택 / 모두 품절이면 FAILED(NO_CANDIDATE)
O4  D 차단: 예산 초과 견적은 승인 화면까지 가지 않는다
O5  혜택 불일치(B-05 409) → BLOCKED
O6  취소: 승인 대기 중 취소 → D-05 철회 / 결제 후 취소 → 409
O7  결제 거절 → FAILED, B-08 장애는 '전달 대기'로 남았다가 reconcile로 재전송
"""

from app.modules.delegation.constants import ApprovalStatus, EventType
from tests.orchestrator.conftest import OK_MESSAGE, REPLY


def _types(events, event_type):
    return [e for e in events if e["event_type"] == event_type]


def _apis(events):
    return [e["detail"].get("api") for e in events if e["event_type"] == "SERVICE_CALL"]


def test_o1_happy_path_until_payment(env):
    v = env.start()
    assert v["state"] == "CANDIDATES_READY"
    assert v["step"] == {"current": 3, "total": 8, "label": "후보 선택"}
    assert [c["index"] for c in v["prompt"]["candidates"]] == [0, 1, 2]

    v = env.reply(v, type="SELECT_CANDIDATE", candidate_index=0)
    assert v["state"] == "PAYMENT_METHOD_SELECTION"
    # 위임하지 않은 결제수단(CARD002)은 C-01에 넘기지 않으므로 나오지 않는다
    assert [o["payment_method_id"] for o in v["prompt"]["options"]] == ["CARD001", "LOCAL001"]

    v = env.reply(v, type="SELECT_PAYMENT_METHOD", payment_method_id="CARD001")
    assert v["state"] == "AWAITING_APPROVAL"
    assert (
        v["prompt"]["summary"]
        == "예시치킨 역삼점 · 후라이드치킨 1 · 17,000원 (CARD001 즉시할인 2,000원)"
    )
    assert "예산 20,000원 이내" in v["prompt"]["checks"]

    v = env.reply(v, type="APPROVE")
    assert v["state"] == "COMPLETED"
    assert v["result"]["payment_status"] == "APPROVED"
    assert v["result"]["charge_krw"] == 17000

    events = env.events(v)
    # 호출 순서가 설계(03 문서 6절) 그대로
    assert _apis(events) == [
        "A-01",
        "D-01",
        "A-05",
        "B-01",
        "A-06",
        "C-01",
        "B-05",
        "B-06",
        "D-02",
        "D-03",
        "B-07",
        "E-01",
        "B-08",
    ]
    pay = _types(events, "PAYMENT_RESULT")
    assert len(pay) == 1 and pay[0]["sync_b08"] == "DONE"
    assert pay[0]["detail"]["event_id"].endswith(":APPROVED")

    # D: 승인은 E의 D-06에서 '사용됨'이 됐고, 감사 로그에 결제 직전 검증이 남았다
    ctx_run = env.svc().session.get(
        __import__("app.modules.orchestrator.models", fromlist=["Run"]).Run, v["request_id"]
    )
    approval = env.d().get_approval(ctx_run.context["approval_id"])
    assert approval.status == ApprovalStatus.CONSUMED
    logs = env.d().list_audit_logs(request_id=v["request_id"]).results
    assert EventType.PAYMENT_CHECK in [log.event_type for log in logs]
    # B는 같은 결과를 한 번만 받았다
    assert len(env.world.ordering.payment_events) == 1


def test_sold_out_candidate_does_not_lock_real_a_style_selection(env):
    """A가 첫 선택 뒤 재선택을 거부해도 B 품절 뒤 남은 후보를 선택할 수 있다."""

    class SelectOnce:
        def __init__(self, inner):
            self.inner = inner
            self.selected = []

        def select(self, request_id, body):
            if self.selected:
                raise AssertionError("A에 두 번째 후보 선택을 요청했습니다")
            self.selected.append(body["candidate_id"])
            return self.inner.select(request_id, body)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    guarded_a = SelectOnce(env.world.discovery)
    first = env.start(OK_MESSAGE + " #품절")
    svc = env.svc(discovery=guarded_a)

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

    after_sold_out = pick(first, 0)
    assert after_sold_out["state"] == "CANDIDATES_READY"
    assert guarded_a.selected == []
    chosen = pick(after_sold_out, 1)
    assert chosen["state"] == "PAYMENT_METHOD_SELECTION"
    assert len(guarded_a.selected) == 1


def test_o2_clarification_registers_policy_only_after_confirmed(env):
    v = env.start("치킨 시켜줘")
    assert v["state"] == "NEED_CLARIFICATION"
    slots = [q["slot"] for q in v["prompt"]["questions"]]
    assert slots == ["people", "budget_krw"]
    assert v["prompt"]["questions"][0]["input_type"] == "SELECT"
    assert "D-01" not in _apis(env.events(v))  # 미확정 조건은 D 기준이 되지 않는다

    v = env.reply(v, type="CLARIFICATION", answers=[{"slot": "people", "value": 2}])
    assert v["state"] == "NEED_CLARIFICATION"  # 예산이 아직 없음
    assert [q["slot"] for q in v["prompt"]["questions"]] == ["budget_krw"]

    v = env.reply(v, type="CLARIFICATION", answers=[{"slot": "budget_krw", "value": "20000"}])
    assert v["state"] == "CANDIDATES_READY"
    assert _apis(env.events(v)).count("D-01") == 1


def test_o2b_invalid_clarification_value_keeps_state(env):
    v = env.start("치킨 시켜줘")
    before = v["state_version"]
    try:
        env.reply(v, type="CLARIFICATION", answers=[{"slot": "budget_krw", "value": "-5"}])
        raise AssertionError("should fail")
    except Exception as exc:
        assert getattr(exc, "status", None) == 400
    again = env.get(v)
    assert again["state"] == "NEED_CLARIFICATION" and again["state_version"] == before


def test_o3_sold_out_offers_remaining_candidates(env):
    v = env.start(OK_MESSAGE + " #품절")
    v = env.reply(v, type="SELECT_CANDIDATE", candidate_index=0)
    assert v["state"] == "CANDIDATES_READY"
    assert v["notice"]["code"] == "CANDIDATE_UNAVAILABLE"
    assert v["notice"]["issues"][0]["code"] == "MENU_SOLD_OUT"
    assert [c["index"] for c in v["prompt"]["candidates"]] == [1, 2]  # 원래 번호 유지

    # 이미 실패한 후보는 다시 고를 수 없다
    try:
        env.reply(v, type="SELECT_CANDIDATE", candidate_index=0)
        raise AssertionError("should fail")
    except Exception as exc:
        assert getattr(exc, "status", None) == 400

    v = env.reply(v, type="SELECT_CANDIDATE", candidate_index=1)
    assert v["state"] == "PAYMENT_METHOD_SELECTION"


def test_o3b_all_sold_out_fails(env):
    v = env.start(OK_MESSAGE + " #모두품절")
    for _ in range(3):
        v = env.reply(
            v, type="SELECT_CANDIDATE", candidate_index=v["prompt"]["candidates"][0]["index"]
        )
    assert v["state"] == "FAILED"
    assert v["error"]["code"] == "NO_CANDIDATE"


def test_o4_d_blocks_budget_exceeded(env):
    # 예산 15,000원: 모든 후보가 예산 초과지만 A는 가장 가까운 후보를 돌려준다
    v = env.start(
        "둘이서 15,000원 이하로 안 매운 치킨",
        delegation={"allowed_payment_method_ids": ["CARD001"]},
    )
    v = env.reply(v, type="SELECT_CANDIDATE", candidate_index=0)
    v = env.reply(v, type="SELECT_PAYMENT_METHOD", payment_method_id="CARD001")
    assert v["state"] == "BLOCKED"
    assert v["blocked"]["reason_codes"] == ["BUDGET_EXCEEDED"]
    assert v["blocked"]["details"]["limit_krw"] == 15000
    assert "D-03" not in _apis(env.events(v))  # 승인 화면까지 가지 않음


def test_o5_benefit_mismatch_blocks(env):
    v = env.flow("PAYMENT_METHOD_SELECTION", OK_MESSAGE + " #혜택불일치")
    v = env.reply(v, type="SELECT_PAYMENT_METHOD", payment_method_id="CARD001")
    assert v["state"] == "BLOCKED"
    assert v["blocked"]["reason_codes"] == ["STALE_CALCULATION"]


def test_o6_cancel_revokes_approval(env):
    v = env.flow("AWAITING_APPROVAL")
    from app.modules.orchestrator.models import Run

    approval_id = env.svc().session.get(Run, v["request_id"]).context["approval_id"]
    v = env.reply(v, type="CANCEL")
    assert v["state"] == "CANCELLED"
    assert env.d().get_approval(approval_id).status == ApprovalStatus.REVOKED


def test_o6b_cancel_after_payment_is_conflict(env):
    v = env.flow("COMPLETED")
    try:
        env.reply(v, type="CANCEL")
        raise AssertionError("should fail")
    except Exception as exc:
        assert exc.status == 409 and exc.code == "INVALID_STATE_FOR_REPLY"
        assert exc.details["state"] == "COMPLETED"


def test_o7_declined_payment_and_b08_redelivery(env):
    v = env.flow("COMPLETED", OK_MESSAGE + " #결제거절 #B08장애")
    assert v["state"] == "FAILED"
    assert v["error"]["code"] == "PAYMENT_FAILED"
    pay = _types(env.events(v), "PAYMENT_RESULT")
    assert pay[0]["sync_b08"] == "PENDING"  # B-08이 실패해 전달 대기

    summary = env.svc().reconcile_pending()  # 서버 시작 시·1분마다 도는 작업
    assert summary["checked"] == 1
    pay = _types(env.events(v), "PAYMENT_RESULT")
    assert pay[0]["sync_b08"] == "DONE"
    assert len(env.world.ordering.payment_events) == 1
