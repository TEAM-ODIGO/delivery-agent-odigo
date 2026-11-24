"""승인 화면: 요기요 장바구니 금액으로 견적·승인 금액 갱신 (이슈 #86)."""

import uuid

import pytest

from app.core.errors import AppError
from app.modules.cart.models import CartJob


def _cart(env, v, *, price_delta=0, fee=None, quantity_delta=0, status="CART_MISMATCH"):
    """요기요 담기 결과를 흉내 낸다 (승인 대상 견적 기준으로 가격·배달비만 바꿈)."""
    q = env.svc()._load(v["request_id"], "U001").context["target_quote"]
    items = [
        {
            "menu_id": i["menu_id"],
            "name": i.get("name"),
            "quantity": i["quantity"] + quantity_delta,
            "unit_price_krw": i["unit_price_krw"] + price_delta,
        }
        for i in q["items"]
    ]
    with env.sf() as s:
        s.add(
            CartJob(
                job_id="CART-" + uuid.uuid4().hex[:8],
                request_id=v["request_id"],
                user_id="U001",
                status=status,
                restaurant_id=q["restaurant_id"],
                expected={"items": q["items"]},
                cart_items=items,
                menu_subtotal_krw=sum(i["unit_price_krw"] * i["quantity"] for i in items),
                delivery_fee_krw=q["delivery_fee_krw"] if fee is None else fee,
                mismatches=["가격이 다름"],
                mode="fake",
                created_at=env.clock(),
            )
        )
        s.commit()
    return q


def test_requote_updates_charge_and_keeps_previous_for_screen(env):
    v = env.flow("AWAITING_APPROVAL")
    old = _cart(env, v, price_delta=1000, fee=None)
    old_fee = old["delivery_fee_krw"]
    with env.sf() as s:  # 배달비도 1,000원 올린다
        job = s.query(CartJob).filter_by(request_id=v["request_id"]).one()
        job.delivery_fee_krw = old_fee + 1000
        s.commit()
    v2 = env.reply(v, type="REQUOTE_FROM_CART")
    assert v2["state"] == "AWAITING_APPROVAL"
    rq = v2["prompt"]["requote"]
    assert rq["previous"]["charge_krw"] == old["charge_krw"]
    assert {c["kind"] for c in rq["changes"]} == {"MENU_PRICE", "DELIVERY_FEE"}
    new_charge = v2["prompt"]["order"]["charge_krw"]
    assert new_charge == old["charge_krw"] + 2000
    # 결제도 새 금액으로 (D 해시·B-07 확인 통과)
    done = env.reply(v2, type="APPROVE")
    assert done["state"] == "COMPLETED" and done["result"]["charge_krw"] == new_charge
    # 같은 담기 결과로 두 번은 하지 않는다
    with pytest.raises(AppError) as e:
        env.reply(done, type="REQUOTE_FROM_CART")
    assert e.value.status == 409


def test_requote_refuses_when_items_differ_or_nothing_changed(env):
    v = env.flow("AWAITING_APPROVAL")
    _cart(env, v, quantity_delta=1)
    with pytest.raises(AppError) as e:
        env.reply(v, type="REQUOTE_FROM_CART")
    assert e.value.code == "CART_ITEMS_DIFFER"
    assert env.get(v)["state"] == "AWAITING_APPROVAL"


def test_requote_only_when_cart_mismatch(env):
    v = env.flow("AWAITING_APPROVAL")
    with pytest.raises(AppError) as e:
        env.reply(v, type="REQUOTE_FROM_CART")
    assert e.value.code == "CART_NOT_MISMATCH"
    _cart(env, v, price_delta=0)
    with pytest.raises(AppError) as e:
        env.reply(v, type="REQUOTE_FROM_CART")
    assert e.value.code == "NOTHING_TO_REQUOTE"


def test_requote_over_budget_is_blocked_by_d(env):
    v = env.flow("AWAITING_APPROVAL")  # 예산 20,000원
    _cart(env, v, price_delta=10000)
    v2 = env.reply(v, type="REQUOTE_FROM_CART")
    assert v2["state"] == "BLOCKED"
    assert "BUDGET_EXCEEDED" in v2["blocked"]["reason_codes"]
