"""rules.py 단위 테스트: DB 없이 규칙 함수만 검사한다. 사유 코드마다 하나 이상."""

from datetime import timedelta

import pytest

from app.modules.delegation.constants import ApprovalStatus, PaymentMethodType, Reason
from app.modules.delegation.rules import (
    ApprovalView,
    PolicyView,
    check_order,
    check_payment,
    recompute_subtotal,
    spending_limit,
)
from app.modules.delegation.schemas import (
    AuthorizationCheckRequest,
    Constraints,
    Delegation,
    OrderValidationRequest,
)
from tests.conftest import T0, make_quote


def policy_view(**over) -> PolicyView:
    base = dict(
        policy_id="POL1",
        version=1,
        is_active=True,
        constraints_confirmed=True,
        constraints=Constraints(budget_krw=40000),
        delegation=Delegation(
            allowed_payment_method_ids=["CARD001", "LOCAL001"], max_charge_krw=40000
        ),
    )
    base.update(over)
    return PolicyView(**base)


def order(quote=None, **over) -> OrderValidationRequest:
    data = dict(
        request_id="REQ001",
        user_id="U001",
        policy_id="POL1",
        policy_version=1,
        approval_target_id="target_001",
        order_version=1,
        quote=quote or make_quote(),
    )
    data.update(over)
    return OrderValidationRequest.model_validate(data)


def run(policy=None, req=None, *, discount=2000, method=PaymentMethodType.CARD, now=T0):
    return check_order(
        policy or policy_view(),
        req or order(),
        now=now,
        expected_discount=discount,
        payment_method_type=method,
    )


# --- 금액 계산 ----------------------------------------------------------------------
def test_subtotal_includes_options_times_quantity():
    q = order(
        make_quote(
            items=[
                {
                    "menu_id": "M",
                    "name": "치킨",
                    "quantity": 2,
                    "unit_price_krw": 10000,
                    "options": [
                        {"option_id": "O1", "price_krw": 1000},
                        {"option_id": "O2", "price_krw": 500},
                    ],
                }
            ]
        )
    ).quote
    assert recompute_subtotal(q) == (10000 + 1000 + 500) * 2


def test_spending_limit_is_smaller_of_budget_and_delegation():
    assert spending_limit(Constraints(budget_krw=30000), Delegation(max_charge_krw=40000)) == 30000
    assert spending_limit(Constraints(), Delegation(max_charge_krw=40000)) == 40000
    assert spending_limit(Constraints(), Delegation()) is None


# --- D-02 규칙 ----------------------------------------------------------------------
def test_valid_order_passes():
    assert run().ok


def test_policy_version_mismatch():
    assert Reason.POLICY_VERSION_MISMATCH in run(policy_view(is_active=False)).reasons
    assert Reason.POLICY_VERSION_MISMATCH in run(req=order(policy_version=2)).reasons


def test_constraints_not_confirmed():
    assert run(policy_view(constraints_confirmed=False)).reasons == [
        Reason.CONSTRAINTS_NOT_CONFIRMED
    ]


def test_build_order_not_delegated():
    p = policy_view(delegation=Delegation(allowed_actions=["SEARCH"]))
    assert Reason.ACTION_NOT_ALLOWED in run(p).reasons


def test_quote_expired():
    assert run(now=T0 + timedelta(minutes=30)).reasons == [Reason.QUOTE_EXPIRED]


def test_unverified_conditions_block():
    r = run(req=order(make_quote(unverified_conditions=["최소주문금액 미확인"])))
    assert r.reasons == [Reason.QUOTE_DATA_UNVERIFIED]


def test_benefit_unavailable_blocks():
    assert Reason.QUOTE_DATA_UNVERIFIED in run(discount=None).reasons


def test_inflated_discount_is_amount_mismatch():
    # B가 할인을 5,000원으로 부풀렸지만 C 계산은 2,000원
    r = run(req=order(make_quote(instant_discount_krw=5000, charge_krw=30000)))
    assert r.reasons == [Reason.AMOUNT_MISMATCH]
    assert r.details["discount_expected"] == 2000


def test_wrong_subtotal_is_amount_mismatch():
    r = run(req=order(make_quote(menu_subtotal_krw=31000, charge_krw=32000)))
    assert Reason.AMOUNT_MISMATCH in r.reasons


def test_wrong_charge_is_amount_mismatch():
    assert run(req=order(make_quote(charge_krw=30000))).reasons == [Reason.AMOUNT_MISMATCH]


def test_budget_exceeded():
    q = make_quote(
        items=[{"menu_id": "M", "name": "치킨", "quantity": 2, "unit_price_krw": 22000}],
        menu_subtotal_krw=44000,
        charge_krw=45000,
    )
    r = run(req=order(q))
    assert r.reasons == [Reason.BUDGET_EXCEEDED]
    assert r.details == {"charge_krw": 45000, "limit_krw": 40000}


def test_payment_method_not_in_allow_list():
    q = make_quote(payment_method_id="CARD999", instant_discount_krw=0, charge_krw=35000)
    assert run(req=order(q), discount=0).reasons == [Reason.PAYMENT_METHOD_NOT_ALLOWED]


def test_local_currency_required():
    p = policy_view(
        constraints=Constraints(
            budget_krw=40000, payment_requirements={"local_currency_only": True}
        )
    )
    assert run(p).reasons == [Reason.PAYMENT_METHOD_NOT_ALLOWED]
    assert run(p, method=PaymentMethodType.LOCAL_CURRENCY).ok


def test_excluded_ingredient_conflict_and_unknown():
    p = policy_view(constraints=Constraints(budget_krw=40000, excluded_ingredients=["땅콩"]))
    conflict = make_quote(
        items=[
            {
                "menu_id": "M",
                "name": "땅콩강정",
                "quantity": 1,
                "unit_price_krw": 30000,
                "options": [{"option_id": "O", "price_krw": 2000}],
            }
        ]
    )
    unknown = make_quote(
        items=[
            {
                "menu_id": "M",
                "quantity": 1,
                "unit_price_krw": 30000,
                "options": [{"option_id": "O", "price_krw": 2000}],
            }
        ]
    )
    assert run(p, order(conflict)).reasons == [Reason.INGREDIENT_CONFLICT]
    assert run(p, order(unknown)).reasons == [Reason.INGREDIENT_UNKNOWN]


def test_collects_every_failure_not_just_first():
    p = policy_view(constraints_confirmed=False)
    r = run(p, order(make_quote(charge_krw=50000)), now=T0 + timedelta(hours=1))
    assert {
        Reason.CONSTRAINTS_NOT_CONFIRMED,
        Reason.QUOTE_EXPIRED,
        Reason.AMOUNT_MISMATCH,
        Reason.BUDGET_EXCEEDED,
    } <= set(r.reasons)


# --- D-06 규칙 ----------------------------------------------------------------------
def approval_view(**over) -> ApprovalView:
    base = dict(
        approval_id="A1",
        status=ApprovalStatus.APPROVED,
        execution_id="EXE-1",
        quote_id="QT001",
        proposal_hash="sha256:h",
        charge_krw=33000,
        payment_method_id="CARD001",
    )
    base.update(over)
    return ApprovalView(**base)


def pay(**over) -> AuthorizationCheckRequest:
    base = dict(
        execution_id="EXE-1",
        approval_id="A1",
        quote_id="QT001",
        proposal_hash="sha256:h",
        charge_krw=33000,
        payment_method_id="CARD001",
    )
    base.update(over)
    return AuthorizationCheckRequest(**base)


def test_payment_matching_approval_passes():
    assert check_payment(approval_view(), policy_view(), pay()).ok


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (ApprovalStatus.PENDING, Reason.APPROVAL_NOT_CONFIRMED),
        (ApprovalStatus.REVOKED, Reason.APPROVAL_REVOKED),
        (ApprovalStatus.EXPIRED, Reason.APPROVAL_EXPIRED),
    ],
)
def test_payment_blocked_by_status(status, reason):
    assert check_payment(approval_view(status=status), policy_view(), pay()).reasons == [reason]


def test_payment_without_approval():
    assert check_payment(None, None, pay()).reasons == [Reason.APPROVAL_NOT_FOUND]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("execution_id", "EXE-X", Reason.EXECUTION_ID_MISMATCH),
        ("quote_id", "QT999", Reason.QUOTE_ID_MISMATCH),
        ("proposal_hash", "sha256:other", Reason.PROPOSAL_HASH_MISMATCH),
        ("charge_krw", 30000, Reason.PAYMENT_AMOUNT_MISMATCH),
        ("payment_method_id", "CARD999", Reason.PAYMENT_METHOD_MISMATCH),
    ],
)
def test_payment_any_difference_blocks(field, value, reason):
    assert check_payment(approval_view(), policy_view(), pay(**{field: value})).reasons == [reason]


def test_consumed_approval():
    consumed = approval_view(status=ApprovalStatus.CONSUMED)
    assert check_payment(consumed, policy_view(), pay()).ok  # 같은 실행·같은 내용 → 재응답
    assert check_payment(consumed, policy_view(), pay(execution_id="EXE-2")).reasons == [
        Reason.ALREADY_EXECUTED
    ]
    assert check_payment(consumed, policy_view(), pay(charge_krw=1)).reasons == [
        Reason.EXECUTION_CONFLICT
    ]


def test_payment_needs_active_policy_and_delegation():
    assert check_payment(approval_view(), policy_view(is_active=False), pay()).reasons == [
        Reason.POLICY_CHANGED
    ]
    no_pay = policy_view(delegation=Delegation(allowed_actions=["SEARCH", "BUILD_ORDER"]))
    assert check_payment(approval_view(), no_pay, pay()).reasons == [Reason.ACTION_NOT_ALLOWED]
