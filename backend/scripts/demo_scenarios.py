"""시나리오 실행기 — 켜져 있는 서버에 실제 HTTP 요청을 보내 시나리오를 재현한다.

MVP 2 승인 대시보드의 '시나리오 실행' 버튼이 할 일을 터미널에서 미리 해 보는 도구다.
각 단계의 결과와, 그 주문(request_id)에 대해 D가 남긴 감사 로그를 시간순으로 보여 준다.

실행 (서버를 켠 터미널과 다른 터미널에서, backend 폴더 안에서):
    uv run --env-file .env python scripts/demo_scenarios.py          # 전체
    uv run --env-file .env python scripts/demo_scenarios.py s2 s5    # 골라서
    uv run --env-file .env python scripts/demo_scenarios.py --list   # 목록만

S10(감사 로그 장애)과 만료(10분 대기)는 서버를 일부러 고장 내거나 기다려야 해서
여기서는 빼고 pytest로 확인한다:  uv run pytest -v -k "s10 or expire"
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ 를 import 경로에 추가

from app.core.clock import KST  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.modules.delegation import audit  # noqa: E402

BASE = os.getenv("DEMO_BASE_URL", "http://127.0.0.1:8000") + "/delegation"
KEY = os.getenv("INTERNAL_SERVICE_TOKEN") or os.getenv("DELEGATION_SERVICE_KEY", "")
RUN = datetime.now(KST).strftime("%m%d%H%M%S")  # 실행할 때마다 새 request_id

client = httpx.Client(timeout=5, headers={"Authorization": f"Bearer {KEY}"})


# ─── 출력 도우미 ────────────────────────────────────────────────────────────────
def step(label: str, status: int, body: dict, ok: bool | None = None) -> None:
    mark = {True: "✅", False: "⛔", None: "  "}[ok]
    summary = body.get("reason_codes") or body.get("error", {}).get("code") or body.get("status")
    print(f"   {mark} {label:<32} HTTP {status}  {summary or ''}")


def show_audit(request_id: str) -> None:
    """D-09로 감사 로그를 읽는다 (MVP 2에서 O-04가 승인 대시보드에 보여 줄 내용과 같다)."""
    r = client.get(f"{BASE}/audit-logs", params={"request_id": request_id, "page_size": 100})
    print(f"   ── D 감사 로그 (D-09, {r.json()['count']}건) ──")
    for row in r.json()["results"]:
        t = row["created_at"][11:19]
        reasons = ",".join(row["reason_codes"])
        print(f"   {t}  {row['event_type']:<20} {row['result']:<9} {reasons}")


# ─── 요청 만들기 ────────────────────────────────────────────────────────────────
def quote(**over) -> dict:
    """(30,000 + 옵션 2,000) + 배달비 3,000 - 카드할인 2,000 = 33,000원"""
    q = {
        "quote_id": "QT001",
        "restaurant_id": "STORE101",
        "items": [
            {
                "menu_id": "MENU001",
                "name": "후라이드치킨",
                "description": "바삭한 국내산 닭",
                "quantity": 1,
                "unit_price_krw": 30000,
                "options": [{"option_id": "OPT001", "price_krw": 2000}],
            }
        ],
        "menu_subtotal_krw": 32000,
        "delivery_fee_krw": 3000,
        "payment_method_id": "CARD001",
        "instant_discount_krw": 2000,
        "charge_krw": 33000,
        "catalog_version": "CAT-V1",
        "benefit_rule_version": "BEN-V1",
        "expires_at": (datetime.now(KST) + timedelta(minutes=30)).isoformat(),
        "unverified_conditions": [],
    }
    q.update(over)
    return q


def register(rid: str, budget: int = 40000, actions=None) -> dict:
    body = {
        "request_id": rid,
        "user_id": "U001",
        "original_message": "3명이 먹을 치킨을 배달비 포함 4만원 이하로 주문해줘",
        "constraints_confirmed": True,
        "constraints": {"budget_krw": budget, "people": 3, "cuisine": "치킨"},
        "delegation": {
            "allowed_actions": actions or ["SEARCH", "BUILD_ORDER", "REQUEST_PAYMENT"],
            "allowed_payment_method_ids": ["CARD001", "LOCAL001"],
            "max_charge_krw": 40000,
        },
    }
    r = client.post(f"{BASE}/policies", json=body)
    step(f"D-01 조건 등록 (예산 {budget:,})", r.status_code, r.json(), r.status_code == 201)
    return r.json()


def validate(rid: str, policy: dict, q: dict | None = None) -> dict:
    body = {
        "request_id": rid,
        "user_id": "U001",
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "approval_target_id": "target_001",
        "order_version": 1,
        "quote": q or quote(),
    }
    r = client.post(f"{BASE}/validations/orders", json=body)
    data = r.json()
    step(
        f"D-02 주문 검증 ({body['quote']['charge_krw']:,}원)",
        r.status_code,
        data,
        data.get("valid"),
    )
    if data.get("details"):
        print(f"        비교값: {data['details']}")
    return data


def confirm(v: dict) -> dict:
    r = client.post(
        f"{BASE}/approvals/{v['approval_id']}/confirm",
        json={"user_id": "U001", "proposal_hash": v["proposal_hash"]},
    )
    step("D-03 사용자 승인", r.status_code, r.json(), r.status_code == 200)
    return r.json()


def pay(v: dict, execution_id: str, label: str = "D-06 결제 직전 검증", **over) -> dict:
    body = {
        "execution_id": execution_id,
        "approval_id": v["approval_id"],
        "quote_id": "QT001",
        "proposal_hash": v["proposal_hash"],
        "charge_krw": 33000,
        "payment_method_id": "CARD001",
    }
    body.update(over)
    r = client.post(
        f"{BASE}/authorizations/check", json=body, headers={"Authorization": f"Bearer {KEY}"}
    )
    data = r.json()
    extra = f"  (요청 {over})" if over else ""
    step(label + extra, r.status_code, data, data.get("allowed"))
    return data


# ─── 시나리오 ───────────────────────────────────────────────────────────────────
def record_result(execution_id: str, status: str = "APPROVED") -> None:
    """E가 결제한 뒤 O가 결과를 D에 기록한다 (D-08, MVP 2)."""
    body = {
        "execution_id": execution_id,
        "payment_id": f"PAY-{execution_id[-6:]}",
        "status": status,
        "charge_krw": 33000,
    }
    r = client.post(f"{BASE}/payments/results", json=body)
    step("D-08 결제 결과 기록 (O → D)", r.status_code, r.json(), r.status_code == 200)


def s1(rid):
    p = register(rid)
    v = validate(rid, p)
    c = confirm(v)
    result = pay(v, c["execution_id"])
    if result.get("allowed"):
        record_result(c["execution_id"])  # E 모의 결제 성공을 가정
    return result


def s2(rid):
    p = register(rid)
    q = quote(
        items=[{"menu_id": "M", "name": "치킨", "quantity": 2, "unit_price_krw": 22000}],
        menu_subtotal_krw=44000,
        charge_krw=45000,
    )
    return validate(rid, p, q)


def s3(rid):
    p = register(rid)
    return validate(rid, p, quote(instant_discount_krw=5000, charge_krw=30000))


def s4(rid):
    p = register(rid)
    v = validate(rid, p)
    print("        (사용자 승인 단계를 건너뛰고 바로 결제 요청)")
    return pay(v, "EXE-guess")


def s5(rid):
    p = register(rid)
    v = validate(rid, p)
    c = confirm(v)
    return pay(v, c["execution_id"], charge_krw=30000)


def s6(rid):
    p = register(rid)
    v = validate(rid, p)
    c = confirm(v)
    register(rid, budget=30000)
    return pay(v, c["execution_id"])


def s7(rid):
    p = register(rid)
    v = validate(rid, p)
    c = confirm(v)
    pay(v, c["execution_id"], "D-06 첫 번째 결제")
    pay(v, c["execution_id"], "D-06 같은 실행 재질문")
    return pay(v, "EXE-second", "D-06 다른 실행으로 재사용")


def s8(rid):
    p = register(rid)
    v = validate(rid, p)
    c = confirm(v)
    return pay(v, c["execution_id"], payment_method_id="LOCAL001")


def s9(rid):
    p = register(rid, actions=["SEARCH", "BUILD_ORDER"])
    v = validate(rid, p)
    c = confirm(v)
    return pay(v, c["execution_id"])


SCENARIOS = {
    "s1": ("✅ 정상 주문", s1, "allowed"),
    "s2": ("⛔ 예산 초과", s2, "BUDGET_EXCEEDED"),
    "s3": ("⛔ 할인 부풀림", s3, "AMOUNT_MISMATCH"),
    "s4": ("⛔ 승인 전 결제", s4, "APPROVAL_NOT_CONFIRMED"),
    "s5": ("⛔ 승인 후 금액 변조", s5, "PAYMENT_AMOUNT_MISMATCH"),
    "s6": ("⛔ 조건 변경 후 옛 승인", s6, "APPROVAL_REVOKED"),
    "s7": ("⛔ 같은 승인 재사용", s7, "ALREADY_EXECUTED"),
    "s8": ("⛔ 승인 후 결제수단 바꿔치기", s8, "PAYMENT_METHOD_MISMATCH"),
    "s9": ("⛔ 결제를 맡기지 않음", s9, "ACTION_NOT_ALLOWED"),
}


def main(argv: list[str]) -> int:
    if "--list" in argv:
        for k, (title, _, expected) in SCENARIOS.items():
            print(f"{k}  {title:<24} 기대: {expected}")
        return 0
    if not KEY:
        print("INTERNAL_SERVICE_TOKEN이 비어 있습니다. --env-file .env 를 붙여 실행하세요.")
        return 1
    try:
        client.get(f"{BASE}/health").raise_for_status()
    except httpx.HTTPError:
        print(
            f"서버에 연결할 수 없습니다: {BASE}\n먼저 서버를 켜세요: "
            "uv run --env-file .env uvicorn app.main:app --reload --port 8000"
        )
        return 1

    chosen = [a.lower() for a in argv if not a.startswith("-")] or list(SCENARIOS)
    summary = []
    for key in chosen:
        title, fn, expected = SCENARIOS[key]
        rid = f"DEMO-{RUN}-{key.upper()}"
        print(f"\n━━ {key.upper()} {title}  (request_id={rid})")
        result = fn(rid)
        show_audit(rid)
        actual = "allowed" if result.get("allowed") else ",".join(result.get("reason_codes", []))
        summary.append((key, title, expected, actual, expected in actual))

    with SessionLocal() as s:
        chain_ok, broken = audit.verify_chain(s)

    print("\n━━ 요약")
    for key, title, expected, actual, ok in summary:
        print(f"   {'✅' if ok else '❌'} {key}  {title:<24} 기대 {expected:<26} 실제 {actual}")
    print(f"\n   감사 로그 해시 사슬: {'정상' if chain_ok else f'끊김 (seq {broken})'}")
    return 0 if all(r[-1] for r in summary) and chain_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
