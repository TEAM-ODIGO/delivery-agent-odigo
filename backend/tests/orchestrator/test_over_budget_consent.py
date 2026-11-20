"""예산 초과 확인(로컬 확장): 예산을 넘는 가게임을 알고 계속하겠다고 하면 그 주문에 한해 진행한다.

- 동의가 없으면 기존처럼 D-02가 BUDGET_EXCEEDED로 막는다.
- 동의는 A가 예산 초과로 표시한 후보에만, 최종 금액이 후보 화면에서 본 금액 이내일 때만 효력이 있다.
"""

from sqlalchemy.orm.attributes import flag_modified

from app.modules.orchestrator.models import Run
from tests.orchestrator.conftest import OK_MESSAGE

# 모든 후보가 예산 초과지만 A는 가장 가까운 후보를 준다
OVER_MESSAGE = "둘이서 15,000원 이하로 안 매운 치킨"
CARD_ONLY = {"allowed_payment_method_ids": ["CARD001"]}


def _over_candidate(view):
    c = view["prompt"]["candidates"][0]
    assert any(v["code"] == "BUDGET_EXCEEDED" for v in c["violations"])  # 화면이 팝업을 띄울 근거
    return c


def test_without_consent_d_still_blocks(env):
    v = env.start(OVER_MESSAGE, delegation=CARD_ONLY)
    _over_candidate(v)
    v = env.reply(v, type="SELECT_CANDIDATE", candidate_index=0, allow_over_budget=False)
    v = env.reply(v, type="SELECT_PAYMENT_METHOD", payment_method_id="CARD001")
    assert v["state"] == "BLOCKED"
    assert v["blocked"]["reason_codes"] == ["BUDGET_EXCEEDED"]


def test_consent_lets_over_budget_order_through_to_payment(env):
    v = env.start(OVER_MESSAGE, delegation=CARD_ONLY)
    _over_candidate(v)
    v = env.reply(v, type="SELECT_CANDIDATE", candidate_index=0, allow_over_budget=True)
    assert v["state"] == "PAYMENT_METHOD_SELECTION"
    v = env.reply(v, type="SELECT_PAYMENT_METHOD", payment_method_id="CARD001")
    assert v["state"] == "AWAITING_APPROVAL"

    ob = v["prompt"]["over_budget"]
    assert ob["limit_krw"] == 15000 and ob["over_krw"] == ob["charge_krw"] - 15000 > 0
    assert any("사용자 확인" in c for c in v["prompt"]["checks"])
    assert not any(
        "이내" in c for c in v["prompt"]["checks"]
    )  # "예산 이내"라고 거짓 표시하지 않는다
    assert v["prompt"]["order"]["charge_krw"] == ob["charge_krw"]

    done = env.reply(v, type="APPROVE")
    assert done["state"] == "COMPLETED"
    assert done["result"]["charge_krw"] == ob["charge_krw"]


def test_consent_is_recorded_and_policy_is_reregistered_in_d(env):
    v = env.start(OVER_MESSAGE, delegation=CARD_ONLY)
    v = env.reply(v, type="SELECT_CANDIDATE", candidate_index=0, allow_over_budget=True)
    v = env.reply(v, type="SELECT_PAYMENT_METHOD", payment_method_id="CARD001")
    events = env.events(v)
    reply = next(
        e for e in events if e["event_type"] == "USER_REPLY" and "candidate_index" in e["detail"]
    )
    assert reply["detail"]["allow_over_budget"] is True  # 동의가 O-04 기록에 남는다
    d01 = [e for e in events if e["service"] == "D" and e["detail"].get("api") == "D-01"]
    assert len(d01) == 2  # 처음 등록 + 동의 뒤 한도 재등록 (D의 정상 경로)


def test_original_delegation_is_not_changed(env):
    v = env.start(OVER_MESSAGE, delegation={**CARD_ONLY, "max_charge_krw": 15000})
    v = env.reply(v, type="SELECT_CANDIDATE", candidate_index=0, allow_over_budget=True)
    v = env.reply(v, type="SELECT_PAYMENT_METHOD", payment_method_id="CARD001")
    assert v["state"] == "AWAITING_APPROVAL"  # 예산과 위임 상한을 둘 다 이번 금액까지 올렸다
    with env.sf() as s:
        # 사용자의 설정은 그대로
        assert s.get(Run, v["request_id"]).delegation["max_charge_krw"] == 15000


def test_consent_is_ignored_for_candidate_within_budget(env):
    v = env.start(OK_MESSAGE)
    c = v["prompt"]["candidates"][0]
    assert not any(x["code"] == "BUDGET_EXCEEDED" for x in c["violations"])
    v = env.reply(v, type="SELECT_CANDIDATE", candidate_index=0, allow_over_budget=True)
    v = env.reply(v, type="SELECT_PAYMENT_METHOD", payment_method_id="CARD001")
    assert v["state"] == "AWAITING_APPROVAL"
    assert "over_budget" not in v["prompt"]
    assert any("이내" in c for c in v["prompt"]["checks"])
    d01 = [e for e in env.events(v) if e["service"] == "D" and e["detail"].get("api") == "D-01"]
    assert len(d01) == 1  # 한도를 다시 등록하지 않았다


def test_consent_does_not_cover_amount_larger_than_shown(env):
    v = env.start(OVER_MESSAGE, delegation=CARD_ONLY)
    v = env.reply(v, type="SELECT_CANDIDATE", candidate_index=0, allow_over_budget=True)
    # 사용자가 본 금액보다 실제 견적이 커졌다고 가정: 동의 범위를 1원으로 줄인다
    with env.sf() as s:
        run = s.get(Run, v["request_id"])
        run.context["over_budget"]["max_total_krw"] = 1
        flag_modified(run, "context")
        s.commit()
    v = env.reply(v, type="SELECT_PAYMENT_METHOD", payment_method_id="CARD001")
    assert v["state"] == "BLOCKED"
    assert v["blocked"]["reason_codes"] == ["BUDGET_EXCEEDED"]
