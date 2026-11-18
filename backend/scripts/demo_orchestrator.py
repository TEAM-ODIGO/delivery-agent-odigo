"""O 시연: 화면이 하듯 HTTP로 O-01·O-02·O-03만 불러 주문 시나리오를 끝까지 돌린다.

서버를 켠 상태에서 다른 터미널 (backend 폴더 안에서):
    uv run --env-file .env python scripts/demo_orchestrator.py            # 시나리오 전부
    uv run --env-file .env python scripts/demo_orchestrator.py o1 o6      # 골라서
    uv run --env-file .env python scripts/demo_orchestrator.py --list

- 사용자 토큰: DEV_USER_TOKENS의 첫 토큰 (기본 dev-token-U001)
- ADMIN_ACCESS_TOKEN이 있으면 각 주문의 O-04 기록(호출 순서)도 보여 준다.
- 별도 e-payment와 함께 전부 실행하려면 백엔드 O_DEMO_E_OVERRIDE=1,
  e-payment E_ALLOW_MOCK_OVERRIDE=1로 각각 재시작한다. 이때 O가 결제 태그를
  E-01의 X-Mock-Outcome 헤더로 전달한다 (시연 전용).
- 별도 scripts/fake_e_server.py는 요청별 헤더를 지원하지 않는다.
"""

import os
import sys
import uuid

import httpx

BASE = os.getenv("O_URL", "http://localhost:8000")
TOKEN = os.getenv("DEV_USER_TOKENS", "dev-token-U001:U001").split(",")[0].split(":")[0]
ADMIN = os.getenv("ADMIN_ACCESS_TOKEN", "")
ADDRESS = {"region_code": "11140", "lat": 37.566, "lng": 126.982}
DELEGATION = {
    "allowed_actions": ["SEARCH", "BUILD_ORDER", "REQUEST_PAYMENT"],
    "allowed_payment_method_ids": ["CARD001", "LOCAL001"],
    "max_charge_krw": 20000,
}
OK = "둘이서 2만원 이하로 안 매운 치킨 빨리 시켜줘"

http = httpx.Client(base_url=BASE, timeout=10)
H = {"Authorization": f"Bearer {TOKEN}"}


def start(message, delegation=DELEGATION):
    r = http.post(
        "/orchestrator/runs",
        headers={**H, "Idempotency-Key": uuid.uuid4().hex},
        json={"message": message, "delivery_address": ADDRESS, "delegation": delegation},
    )
    show("O-01", r)
    return r.json()


def reply(v, **body):
    rid = v["request_id"]
    r = http.post(
        f"/orchestrator/runs/{rid}/reply",
        headers={**H, "X-Request-ID": rid, "Idempotency-Key": uuid.uuid4().hex},
        json={**body, "expected_state_version": v["state_version"]},
    )
    show(f"O-02 {body['type']}", r)
    return r.json() if r.status_code < 400 else {**v, "_error": r.json(), "_status": r.status_code}


def get(v):
    r = http.get(f"/orchestrator/runs/{v['request_id']}", headers=H)
    show("O-03", r)
    return r.json()


def show(label, r):
    b = r.json()
    if r.status_code >= 400:
        print(f"   {label:<28} ⛔ {r.status_code} {b['error']['code']} {b['error']['details']}")
        return
    extra = ""
    if b.get("prompt"):
        p = b["prompt"]
        extra = {
            "CLARIFICATION": lambda: "질문 " + ", ".join(q["slot"] for q in p["questions"]),
            "SELECT_CANDIDATE": lambda: (
                "후보 "
                + " / ".join(
                    f"[{c['index']}] {c['restaurant_name']} {c['total_krw']:,}원"
                    for c in p["candidates"]
                )
            ),
            "SELECT_PAYMENT_METHOD": lambda: (
                "결제수단 "
                + " / ".join(
                    f"{o['payment_method_id']} {o['charge_krw']:,}원" for o in p["options"]
                )
            ),
            "APPROVAL": lambda: p["summary"],
        }[p["type"]]()
    if b.get("notice"):
        extra += f"  ⚠ {b['notice']['code']}"
    if b.get("blocked"):
        extra += f"  ⛔ {b['blocked']['reason_codes']}"
    if b.get("error"):
        extra += f"  ✖ {b['error'].get('code')}"
    if b.get("result"):
        extra += f"  💳 {b['result']['payment_status']} {b['result'].get('charge_krw')}"
    print(f"   {label:<28} → {b['state']:<26} v{b['state_version']}  {extra}")


def until(v, state):
    while v["state"] != state and "_error" not in v:
        p = v.get("prompt") or {}
        if v["state"] == "NEED_CLARIFICATION":
            v = reply(
                v,
                type="CLARIFICATION",
                answers=[{"slot": "people", "value": 2}, {"slot": "budget_krw", "value": "20000"}],
            )
        elif v["state"] == "CANDIDATES_READY":
            v = reply(v, type="SELECT_CANDIDATE", candidate_index=p["candidates"][0]["index"])
        elif v["state"] == "PAYMENT_METHOD_SELECTION":
            v = reply(
                v,
                type="SELECT_PAYMENT_METHOD",
                payment_method_id=p["options"][0]["payment_method_id"],
            )
        elif v["state"] == "AWAITING_APPROVAL":
            v = reply(v, type="APPROVE")
        else:
            break
    return v


# --- 시나리오: (설명, 실행 함수, 기대 최종 상태) -------------------------------------------
def o1():
    return until(start(OK), "COMPLETED")


def o2():
    return until(start("치킨 시켜줘"), "COMPLETED")


def o3():
    return until(start(OK + " #품절"), "COMPLETED")


def o4():
    v = start("둘이서 15,000원 이하로 안 매운 치킨", {"allowed_payment_method_ids": ["CARD001"]})
    return until(v, "COMPLETED")


def o5():
    return until(start(OK + " #결제미확정"), "COMPLETED")


def o5b():
    v = until(start(OK + " #결제미확정"), "COMPLETED")
    return get(v)


def o6():
    v = until(start(OK), "AWAITING_APPROVAL")
    return reply(v, type="CANCEL")


def o7():
    v = until(start(OK + " #응답유실"), "COMPLETED")
    return get(v)


def o8():
    return until(start(OK + " #결제거절"), "COMPLETED")


def o11():
    """분할결제(로컬 확장): 지역화폐 8,000원을 먼저 정하고 나머지는 카드로"""
    v = until(start(OK), "PAYMENT_METHOD_SELECTION")
    v = reply(
        v,
        type="SELECT_PAYMENT_METHOD",
        payment_method_id="CARD001",
        local_currency_amount_krw=8000,
    )
    if v.get("prompt", {}).get("split"):
        print("   분할:", v["prompt"]["split"])
    return reply(v, type="APPROVE")


def o12():
    """맛 취향(A 확장): 메뉴 이름 없이 '매운 음식'만 말해도 후보가 나오고 결제까지 간다"""
    # 후보가 27,900원대라 맡긴 한도(기본 2만원)를 넘으면 D가 막는다 → 한도를 4만원으로
    return until(start("매운 음식 둘이서 3만원 이하로", {"max_charge_krw": 40000}), "COMPLETED")


def o9():
    v = start(OK)
    stale = dict(v, state_version=v["state_version"] - 1)
    return reply(stale, type="SELECT_CANDIDATE", candidate_index=0)


def o10():
    v = until(start(OK), "COMPLETED")
    return reply(v, type="CANCEL")


SCENARIOS = {
    "o1": ("정상 주문 → 결제 완료", o1, "COMPLETED"),
    "o2": ("재질문(인원·예산) → 선택·입력 → 결제 완료", o2, "COMPLETED"),
    "o3": ("#품절: 남은 후보로 다시 선택 → 결제 완료", o3, "COMPLETED"),
    "o4": ("예산 초과 견적 → D-02 차단", o4, "BLOCKED"),
    "o5": ("#결제미확정: 결제 응답 UNKNOWN", o5, "PAYMENT_UNKNOWN"),
    "o5b": ("#결제미확정 → O-03 조회 때 E-03으로 확정", o5b, "COMPLETED"),
    "o6": ("승인 대기 중 취소 → D-05 철회", o6, "CANCELLED"),
    "o7": ("#응답유실 → 재결제 없이 E-03으로 확정", o7, "COMPLETED"),
    "o8": ("#결제거절 → FAILED", o8, "FAILED"),
    "o9": ("오래된 화면에서 누름 → 409", o9, 409),
    "o10": ("결제 후 취소 → 409 (환불로 안내)", o10, 409),
    "o12": ("맛 취향: '매운 음식' → 후보 → 결제 완료 (새 A)", o12, "COMPLETED"),
    "o11": ("분할결제: 지역화폐 8,000원 + 나머지 카드 (로컬 확장)", o11, "COMPLETED"),
}


def timeline(rid):
    if not ADMIN:
        return
    r = http.get(f"/orchestrator/runs/{rid}/events", headers={"Authorization": f"Bearer {ADMIN}"})
    if r.status_code != 200:
        return
    calls = [
        e["detail"].get("api")
        for e in r.json()["events"]
        if e["event_type"] in ("SERVICE_CALL",) and e["detail"].get("api")
    ]
    print("   호출 순서:", " → ".join(calls))


def main(argv):
    if "--list" in argv:
        for k, (desc, _, want) in SCENARIOS.items():
            print(f"{k:<4} {desc}  (기대: {want})")
        return 0
    picked = [a for a in argv if a in SCENARIOS] or list(SCENARIOS)
    results = []
    for key in picked:
        desc, fn, want = SCENARIOS[key]
        print(f"\n▶ {key} {desc}")
        v = fn()
        got = v.get("_status") if "_status" in v else v["state"]
        ok = got == want
        results.append((key, desc, want, got, ok))
        timeline(v["request_id"])
    print("\n요약")
    for key, desc, want, got, ok in results:
        print(f"  {'✅' if ok else '❌'} {key:<4} 기대 {str(want):<16} 결과 {str(got):<16} {desc}")
    passed = sum(r[-1] for r in results)
    print(f"\n{passed}/{len(results)} 통과")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
