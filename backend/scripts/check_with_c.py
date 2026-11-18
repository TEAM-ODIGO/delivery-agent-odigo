"""C(혜택)와 D(위임·승인)를 실제로 붙여서 함께 동작하는지 확인한다.

C 담당자와 같이 돌려 보는 용도. D는 이 스크립트 안에서 메모리 DB로 돌고,
C는 둘 중 하나로 붙인다.

  1) C가 서버로 떠 있을 때 (각자 개발 중)
     uv run python scripts/check_with_c.py --c-url http://localhost:8001 --token <서비스 토큰>

  2) C 코드를 이 저장소에 넣었을 때 (모놀리스로 합친 뒤)
     uv run python scripts/check_with_c.py --c-module app.modules.benefit.facade
     → 그 모듈에 두 함수가 있어야 한다:
        calculate(body: dict) -> dict              (C-05)
        list_payment_methods(user_id: str) -> dict (C-02)

  C 없이 이 스크립트를 먼저 확인하려면 명세 예시대로 답하는 가짜 C를 띄운다:
     uv run uvicorn scripts.fake_c_server:app --port 8001

확인하는 것 (명세 C-02·C-05 + D-02 규칙)
  - C-02·C-05 응답 모양이 명세와 같은가 (D가 읽는 필드 이름·타입)
  - C가 계산한 할인을 B가 그대로 넣은 견적 → D 통과
  - 할인을 부풀린 견적 → D 차단 (AMOUNT_MISMATCH)
  - C가 모르는 규칙 버전 → C가 에러로 답하고 D는 확인 불가로 차단 (QUOTE_DATA_UNVERIFIED)
  - 사용자 것이 아닌 결제수단 → D 차단 (QUOTE_DATA_UNVERIFIED)
  - '지역화폐만' 조건: 지역화폐 수단은 통과, 카드는 차단 (C-02의 type으로 판단)
"""

import argparse
import importlib
import sys
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.clock import now as kst_now  # noqa: E402
from app.core.db import init_db, make_engine  # noqa: E402
from app.modules.delegation.benefits import CBenefitPort, http_benefit_port  # noqa: E402
from app.modules.delegation.schemas import OrderValidationRequest, PolicyCreate  # noqa: E402
from app.modules.delegation.service import DelegationService  # noqa: E402

C02_METHOD_KEYS = {"payment_method_id", "type"}
C05_KEYS = {
    "payment_method_id",
    "eligible",
    "benefit_rule_version",
    "instant_discount_krw",
    "charge_krw",
}
results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    results.append((ok, name, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail else ""))
    return ok


def build_port(args) -> CBenefitPort:
    if args.c_url:
        return http_benefit_port(args.c_url, args.token)
    mod = importlib.import_module(args.c_module)
    return CBenefitPort(calculate=mod.calculate, list_methods=mod.list_payment_methods)


class Runner:
    def __init__(self, port: CBenefitPort, args):
        self.port, self.args = port, args
        eng = make_engine("sqlite://")
        init_db(eng)
        self.sessions = sessionmaker(bind=eng, expire_on_commit=False)

    def svc(self, s):
        return DelegationService(s, self.port)

    def validate(self, method_id: str, discount: int, *, rule_version=None, local_only=False):
        """정책 등록(D-01) → 주문 검증(D-02). C는 D 안에서 C-05·C-02로 불린다."""
        a = self.args
        rid = f"CHK-{uuid.uuid4().hex[:8]}"
        total = a.subtotal + a.delivery_fee
        constraints = {"budget_krw": total + 100000, "people": 1}
        if local_only:
            constraints["payment_requirements"] = {"local_currency_only": True}
        with self.sessions() as s:
            svc = self.svc(s)
            policy = svc.register_policy(
                PolicyCreate.model_validate(
                    {
                        "request_id": rid,
                        "user_id": a.user,
                        "original_message": "C·D 연동 확인",
                        "constraints_confirmed": True,
                        "constraints": constraints,
                        "delegation": {
                            "allowed_actions": ["SEARCH", "BUILD_ORDER", "REQUEST_PAYMENT"],
                            "allowed_payment_method_ids": [method_id],
                            "max_charge_krw": total + 100000,
                        },
                    }
                )
            )
            quote = {
                "quote_id": f"QT-{rid}",
                "restaurant_id": a.restaurant,
                "items": [
                    {
                        "menu_id": "MENU-CHECK",
                        "name": "연동 확인 메뉴",
                        "quantity": 1,
                        "unit_price_krw": a.subtotal,
                        "options": [],
                    }
                ],
                "menu_subtotal_krw": a.subtotal,
                "delivery_fee_krw": a.delivery_fee,
                "payment_method_id": method_id,
                "instant_discount_krw": discount,
                "charge_krw": total - discount,
                "catalog_version": "CAT-CHECK",
                "benefit_rule_version": rule_version or a.rule_version,
                "expires_at": (kst_now() + timedelta(minutes=30)).isoformat(),
                "unverified_conditions": [],
            }
            return svc.validate_order(
                OrderValidationRequest.model_validate(
                    {
                        "request_id": rid,
                        "user_id": a.user,
                        "policy_id": policy.policy_id,
                        "policy_version": policy.policy_version,
                        "approval_target_id": f"TG-{rid}",
                        "order_version": 1,
                        "quote": quote,
                    }
                )
            )


def main() -> int:
    p = argparse.ArgumentParser(description="C·D 연동 확인")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--c-url", help="C 서버 주소 (예: http://localhost:8001)")
    src.add_argument("--c-module", help="C 함수가 있는 모듈 (calculate, list_payment_methods)")
    p.add_argument("--token", default="change-me-dev-only", help="서비스 토큰 (C 서버 호출용)")
    p.add_argument("--user", default="U001")
    p.add_argument("--restaurant", default="STORE101")
    p.add_argument("--subtotal", type=int, default=18000, help="메뉴 합계 (명세 예시 18,000)")
    p.add_argument("--delivery-fee", type=int, default=2000)
    p.add_argument("--rule-version", default="BEN-V1")
    args = p.parse_args()
    port = build_port(args)
    run = Runner(port, args)

    print("\n[1] C-02 결제수단 목록 (D가 결제수단 유형을 확인할 때 부름)")
    try:
        c02 = port.list_methods(args.user)
        methods = list(c02["results"])
    except Exception as exc:
        check(False, "C-02 호출", f"{type(exc).__name__}: {exc}")
        return summary()
    check(bool(methods), "C-02 results에 결제수단이 있음", f"{len(methods)}개")
    for m in methods:
        missing = C02_METHOD_KEYS - set(m)
        check(
            not missing,
            f"C-02 {m.get('payment_method_id')} 필드",
            f"빠짐: {sorted(missing)}" if missing else "",
        )
        check(
            m.get("type") in ("CARD", "LOCAL_CURRENCY"),
            f"C-02 {m.get('payment_method_id')} type 값",
            str(m.get("type")),
        )

    print("\n[2] C-05 혜택 계산 (D가 할인 금액을 다시 확인할 때 부름)")
    c05 = {}
    for m in methods:
        mid = m["payment_method_id"]
        body = {
            "request_id": "CHK-C05",
            "user_id": args.user,
            "restaurant_id": args.restaurant,
            "menu_subtotal_krw": args.subtotal,
            "delivery_fee_krw": args.delivery_fee,
            "payment_method_id": mid,
            "benefit_rule_version": args.rule_version,
        }
        try:
            res = port.calculate(body)
        except Exception as exc:
            check(False, f"C-05 {mid} 호출", f"{type(exc).__name__}: {exc}")
            continue
        missing = C05_KEYS - set(res)
        check(not missing, f"C-05 {mid} 필드", f"빠짐: {sorted(missing)}" if missing else "")
        if missing:
            continue
        c05[mid] = res
        d = res["instant_discount_krw"]
        check(
            isinstance(d, int) and not isinstance(d, bool) and d >= 0,
            f"C-05 {mid} 할인액은 0 이상 정수",
            repr(d),
        )
        check(res["payment_method_id"] == mid, f"C-05 {mid} 같은 결제수단으로 계산")
        check(
            res["benefit_rule_version"] == args.rule_version, f"C-05 {mid} 같은 규칙 버전으로 계산"
        )
        expected_charge = args.subtotal + args.delivery_fee - (d if res["eligible"] else 0)
        check(
            res["charge_krw"] == expected_charge,
            f"C-05 {mid} charge = 메뉴 + 배달비 - 할인",
            f"{res['charge_krw']:,} / 기대 {expected_charge:,}",
        )

    print("\n[3] C를 붙인 D 주문 검증 (D-02)")
    if not c05:
        check(False, "C-05가 정상 응답한 결제수단이 없어 D 확인을 건너뜀")
        return summary()
    types = {m["payment_method_id"]: m["type"] for m in methods}
    mid = next(iter(c05))
    disc = c05[mid]["instant_discount_krw"] if c05[mid]["eligible"] else 0

    v = run.validate(mid, disc)
    check(v.valid, f"C가 계산한 할인 {disc:,}원 그대로 → D 통과", ",".join(v.reason_codes))
    v = run.validate(mid, disc + 1000)
    check(
        not v.valid and "AMOUNT_MISMATCH" in v.reason_codes,
        "할인을 1,000원 부풀린 견적 → D 차단 (AMOUNT_MISMATCH)",
        ",".join(v.reason_codes),
    )
    v = run.validate(mid, disc, rule_version="BEN-UNKNOWN-CHECK")
    check(
        not v.valid and "QUOTE_DATA_UNVERIFIED" in v.reason_codes,
        "C가 모르는 규칙 버전 → C 에러 → D 확인 불가로 차단",
        ",".join(v.reason_codes)
        + ("  ※ C가 모르는 버전에도 계산해 주면 여기서 실패" if v.valid else ""),
    )
    v = run.validate("NOT-MINE-CHECK", 0)
    check(
        not v.valid and "QUOTE_DATA_UNVERIFIED" in v.reason_codes,
        "사용자 것이 아닌 결제수단 → D 차단",
        ",".join(v.reason_codes),
    )

    print("\n[4] '지역화폐만' 조건 (C-02의 type으로 판단)")
    local = next((k for k, t in types.items() if t == "LOCAL_CURRENCY" and k in c05), None)
    card = next((k for k, t in types.items() if t == "CARD" and k in c05), None)
    if local:
        ld = c05[local]["instant_discount_krw"] if c05[local]["eligible"] else 0
        v = run.validate(local, ld, local_only=True)
        check(v.valid, f"지역화폐 {local} → 통과", ",".join(v.reason_codes))
    else:
        print("  - C-02에 지역화폐 수단이 없어 건너뜀")
    if card:
        cd = c05[card]["instant_discount_krw"] if c05[card]["eligible"] else 0
        v = run.validate(card, cd, local_only=True)
        check(
            not v.valid and "PAYMENT_METHOD_NOT_ALLOWED" in v.reason_codes,
            f"카드 {card} → 차단 (PAYMENT_METHOD_NOT_ALLOWED)",
            ",".join(v.reason_codes),
        )
    return summary()


def summary() -> int:
    bad = [r for r in results if not r[0]]
    print(f"\n결과: {len(results) - len(bad)}/{len(results)} 통과")
    if bad:
        print("실패 항목:")
        for _, name, detail in bad:
            print(f"  - {name}  {detail}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
