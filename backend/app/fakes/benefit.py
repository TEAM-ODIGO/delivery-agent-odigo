"""가짜 C (혜택): C-01 결제수단 추천, C-02 보유 결제수단, C-05 혜택 재계산.

D도 같은 C-05·C-02로 할인을 다시 확인하므로(D → C-05·C-02),
O가 쓰는 가짜 C와 D가 쓰는 C가 같은 인스턴스여야 금액이 맞는다 (wiring.py에서 연결).

규칙 (BEN-V1, 시드 데이터 대역)
- CARD001 (상품 CARD-A): 메뉴 합계 15,000원 이상이면 즉시할인 2,000원
- CARD002 (상품 CARD-B): 전월 실적 미달 → 추천 제외(PERFORMANCE_NOT_MET), 계산하면 eligible=false
- LOCAL001 (지역화폐): 메뉴 합계의 5%(10원 미만 버림), 잔액 50,000원, 지역화폐 가맹점에서만
"""

import uuid
from datetime import timedelta
from typing import Any

from app.core import clock
from app.core.errors import AppError
from app.fakes import catalog

RULE_VERSION = "BEN-V1"

METHODS = [
    {
        "payment_method_id": "CARD001",
        "type": "CARD",
        "product_id": "CARD-A",
        "display_name": "신한 배달카드",
    },
    {
        "payment_method_id": "CARD002",
        "type": "CARD",
        "product_id": "CARD-B",
        "display_name": "일반 체크카드",
    },
    {
        "payment_method_id": "LOCAL001",
        "type": "LOCAL_CURRENCY",
        "product_id": "LOCAL-A",
        "display_name": "중구 지역화폐",
        "balance_krw": 50000,
    },
]
_BY_ID = {m["payment_method_id"]: m for m in METHODS}


class FakeBenefit:
    def __init__(self, now=clock.now):
        self.now = now
        self.recommendations: dict[str, dict] = {}

    # --- 계산 규칙 (C-01·C-05가 같이 쓴다) ------------------------------------------
    @staticmethod
    def _discount(method_id: str, menu_subtotal: int) -> tuple[bool, int, list[dict], list[str]]:
        """(eligible, 할인액, applied_benefits, reasons)"""
        if method_id == "CARD001" and menu_subtotal >= 15000:
            return (
                True,
                2000,
                [
                    {
                        "rule_id": "RULE-CARD-A-DELIVERY",
                        "type": "INSTANT_DISCOUNT",
                        "amount_krw": 2000,
                    }
                ],
                ["배달앱 결제 즉시할인 2,000원"],
            )
        if method_id == "LOCAL001":
            amount = menu_subtotal * 5 // 100 // 10 * 10
            return (
                True,
                amount,
                [
                    {
                        "rule_id": "RULE-LOCAL-A-DISCOUNT",
                        "type": "INSTANT_DISCOUNT",
                        "amount_krw": amount,
                    }
                ],
                ["지역화폐 즉시할인 5% (가맹점 여부는 추정)"],
            )
        return False, 0, [], []

    # --- C-02 ---------------------------------------------------------------------
    def list_methods(self, user_id: str) -> dict[str, Any]:
        return {"user_id": user_id, "results": [dict(m) for m in METHODS]}

    # --- C-05 ---------------------------------------------------------------------
    def calculate(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("benefit_rule_version") != RULE_VERSION:
            raise AppError(404, "RULE_NOT_FOUND", "혜택 규칙 버전을 찾을 수 없습니다.")
        method_id = body["payment_method_id"]
        if method_id not in _BY_ID:
            raise AppError(404, "PAYMENT_METHOD_NOT_FOUND", "결제수단이 없습니다.")
        eligible, discount, _, _ = self._discount(method_id, body["menu_subtotal_krw"])
        if method_id == "CARD002":
            reasons = ["PERFORMANCE_NOT_MET"]
        else:
            reasons = [] if eligible else ["NO_RULE_MATCHED"]
        return {
            "payment_method_id": method_id,
            "benefit_rule_version": RULE_VERSION,
            "eligible": eligible,
            "instant_discount_krw": discount,
            "charge_krw": body["menu_subtotal_krw"] + body["delivery_fee_krw"] - discount,
            "reason_codes": reasons,
        }

    # --- C-01 ---------------------------------------------------------------------
    def recommend(
        self, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        rest = catalog.restaurant(body["restaurant_id"])
        subtotal, fee = body["menu_subtotal_krw"], body["delivery_fee_krw"]
        local_only = (body.get("payment_requirements") or {}).get("local_currency_only", False)
        wanted = body.get("candidate_payment_method_ids") or [
            m["payment_method_id"] for m in METHODS
        ]

        options, excluded = [], []
        for method_id in wanted:
            m = _BY_ID.get(method_id)
            if m is None:
                excluded.append({"payment_method_id": method_id, "reason_codes": ["NOT_OWNED"]})
                continue
            is_local = m["type"] == "LOCAL_CURRENCY"
            if local_only and not is_local:
                excluded.append(
                    {"payment_method_id": method_id, "reason_codes": ["LOCAL_CURRENCY_REQUIRED"]}
                )
                continue
            if is_local and (rest is None or rest.local_currency_merchant is not True):
                code = (
                    "LOCAL_CURRENCY_UNVERIFIED"
                    if rest is None or rest.local_currency_merchant is None
                    else "LOCAL_CURRENCY_NOT_ACCEPTED"
                )
                excluded.append({"payment_method_id": method_id, "reason_codes": [code]})
                continue
            if method_id == "CARD002":
                excluded.append(
                    {"payment_method_id": method_id, "reason_codes": ["PERFORMANCE_NOT_MET"]}
                )
                continue
            _, discount, applied, reasons = self._discount(method_id, subtotal)
            charge = subtotal + fee - discount
            if is_local and m.get("balance_krw", 0) < charge:
                excluded.append(
                    {"payment_method_id": method_id, "reason_codes": ["INSUFFICIENT_BALANCE"]}
                )
                continue
            options.append(
                {
                    "payment_method_id": method_id,
                    "instant_discount_krw": discount,
                    "charge_krw": charge,
                    "applied_benefits": applied,
                    "reasons": reasons or ["적용 가능한 즉시할인 없음"],
                }
            )

        options.sort(key=lambda o: o["charge_krw"])
        for rank, o in enumerate(options, start=1):
            o["rank"] = rank
        rec = {
            "recommendation_id": str(uuid.uuid4()),
            "request_id": body["request_id"],
            "status": "ACTIVE",
            "benefit_rule_version": RULE_VERSION,
            "expires_at": (self.now() + timedelta(minutes=10)).isoformat(),
            "options": [{"rank": o.pop("rank"), **o} for o in options],
            "excluded": excluded,
            "mock": True,
        }
        self.recommendations[rec["recommendation_id"]] = rec
        return rec
