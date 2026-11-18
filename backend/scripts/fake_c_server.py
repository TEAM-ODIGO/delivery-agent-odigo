"""명세서 예시대로만 답하는 가짜 C 서버 (C-05·C-02).

C 담당자의 실제 서버가 아직 없을 때 check_with_c.py가 잘 도는지 보는 용도이고,
C 담당자에게는 '이 모양으로 답하면 D가 받아들인다'는 기준이 된다.

실행: uv run uvicorn scripts.fake_c_server:app --port 8001
"""

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

app = FastAPI(title="가짜 C (명세 예시)")

RULE_VERSION = "BEN-V1"
METHODS = [
    {
        "payment_method_id": "CARD001",
        "product_id": "PRD-CARD-A",
        "type": "CARD",
        "name": "가상카드 A",
        "monthly_performance_krw": 350000,
        "monthly_benefit_remaining_krw": 8000,
        "status": "ACTIVE",
    },
    {
        "payment_method_id": "LOCAL001",
        "product_id": "PRD-LOCAL-A",
        "type": "LOCAL_CURRENCY",
        "name": "가상 지역화폐",
        "balance_krw": 120000,
        "monthly_benefit_remaining_krw": 15000,
        "status": "ACTIVE",
    },
]
RULES = {"CARD001": "RULE-CARD-A-DELIVERY", "LOCAL001": "RULE-LOCAL-A-DISCOUNT"}
# 카드 A: 메뉴 15,000원 이상이면 2,000원 / 지역화폐: 메뉴 금액의 5%
DISCOUNT = {
    "CARD001": lambda total: 2000 if total >= 15000 else 0,
    "LOCAL001": lambda total: total // 20,
}


def error(status: int, code: str, message: str, request_id: str | None):
    body = {"error": {"code": code, "message": message, "details": {}}, "request_id": request_id}
    return JSONResponse(status_code=status, content=body)


@app.get("/benefit/users/{user_id}/payment-methods")
def list_payment_methods(user_id: str):
    return {"count": len(METHODS), "results": METHODS, "mock": True}


@app.post("/benefit/calculations")
def calculate(body: dict, x_request_id: str | None = Header(default=None)):
    if body.get("benefit_rule_version") != RULE_VERSION:
        return error(404, "RULE_NOT_FOUND", "혜택 규칙 버전을 찾을 수 없습니다.", x_request_id)
    method = body.get("payment_method_id")
    if method not in DISCOUNT:
        return error(404, "PAYMENT_METHOD_NOT_FOUND", "결제수단을 찾을 수 없습니다.", x_request_id)
    total = body["menu_subtotal_krw"] + body["delivery_fee_krw"]
    discount = DISCOUNT[method](body["menu_subtotal_krw"])
    return {
        "payment_method_id": method,
        "eligible": discount > 0,
        "instant_discount_krw": discount,
        "charge_krw": total - discount,
        "benefit_rule_version": RULE_VERSION,
        "applied_benefits": (
            [{"rule_id": RULES[method], "type": "INSTANT_DISCOUNT", "amount_krw": discount}]
            if discount
            else []
        ),
        "reason_codes": [] if discount else ["MIN_ORDER_NOT_MET"],
        "mock": True,
    }
