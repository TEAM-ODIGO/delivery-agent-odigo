"""가짜 B (주문): B-01 주문안, B-05 혜택 연결, B-06 승인 대상, B-07 실행 전 확인, B-08 결제 결과.

- 가격은 공용 데이터(catalog)에서 B가 직접 읽는다 (A가 준 단가를 믿지 않음).
- 혜택은 C-05로 계산받는다 (B → C-05 허용 호출).
- B-07은 D-04로 승인 상태를 읽는다 (B → D-04 허용 호출, 읽기 전용).
  명세상 B-07 요청·D-04 응답에 proposal_hash가 없어서 execution_input.proposal_hash는 None으로 둔다.
  → O가 D-02 응답의 값으로 채운다 (협의 사항).
"""

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from app.core import clock
from app.core.errors import AppError
from app.fakes import catalog
from app.fakes.scenario import ScenarioBook

CalculateFn = Callable[[dict[str, Any]], dict[str, Any]]
ApprovalReader = Callable[[str], dict[str, Any]]  # D-04: approval_id → 승인 상태


def _err(status: int, code: str, message: str, next_action: str = "NONE", retryable=False):
    return AppError(
        status,
        code,
        message,
        {
            "current_order_version": None,
            "current_benefit_revision": None,
            "current_approval_target_id": None,
            "next_action": next_action,
            "retryable": retryable,
        },
    )


class FakeOrdering:
    def __init__(
        self,
        book: ScenarioBook,
        calculate: CalculateFn,
        read_approval: ApprovalReader | None = None,
        now=clock.now,
    ):
        self.book = book
        self.calculate = calculate
        self.read_approval = read_approval
        self.now = now
        self.plans: dict[str, dict] = {}
        self.idem: dict[str, dict] = {}
        self.payment_events: dict[str, dict] = {}
        self._b01_calls: dict[str, int] = {}

    def _plan(self, order_plan_id: str) -> dict:
        p = self.plans.get(order_plan_id)
        if p is None:
            raise _err(404, "ORDER_PLAN_NOT_FOUND", "주문안을 찾을 수 없습니다.")
        return p

    # --- B-01 ---------------------------------------------------------------------
    def create_order_plan(self, body: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        if idempotency_key in self.idem:
            return self.idem[idempotency_key]
        rid = body["request_id"]
        n = self._b01_calls[rid] = self._b01_calls.get(rid, 0) + 1
        rest = catalog.restaurant(body["restaurant_id"])

        issues = []
        if rest is None:
            issues.append({"code": "RESTAURANT_NOT_FOUND", "restaurant_id": body["restaurant_id"]})
        soldout = self.book.has(rid, "SOLDOUT_ALL") or (
            self.book.has(rid, "SOLDOUT_FIRST") and n == 1
        )
        lines = []
        for it in body["items"]:
            m = catalog.menu(body["restaurant_id"], it["menu_id"]) if rest else None
            if it.get("quantity", 0) < 1:
                raise _err(422, "VALIDATION_ERROR", "수량은 양의 정수여야 합니다.")
            if m is None:
                issues.append({"code": "MENU_NOT_FOUND", "menu_id": it["menu_id"]})
                continue
            if soldout:
                issues.append(
                    {"code": "MENU_SOLD_OUT", "menu_id": m.menu_id, "message": f"{m.name} 품절"}
                )
            opts = [o for o in m.options if o.option_id in it.get("option_ids", [])]
            lines.append({"menu": m, "quantity": it["quantity"], "options": opts})

        if issues:
            res = {
                "request_id": rid,
                "order_plans": [],
                "issues": issues,
                "excluded_candidate_ids": [body.get("candidate_id")],
                "next_action": "RESEARCH_REQUIRED",
            }
            self.idem[idempotency_key] = res
            return res

        subtotal = sum(
            (ln["menu"].price_krw + sum(o.price_krw for o in ln["options"])) * ln["quantity"]
            for ln in lines
        )
        plan_id = "op_" + uuid.uuid4().hex[:8]
        quote = {
            "quote_id": "QT" + uuid.uuid4().hex[:8].upper(),
            "restaurant_id": rest.restaurant_id,
            "menu_subtotal_krw": subtotal,
            "delivery_fee_krw": rest.delivery_fee_krw,
            "pre_financial_amount_krw": subtotal + rest.delivery_fee_krw,
            "expires_at": (self.now() + timedelta(minutes=30)).isoformat(),
        }
        budget = (body.get("constraints") or {}).get("budget_krw")
        self.plans[plan_id] = {
            "request_id": rid,
            "user_id": body.get("user_id", "-"),  # C-05는 사용자의 결제수단을 찾으려고 user_id가 필요
            "order_plan_id": plan_id,
            "order_version": 1,
            "lines": lines,
            "quote": quote,
            "budget": budget,
            "constraints": body.get("constraints") or {},
            "benefit": None,
            "benefit_revision": 0,
            "target": None,
        }
        res = {
            "request_id": rid,
            "order_plans": [
                {
                    "order_plan_id": plan_id,
                    "order_version": 1,
                    "validation_status": "VALID",
                    "quote_status": "ACTIVE",
                    "quote": dict(quote),
                    "budget_check": {
                        "basis": "POST_FINANCIAL",
                        "limit_krw": budget,
                        "compared_krw": None,
                        "status": "PENDING_BENEFIT",
                    },
                }
            ],
            "issues": [],
            "excluded_candidate_ids": [],
            "next_action": "CALCULATE_BENEFITS",
        }
        self.idem[idempotency_key] = res
        return res

    # --- B-05 ---------------------------------------------------------------------
    def bind_benefit(
        self, order_plan_id: str, body: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        if idempotency_key in self.idem:
            return self.idem[idempotency_key]
        p = self._plan(order_plan_id)
        if (
            body["base_order_version"] != p["order_version"]
            or body["quote_id"] != p["quote"]["quote_id"]
        ):
            raise _err(409, "VERSION_CONFLICT", "현재 주문안과 버전이 다릅니다.")
        if self.book.consume(p["request_id"], "B05_STALE"):
            raise _err(409, "STALE_CALCULATION", "현재 견적과 다른 혜택 결과입니다.")
        q = p["quote"]
        calc = self.calculate(
            {
                "request_id": p["request_id"],
                "user_id": p.get("user_id", "-"),
                "restaurant_id": q["restaurant_id"],
                "menu_subtotal_krw": q["menu_subtotal_krw"],
                "delivery_fee_krw": q["delivery_fee_krw"],
                "payment_method_id": body["payment_method_id"],
                "benefit_rule_version": body["benefit_rule_version"],
            }
        )
        p["benefit_revision"] += 1
        p["benefit"] = {
            "benefit_revision": p["benefit_revision"],
            "payment_method_id": body["payment_method_id"],
            "instant_discount_krw": calc["instant_discount_krw"],
            "charge_krw": calc["charge_krw"],
            "benefit_rule_version": calc["benefit_rule_version"],
        }
        status = "PASS" if p["budget"] is None or calc["charge_krw"] <= p["budget"] else "FAIL"
        res = {
            "view": {
                "order_plan_id": order_plan_id,
                "order_version": p["order_version"],
                "validation_status": "VALID",
                "quote_status": "ACTIVE",
                "quote": dict(q),
                "budget_check": {
                    "basis": "POST_FINANCIAL",
                    "limit_krw": p["budget"],
                    "compared_krw": calc["charge_krw"],
                    "status": status,
                },
            },
            "benefit_binding": dict(p["benefit"]),
            "invalidated_approval_target_ids": [],
            "next_action": "BUILD_APPROVAL_TARGET",
        }
        self.idem[idempotency_key] = res
        return res

    # --- B-06 ---------------------------------------------------------------------
    def create_approval_target(
        self, order_plan_id: str, body: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        if idempotency_key in self.idem:
            return self.idem[idempotency_key]
        p = self._plan(order_plan_id)
        b = p["benefit"]
        if b is None or body["expected_benefit_revision"] != b["benefit_revision"]:
            raise _err(409, "BENEFIT_RESULT_MISMATCH", "현재 채택한 혜택 결과와 일치하지 않습니다.")
        if (
            body["base_order_version"] != p["order_version"]
            or body["quote_id"] != p["quote"]["quote_id"]
        ):
            raise _err(409, "VERSION_CONFLICT", "현재 주문안과 버전이 다릅니다.")
        q = p["quote"]
        rest = catalog.restaurant(q["restaurant_id"])
        unverified = []
        local_only = (p["constraints"].get("payment_requirements") or {}).get("local_currency_only")
        if local_only and rest.local_currency_merchant is None:
            unverified.append("LOCAL_CURRENCY_MERCHANT")
        target = {
            "approval_target_id": "target_" + uuid.uuid4().hex[:8],
            "request_id": p["request_id"],
            "order_plan_id": order_plan_id,
            "order_version": p["order_version"],
            "constraints_version": None,
            "benefit_revision": b["benefit_revision"],
            "status": "PREPARED",
            "quote": {
                "quote_id": q["quote_id"],
                "restaurant_id": q["restaurant_id"],
                "items": [
                    {
                        "menu_id": ln["menu"].menu_id,
                        "name": ln["menu"].name,
                        "description": ln["menu"].description,
                        "quantity": ln["quantity"],
                        "unit_price_krw": ln.get("unit_price_krw", ln["menu"].price_krw),
                        "options": [
                            {"option_id": o.option_id, "price_krw": o.price_krw}
                            for o in ln["options"]
                        ],
                    }
                    for ln in p["lines"]
                ],
                "menu_subtotal_krw": q["menu_subtotal_krw"],
                "delivery_fee_krw": q["delivery_fee_krw"],
                "payment_method_id": b["payment_method_id"],
                "instant_discount_krw": b["instant_discount_krw"],
                "charge_krw": b["charge_krw"],
                "catalog_version": catalog.CATALOG_VERSION,
                "benefit_rule_version": b["benefit_rule_version"],
                "expires_at": q["expires_at"],
                "unverified_conditions": unverified,
            },
            "created_at": self.now().isoformat(),
        }
        p["target"] = target
        res = {"approval_target": target, "next_action": "REQUEST_D_APPROVAL"}
        self.idem[idempotency_key] = res
        return res

    # --- B-10 (로컬 확장, #86): 요기요 장바구니 가격으로 재견적 -------------------------------
    def requote(
        self, order_plan_id: str, body: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        if idempotency_key in self.idem:
            return self.idem[idempotency_key]
        p = self._plan(order_plan_id)
        if (
            body["base_order_version"] != p["order_version"]
            or body["quote_id"] != p["quote"]["quote_id"]
        ):
            raise _err(409, "VERSION_CONFLICT", "현재 주문안과 버전이 다릅니다.")
        prices = {str(m["menu_id"]): m["unit_price_krw"] for m in body.get("menu_prices") or []}
        subtotal = 0
        for ln in p["lines"]:
            if ln["menu"].menu_id in prices:
                ln["unit_price_krw"] = prices[ln["menu"].menu_id]
            unit = ln.get("unit_price_krw", ln["menu"].price_krw)
            subtotal += (unit + sum(o.price_krw for o in ln["options"])) * ln["quantity"]
        old = p["quote"]
        fee = old["delivery_fee_krw"] if body.get("delivery_fee_krw") is None else body["delivery_fee_krw"]
        p["order_version"] += 1
        p["quote"] = {
            **old,
            "quote_id": "QT" + uuid.uuid4().hex[:8].upper(),
            "menu_subtotal_krw": subtotal,
            "delivery_fee_krw": fee,
            "pre_financial_amount_krw": subtotal + fee,
            "expires_at": (self.now() + timedelta(minutes=30)).isoformat(),
        }
        p["benefit"], p["target"] = None, None
        res = {
            "view": {
                "order_plan_id": order_plan_id,
                "order_version": p["order_version"],
                "validation_status": "VALID",
                "quote_status": "ACTIVE",
                "quote": dict(p["quote"]),
            },
            "previous_quote_id": old["quote_id"],
            "invalidated_approval_target_ids": [],
            "next_action": "CALCULATE_BENEFITS",
        }
        self.idem[idempotency_key] = res
        return res

    # --- B-07 ---------------------------------------------------------------------
    def check_execution(self, order_plan_id: str, body: dict[str, Any]) -> dict[str, Any]:
        p = self._plan(order_plan_id)
        t = p["target"]
        reasons = []
        if t is None or t["approval_target_id"] != body["approval_target_id"]:
            reasons.append("APPROVAL_TARGET_MISMATCH")
        if (
            body["quote_id"] != p["quote"]["quote_id"]
            or body["base_order_version"] != p["order_version"]
        ):
            reasons.append("QUOTE_CHANGED")
        if self.read_approval is not None:
            try:
                approval = self.read_approval(body["approval_id"])
            except Exception as exc:
                raise _err(
                    503,
                    "APPROVAL_UNAVAILABLE",
                    "D 승인 상태를 확인하지 못했습니다.",
                    "CHECK_DATA",
                    True,
                ) from exc
            if approval.get("status") != "APPROVED":
                reasons.append("APPROVAL_NOT_CONFIRMED")
            if approval.get("execution_id") != body["execution_id"]:
                reasons.append("EXECUTION_ID_MISMATCH")
        ready = not reasons
        return {
            "execution_check_id": "check_" + uuid.uuid4().hex[:8],
            "stage": body.get("stage", "MOCK_PAYMENT"),
            "ready": ready,
            "checked_at": self.now().isoformat(),
            "reasons": reasons,
            "execution_input": {
                "request_id": p["request_id"],
                "execution_id": body["execution_id"],
                "approval_id": body["approval_id"],
                "quote_id": p["quote"]["quote_id"],
                "proposal_hash": None,  # 명세 공백: B가 얻을 경로 없음 → O가 채움
                "payment_method_id": p["benefit"]["payment_method_id"] if p["benefit"] else None,
                "charge_krw": p["benefit"]["charge_krw"] if p["benefit"] else None,
            }
            if ready
            else None,
            "next_action": "NONE",
        }

    # --- B-08 ---------------------------------------------------------------------
    def record_payment_event(
        self, order_plan_id: str, body: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        p = self._plan(order_plan_id)
        if self.book.consume(p["request_id"], "B08_FAIL_ONCE"):
            raise _err(503, "ORDERING_UNAVAILABLE", "B 응답 없음 (#B08장애)", retryable=True)
        prev = self.payment_events.get(body["event_id"])
        if prev is not None:
            if prev["body"] != body:
                raise _err(409, "EVENT_CONFLICT", "같은 이벤트 ID에 다른 내용이 전달됐습니다.")
            return prev["res"]
        res = {
            "event_id": body["event_id"],
            "payment_id": body["payment_id"],
            "linked_quote_id": body["quote_id"],
            "application_status": "APPLIED",
            "payment_status": body["status"],
            "applied_to_current_quote": True,
            "next_action": "NONE",
        }
        self.payment_events[body["event_id"]] = {"body": dict(body), "res": res}
        return res
