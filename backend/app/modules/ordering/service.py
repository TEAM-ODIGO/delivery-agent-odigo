"""B(주문) 업무 로직. 라우터는 얇게 두고 여기서 명세 규칙을 지킨다.

    B-01 create_order_plan     주문안 생성·검증 (A 후보를 수집 데이터로 다시 확인 → 견적)
    B-02 get_order_plan        주문안·견적 조회 (재계산 없음)
    B-05 bind_benefit          C-05 혜택 결과를 현재 견적에 연결, 예산 확인
    B-06 create_approval_target  D-02로 보낼 승인 대상 묶기
    B-07 check_execution       D-03 직후 승인 상태(D-04)·견적 확인, E-01 입력 준비
    B-08 record_payment_event  E 결제 결과를 견적에 연결

모든 메서드는 (HTTP 상태 코드, 응답 dict)를 돌려준다.
같은 서버 안의 O는 HTTP 대신 이 메서드를 바로 불러도 된다 (통합 서버의 어댑터가 그렇게 한다).
"""

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import now as clock_now
from app.core.clock import to_kst
from app.modules.ordering import ports
from app.modules.ordering.catalog import read_restaurant
from app.modules.ordering.config import get_settings
from app.modules.ordering.constants import (
    FINAL_PAYMENT_STATUSES,
    UNVERIFIED_LOCAL_CURRENCY,
    ApplicationStatus,
    NextAction,
    PaymentStatus,
    PlanStatus,
    TargetStatus,
)
from app.modules.ordering.errors import OrderingError
from app.modules.ordering.models import (
    ApprovalTarget,
    BenefitBinding,
    ExecutionCheck,
    IdempotencyRecord,
    OrderPlan,
    PaymentEvent,
    Quote,
)
from app.modules.ordering.pricing import issue, price_items
from app.modules.ordering.schemas import (
    BindBenefitBody,
    CreateApprovalTargetBody,
    CreateOrderPlanBody,
    ExecutionCheckBody,
    PaymentEventBody,
)
from app.modules.ordering.views import binding_view, iso, plan_view, split_fields, target_view

Result = tuple[int, dict]


def new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:16]


def body_hash(*parts) -> str:
    text = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class OrderingService:
    def __init__(
        self,
        db: Session,
        benefit: ports.BenefitCalculator | None = None,
        approvals: ports.ApprovalReader | None = None,
        clock: Callable[[], datetime] = clock_now,
    ):
        self.db = db
        self.benefit = benefit or ports.benefit_calculator()
        self.approvals = approvals or ports.approval_reader()
        self.clock = clock

    # ================================================================ 공통 도우미
    def _require_key(self, given: str | None, expected: str) -> None:
        if given is None:
            raise OrderingError(400, "VALIDATION_ERROR", f"Idempotency-Key 헤더가 필요합니다 ({expected}).",
                                fields=[{"field": "Idempotency-Key", "message": "필수 헤더입니다."}])
        if given != expected:
            raise OrderingError(400, "VALIDATION_ERROR", f"Idempotency-Key는 {expected} 이어야 합니다.",
                                fields=[{"field": "Idempotency-Key", "message": "형식이 명세와 다릅니다."}])

    def _replay(self, key: str, operation: str, request_hash: str, conflict_code: str = "IDEMPOTENCY_CONFLICT"):
        """같은 키로 이미 처리했으면 처음 응답을 그대로 돌려준다. 내용이 다르면 409."""
        record = self.db.get(IdempotencyRecord, key)
        if record is None:
            return None
        if record.operation != operation or record.request_hash != request_hash:
            message = "같은 이벤트 ID에 다른 내용이 전달됐습니다." if conflict_code == "EVENT_CONFLICT" \
                else "같은 멱등 키로 다른 내용의 요청이 이미 있습니다."
            raise OrderingError(409, conflict_code, message)
        return record.status_code, record.response

    def _remember(self, key: str, operation: str, request_hash: str, status: int, response: dict) -> None:
        self.db.add(IdempotencyRecord(
            idempotency_key=key,
            operation=operation,
            request_hash=request_hash,
            status_code=status,
            response=response,
            created_at=self.clock(),
        ))

    def _commit_or_replay(self, key: str, operation: str, request_hash: str, result: Result,
                          conflict_code: str = "IDEMPOTENCY_CONFLICT") -> Result:
        """같은 키가 거의 동시에 두 번 오면 먼저 저장된 쪽 응답을 돌려준다."""
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            replay = self._replay(key, operation, request_hash, conflict_code)
            if replay is None:
                raise
            return replay
        return result

    def _load_plan(self, order_plan_id: str, header_request_id: str | None, lock: bool = False) -> OrderPlan:
        statement = select(OrderPlan).where(OrderPlan.order_plan_id == order_plan_id)
        if lock:
            statement = statement.with_for_update()
        plan = self.db.scalars(statement).first()
        if plan is None:
            raise OrderingError(404, "ORDER_PLAN_NOT_FOUND", "주문안을 찾을 수 없습니다.")
        # 다른 주문 요청의 주문안을 건드리지 못하게 (X-Request-ID = 주문안을 만든 request_id)
        if header_request_id is not None and header_request_id != plan.request_id:
            raise OrderingError(403, "REQUEST_MISMATCH", "이 주문 요청의 주문안이 아닙니다.")
        return plan

    def _quote(self, plan: OrderPlan) -> Quote:
        return self.db.get(Quote, plan.current_quote_id)

    def _binding(self, plan: OrderPlan) -> BenefitBinding | None:
        if plan.current_benefit_revision is None:
            return None
        return self.db.scalars(
            select(BenefitBinding).where(
                BenefitBinding.order_plan_id == plan.order_plan_id,
                BenefitBinding.benefit_revision == plan.current_benefit_revision,
            )
        ).first()

    def _state_error(self, plan: OrderPlan, status: int, code: str, message: str,
                     next_action: str = NextAction.NONE) -> OrderingError:
        return OrderingError(
            status, code, message,
            next_action=next_action,
            current_order_version=plan.order_version,
            current_benefit_revision=plan.current_benefit_revision,
            current_approval_target_id=plan.current_approval_target_id,
        )

    def _check_current(self, plan: OrderPlan, base_order_version: int, quote_id: str) -> Quote:
        """B-05·B-06 공통: 주문안이 열려 있고, 버전·견적이 현재 것이며, 견적이 만료되지 않았는지."""
        if plan.status == PlanStatus.PAID:
            raise self._state_error(plan, 409, "ORDER_CLOSED", "이미 결제가 끝난 주문안입니다.")
        if base_order_version != plan.order_version:
            raise self._state_error(plan, 409, "VERSION_CONFLICT", "현재 주문 버전을 다시 조회해 주세요.")
        if quote_id != plan.current_quote_id:
            raise self._state_error(plan, 409, "QUOTE_NOT_CURRENT", "현재 견적이 아닙니다. 주문안을 다시 조회해 주세요.")
        quote = self._quote(plan)
        if self.clock() >= quote.expires_at:
            raise self._state_error(plan, 409, "QUOTE_EXPIRED", "견적 유효시간이 지났습니다.", NextAction.REVALIDATE)
        return quote

    def _supersede_targets(self, plan: OrderPlan) -> list[str]:
        """혜택·견적이 바뀌면 이전 승인 대상은 더 쓰지 않는다 (D-02 재호출로 이전 승인도 철회됨)."""
        invalidated = []
        targets = self.db.scalars(
            select(ApprovalTarget).where(
                ApprovalTarget.order_plan_id == plan.order_plan_id,
                ApprovalTarget.status == TargetStatus.PREPARED,
            )
        )
        for target in targets:
            target.status = TargetStatus.SUPERSEDED
            invalidated.append(target.approval_target_id)
        plan.current_approval_target_id = None
        return invalidated

    # ================================================================ B-01
    def create_order_plan(self, body: CreateOrderPlanBody, idempotency_key: str | None,
                          header_request_id: str | None = None, user_id: str | None = None) -> Result:
        if header_request_id is not None and header_request_id != body.request_id:
            raise OrderingError(400, "VALIDATION_ERROR", "X-Request-ID와 request_id가 다릅니다.",
                                fields=[{"field": "X-Request-ID", "message": "request_id와 같아야 합니다."}])
        key = f"{body.request_id}:b01:{body.candidate_id}"
        self._require_key(idempotency_key, key)
        request_hash = body_hash("B-01", body.model_dump(mode="json"))
        replay = self._replay(key, "B-01", request_hash)
        if replay is not None:
            return replay

        restaurant = read_restaurant(self.db, body.restaurant_id)
        if restaurant is None:
            result = self._unavailable(body, [issue("RESTAURANT_NOT_FOUND", "식당 정보를 찾을 수 없습니다.")])
            self._remember(key, "B-01", request_hash, *result)
            return self._commit_or_replay(key, "B-01", request_hash, result)

        priced = price_items(restaurant, [item.model_dump() for item in body.items])
        if not priced.orderable:
            result = self._unavailable(body, priced.issues)
            self._remember(key, "B-01", request_hash, *result)
            return self._commit_or_replay(key, "B-01", request_hash, result)

        notices = []
        if restaurant.catalog_version != body.catalog_version:
            # 견적은 지금 판본 가격으로 만든다. 어느 판본인지는 견적(catalog_version)에 남는다
            notices.append(issue(
                "CATALOG_VERSION_CHANGED", "후보를 만든 뒤 수집 데이터가 바뀌어 최신 가격으로 견적을 냈습니다.",
                requested=body.catalog_version, used=restaurant.catalog_version, severity="INFO",
            ))

        now = self.clock()
        plan = OrderPlan(
            order_plan_id=new_id("op_"),
            request_id=body.request_id,
            user_id=user_id,
            candidate_id=body.candidate_id,
            restaurant_id=body.restaurant_id,
            policy_id=body.policy_id,
            constraints_version=body.constraints_version,
            constraints=body.constraints.model_dump(mode="json"),
            budget_basis=body.budget_basis,
            requested_catalog_version=body.catalog_version,
            status=PlanStatus.OPEN,
            order_version=1,
            current_quote_id="",
            current_benefit_revision=None,
            current_approval_target_id=None,
            payment_status=None,
            created_at=now,
            updated_at=now,
        )
        quote = Quote(
            quote_id=new_id("QT"),
            order_plan_id=plan.order_plan_id,
            order_version=1,
            restaurant_id=restaurant.restaurant_id,
            catalog_version=restaurant.catalog_version,
            items=[item.to_dict() for item in priced.items],
            menu_subtotal_krw=priced.menu_subtotal_krw,
            delivery_fee_krw=priced.delivery_fee_krw,
            pre_financial_amount_krw=priced.pre_financial_amount_krw,
            local_currency_merchant=restaurant.local_currency_merchant,
            expires_at=now + timedelta(seconds=get_settings().quote_ttl_seconds),
            created_at=now,
        )
        plan.current_quote_id = quote.quote_id
        self.db.add(plan)
        self.db.flush()  # 견적이 주문안을 참조하므로 먼저 넣는다
        self.db.add(quote)

        response = {
            "request_id": body.request_id,
            "order_plans": [plan_view(plan, quote, None, now)],
            "issues": notices,
            "excluded_candidate_ids": [],
            "next_action": NextAction.CALCULATE_BENEFITS,
        }
        result = (201, response)
        self._remember(key, "B-01", request_hash, *result)
        return self._commit_or_replay(key, "B-01", request_hash, result)

    def _unavailable(self, body: CreateOrderPlanBody, issues: list[dict]) -> Result:
        """명세: 생성 불가는 200, order_plans=[], next_action=RESEARCH_REQUIRED"""
        for item in issues:
            item["candidate_id"] = body.candidate_id
        return 200, {
            "request_id": body.request_id,
            "order_plans": [],
            "issues": issues,
            "excluded_candidate_ids": [body.candidate_id],
            "next_action": NextAction.RESEARCH_REQUIRED,
        }

    # ================================================================ B-02
    def get_order_plan(self, order_plan_id: str, quote_id: str | None = None,
                       header_request_id: str | None = None) -> Result:
        plan = self._load_plan(order_plan_id, header_request_id)
        now = self.clock()
        quote = self._quote(plan)
        return 200, {
            "view": plan_view(plan, quote, self._binding(plan), now),
            "is_current_quote": quote_id is None or quote_id == plan.current_quote_id,
            "current_order_version": plan.order_version,
            "observed_at": iso(now),
        }

    # ================================================================ B-05
    def bind_benefit(self, order_plan_id: str, body: BindBenefitBody, idempotency_key: str | None,
                     header_request_id: str | None = None, user_id: str | None = None) -> Result:
        plan = self._load_plan(order_plan_id, header_request_id, lock=True)
        key = f"{plan.request_id}:b05:{body.base_order_version}:{body.payment_method_id}"
        if body.local_currency_payment_method_id is not None:
            # 분할결제: 같은 카드라도 지역화폐 금액이 다르면 결과가 다르므로 키에 넣는다 (키 = 결과를 바꾸는 값 전부)
            key += f":{body.local_currency_payment_method_id}:{body.local_currency_amount_krw}"
        self._require_key(idempotency_key, key)
        request_hash = body_hash("B-05", order_plan_id, body.model_dump(mode="json"))
        replay = self._replay(key, "B-05", request_hash)
        if replay is not None:
            return replay

        quote = self._check_current(plan, body.base_order_version, body.quote_id)
        user = user_id or plan.user_id
        if user is None:
            raise OrderingError(400, "VALIDATION_ERROR", "C-05 계산에 필요한 사용자(X-User-ID)가 없습니다.",
                                fields=[{"field": "X-User-ID", "message": "O가 인증한 user_id를 보내 주세요."}])

        calculation = self._calculate_benefit(plan, quote, body, user)

        now = self.clock()
        revision = (plan.current_benefit_revision or 0) + 1
        binding = BenefitBinding(
            order_plan_id=plan.order_plan_id,
            order_version=plan.order_version,
            quote_id=quote.quote_id,
            benefit_revision=revision,
            recommendation_id=body.recommendation_id,
            payment_method_id=body.payment_method_id,
            benefit_rule_version=calculation["benefit_rule_version"],
            instant_discount_krw=calculation["instant_discount_krw"],
            charge_krw=calculation["charge_krw"],
            applied_benefits=calculation.get("applied_benefits") or [],
            local_currency_payment_method_id=body.local_currency_payment_method_id,
            local_currency_amount_krw=body.local_currency_amount_krw,
            local_currency_charge_krw=calculation.get("local_currency_charge_krw"),
            created_at=now,
        )
        self.db.add(binding)
        invalidated = self._supersede_targets(plan)
        plan.current_benefit_revision = revision
        plan.updated_at = now

        result = (200, {
            "view": plan_view(plan, quote, binding, now),
            "benefit_binding": binding_view(binding),
            "invalidated_approval_target_ids": invalidated,
            "next_action": NextAction.BUILD_APPROVAL_TARGET,
        })
        self._remember(key, "B-05", request_hash, *result)
        return self._commit_or_replay(key, "B-05", request_hash, result)

    def _calculate_benefit(self, plan: OrderPlan, quote: Quote, body: BindBenefitBody, user_id: str) -> dict:
        """B → C-05. B는 할인 규칙을 계산하지 않고, C 결과가 이 견적과 맞는지만 확인한다.

        분할결제(로컬 확장)면 C-05를 두 번 부른다: 지역화폐 수단(몫 = 지역화폐 금액)과 나머지 카드(몫 = 나머지).
        두 결과를 합쳐 총 할인·총 결제액으로 돌려주고, 지역화폐로 실제 차감할 금액(local_currency_charge_krw)도 담는다.
        """
        total = quote.pre_financial_amount_krw
        local_amount = body.local_currency_amount_krw
        if local_amount is not None and local_amount >= total:
            raise self._state_error(plan, 409, "SPLIT_AMOUNT_INVALID",
                                    "지역화폐로 낼 금액은 주문 금액보다 작아야 합니다.")
        main = self._calculate_one(plan, quote, body, user_id, body.payment_method_id,
                                   None if local_amount is None else total - local_amount)
        if local_amount is None:
            return main

        local = self._calculate_one(plan, quote, body, user_id, body.local_currency_payment_method_id, local_amount)
        return {
            **main,
            "instant_discount_krw": main["instant_discount_krw"] + local["instant_discount_krw"],
            "charge_krw": main["charge_krw"] + local["charge_krw"],
            "applied_benefits": [*(local.get("applied_benefits") or []), *(main.get("applied_benefits") or [])],
            "local_currency_charge_krw": local["charge_krw"],
        }

    def _calculate_one(self, plan: OrderPlan, quote: Quote, body: BindBenefitBody, user_id: str,
                       payment_method_id: str, share_krw: int | None) -> dict:
        request = {
            "request_id": plan.request_id,
            "user_id": user_id,
            "restaurant_id": quote.restaurant_id,
            "menu_subtotal_krw": quote.menu_subtotal_krw,
            "delivery_fee_krw": quote.delivery_fee_krw,
            "payment_method_id": payment_method_id,
            "benefit_rule_version": body.benefit_rule_version,
        }
        if share_krw is not None:
            request["share_krw"] = share_krw
        try:
            result = self.benefit.calculate(request)
        except ports.DependencyUnavailable as error:
            raise OrderingError(503, "BENEFIT_UNAVAILABLE", "혜택 계산(C)을 확인하지 못했습니다.",
                                next_action=NextAction.CHECK_DATA, retryable=True,
                                current_order_version=plan.order_version) from error
        except ports.DependencyRejected as error:
            raise self._state_error(plan, 409, "BENEFIT_REJECTED",
                                    f"혜택 계산이 거절됐습니다 ({error.code}).") from error

        if not result.get("eligible", False):
            raise self._state_error(plan, 409, "PAYMENT_METHOD_NOT_ELIGIBLE",
                                    "이 결제수단으로는 이 주문을 결제할 수 없습니다.")

        base = quote.pre_financial_amount_krw if share_krw is None else share_krw
        discount = result.get("instant_discount_krw")
        charge = result.get("charge_krw")
        matches = (
            result.get("payment_method_id") == payment_method_id
            and result.get("benefit_rule_version") == body.benefit_rule_version
            and isinstance(discount, int) and isinstance(charge, int)
            and 0 <= discount <= base
            and charge == base - discount
        )
        if not matches:
            raise self._state_error(plan, 409, "STALE_CALCULATION", "현재 견적과 다른 혜택 결과입니다.")
        return result

    # ================================================================ B-06
    def create_approval_target(self, order_plan_id: str, body: CreateApprovalTargetBody,
                               idempotency_key: str | None, header_request_id: str | None = None) -> Result:
        plan = self._load_plan(order_plan_id, header_request_id, lock=True)
        key = f"{plan.request_id}:b06:{body.base_order_version}:{body.expected_benefit_revision}"
        self._require_key(idempotency_key, key)
        request_hash = body_hash("B-06", order_plan_id, body.model_dump(mode="json"))
        replay = self._replay(key, "B-06", request_hash)
        if replay is not None:
            return replay

        quote = self._check_current(plan, body.base_order_version, body.quote_id)
        binding = self._binding(plan)
        if binding is None or binding.benefit_revision != body.expected_benefit_revision:
            raise self._state_error(plan, 409, "BENEFIT_RESULT_MISMATCH", "현재 채택한 혜택 결과와 일치하지 않습니다.")

        unverified = []
        payment = (plan.constraints or {}).get("payment_requirements") or {}
        if payment.get("local_currency_only") and quote.local_currency_merchant is None:
            unverified.append(UNVERIFIED_LOCAL_CURRENCY)  # D가 QUOTE_DATA_UNVERIFIED로 막는다

        now = self.clock()
        self._supersede_targets(plan)
        target = ApprovalTarget(
            approval_target_id=new_id("target_"),
            order_plan_id=plan.order_plan_id,
            request_id=plan.request_id,
            order_version=plan.order_version,
            constraints_version=plan.constraints_version,
            benefit_revision=binding.benefit_revision,
            quote_id=quote.quote_id,
            status=TargetStatus.PREPARED,
            # D-02가 금액을 다시 계산할 수 있게 견적 상세를 전부 담는다
            quote={
                "quote_id": quote.quote_id,
                "restaurant_id": quote.restaurant_id,
                "items": quote.items,
                "menu_subtotal_krw": quote.menu_subtotal_krw,
                "delivery_fee_krw": quote.delivery_fee_krw,
                "payment_method_id": binding.payment_method_id,
                "instant_discount_krw": binding.instant_discount_krw,
                "charge_krw": binding.charge_krw,
                "catalog_version": quote.catalog_version,
                "benefit_rule_version": binding.benefit_rule_version,
                "expires_at": iso(quote.expires_at),
                "unverified_conditions": unverified,
                **split_fields(binding),  # 분할결제(로컬 확장)일 때만 지역화폐 몫이 들어간다
            },
            created_at=now,
        )
        self.db.add(target)
        plan.current_approval_target_id = target.approval_target_id
        plan.updated_at = now

        result = (201, {"approval_target": target_view(target), "next_action": NextAction.REQUEST_D_APPROVAL})
        self._remember(key, "B-06", request_hash, *result)
        return self._commit_or_replay(key, "B-06", request_hash, result)

    # ================================================================ B-07
    def check_execution(self, order_plan_id: str, body: ExecutionCheckBody,
                        header_request_id: str | None = None) -> Result:
        plan = self._load_plan(order_plan_id, header_request_id)
        if body.base_order_version != plan.order_version:
            raise self._state_error(plan, 409, "VERSION_CONFLICT", "현재 주문 버전을 다시 조회해 주세요.")
        if body.quote_id != plan.current_quote_id:
            raise self._state_error(plan, 409, "QUOTE_NOT_CURRENT", "현재 견적이 아닙니다.")
        target = self.db.get(ApprovalTarget, body.approval_target_id)
        if target is None or target.order_plan_id != plan.order_plan_id:
            raise self._state_error(plan, 404, "APPROVAL_TARGET_NOT_FOUND", "승인 대상을 찾을 수 없습니다.")

        now = self.clock()
        reasons = []
        if plan.status == PlanStatus.PAID:
            reasons.append("ORDER_ALREADY_PAID")
        if now >= self._quote(plan).expires_at:
            reasons.append("QUOTE_EXPIRED")
        if target.status != TargetStatus.PREPARED or target.approval_target_id != plan.current_approval_target_id:
            reasons.append("APPROVAL_TARGET_NOT_CURRENT")

        approval = self._read_approval(plan, body.approval_id)
        if approval is None:
            reasons.append("APPROVAL_NOT_FOUND")
        else:
            reasons.extend(self._approval_reasons(approval, plan, target, body, now))

        ready = not reasons
        execution_input = None
        if ready:
            execution_input = {
                "request_id": plan.request_id,
                "execution_id": body.execution_id,
                "approval_id": body.approval_id,
                "quote_id": target.quote_id,
                "proposal_hash": approval["proposal_hash"],
                "payment_method_id": target.quote["payment_method_id"],
                "charge_krw": target.quote["charge_krw"],
            }
            if target.quote.get("local_currency_payment_method_id"):
                # 분할결제(로컬 확장): E가 지역화폐분과 카드분을 나눠 결제할 수 있게 넘긴다
                execution_input["local_currency_payment_method_id"] = target.quote["local_currency_payment_method_id"]
                execution_input["local_currency_charge_krw"] = target.quote["local_currency_charge_krw"]

        check = ExecutionCheck(
            execution_check_id=new_id("check_"),
            order_plan_id=plan.order_plan_id,
            approval_target_id=target.approval_target_id,
            approval_id=body.approval_id,
            execution_id=body.execution_id,
            stage=body.stage,
            ready=ready,
            reasons=reasons,
            execution_input=execution_input,
            checked_at=now,
        )
        self.db.add(check)
        self.db.commit()

        return 200, {
            "execution_check_id": check.execution_check_id,
            "stage": body.stage,
            "ready": ready,
            "checked_at": iso(now),
            "reasons": reasons,
            "execution_input": execution_input,
            "next_action": self._next_after_check(reasons),
        }

    def _read_approval(self, plan: OrderPlan, approval_id: str) -> dict | None:
        """B → D-04 (읽기 전용)."""
        try:
            return self.approvals.get_approval(approval_id)
        except ports.DependencyUnavailable as error:
            raise OrderingError(503, "APPROVAL_UNAVAILABLE", "D 승인 상태를 확인하지 못했습니다.",
                                next_action=NextAction.CHECK_DATA, retryable=True) from error
        except ports.DependencyRejected as error:
            if error.status == 404:
                return None
            raise OrderingError(503, "APPROVAL_UNAVAILABLE", "D 승인 상태를 확인하지 못했습니다.",
                                next_action=NextAction.CHECK_DATA, retryable=True) from error

    def _approval_reasons(self, approval: dict, plan: OrderPlan, target: ApprovalTarget,
                          body: ExecutionCheckBody, now: datetime) -> list[str]:
        reasons = []
        status = approval.get("status")
        if status == "CONSUMED":
            reasons.append("APPROVAL_ALREADY_USED")
        elif status != "APPROVED":
            reasons.append("APPROVAL_NOT_APPROVED")

        same_target = (
            approval.get("request_id") in (None, plan.request_id)
            and approval.get("approval_target_id") == target.approval_target_id
            and approval.get("order_version") == target.order_version
            and approval.get("quote_id") == target.quote_id
        )
        if not same_target:
            reasons.append("APPROVAL_TARGET_MISMATCH")
        if approval.get("execution_id") != body.execution_id:
            reasons.append("EXECUTION_ID_MISMATCH")

        expires_at = approval.get("expires_at")
        if expires_at:
            if to_kst(datetime.fromisoformat(expires_at)) <= now:
                reasons.append("APPROVAL_EXPIRED")
        if not approval.get("proposal_hash"):
            # D-04에 승인 원본 해시가 있어야 E-01 입력을 만들 수 있다 (명세 확정 필요, README)
            reasons.append("APPROVAL_HASH_UNAVAILABLE")
        return reasons

    @staticmethod
    def _next_after_check(reasons: list[str]) -> str:
        if not reasons or "ORDER_ALREADY_PAID" in reasons:
            return NextAction.NONE
        if "QUOTE_EXPIRED" in reasons:
            return NextAction.REVALIDATE
        if "APPROVAL_HASH_UNAVAILABLE" in reasons and len(reasons) == 1:
            return NextAction.CHECK_DATA
        return NextAction.REQUEST_D_APPROVAL

    # ================================================================ B-08
    def record_payment_event(self, order_plan_id: str, body: PaymentEventBody, idempotency_key: str | None,
                             header_request_id: str | None = None) -> Result:
        self._require_key(idempotency_key, body.event_id)
        plan = self._load_plan(order_plan_id, header_request_id, lock=True)
        key = body.event_id
        request_hash = body_hash("B-08", order_plan_id, body.model_dump(mode="json"))
        replay = self._replay(key, "B-08", request_hash, conflict_code="EVENT_CONFLICT")
        if replay is not None:
            return replay

        target = self.db.get(ApprovalTarget, body.approval_target_id)
        if target is None or target.order_plan_id != plan.order_plan_id:
            raise self._state_error(plan, 404, "APPROVAL_TARGET_NOT_FOUND", "승인 대상을 찾을 수 없습니다.")

        is_current = (
            target.approval_target_id == plan.current_approval_target_id
            and body.quote_id == target.quote_id
            and body.quote_id == plan.current_quote_id
        )
        now = self.clock()
        if not is_current:
            application = ApplicationStatus.RECORDED_STALE
        elif plan.payment_status in FINAL_PAYMENT_STATUSES and body.status != plan.payment_status:
            application = ApplicationStatus.IGNORED_AFTER_FINAL  # 결제 결과는 한 번 확정되면 바꾸지 않는다
        else:
            application = ApplicationStatus.APPLIED
            plan.payment_status = body.status
            plan.updated_at = now
            if body.status == PaymentStatus.APPROVED:
                plan.status = PlanStatus.PAID
                target.status = TargetStatus.PAID

        self.db.add(PaymentEvent(
            event_id=body.event_id,
            order_plan_id=plan.order_plan_id,
            approval_target_id=target.approval_target_id,
            quote_id=body.quote_id,
            execution_id=body.execution_id,
            payment_id=body.payment_id,
            status=body.status,
            observed_at=body.observed_at,
            reason_code=body.reason_code,
            application_status=application,
            applied_to_current_quote=is_current,
            request_hash=request_hash,
            received_at=now,
        ))

        waiting = application == ApplicationStatus.APPLIED and body.status in (
            PaymentStatus.PENDING, PaymentStatus.UNKNOWN,
        )
        result = (200, {
            "event_id": body.event_id,
            "payment_id": body.payment_id,
            "linked_quote_id": body.quote_id,
            "application_status": application,
            "payment_status": body.status,
            "applied_to_current_quote": is_current,
            "next_action": NextAction.WAIT_PAYMENT_RESULT if waiting else NextAction.NONE,
        })
        self._remember(key, "B-08", request_hash, *result)
        return self._commit_or_replay(key, "B-08", request_hash, result, conflict_code="EVENT_CONFLICT")
