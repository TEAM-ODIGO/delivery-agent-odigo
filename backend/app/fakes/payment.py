"""가짜 E (모의결제): E-01 결제 요청, E-03 실행 ID로 결제 조회.

E-01 처리 순서는 명세 그대로:
  ① Idempotency-Key(= execution_id) 멱등 확인
  ② D-06 결제 직전 검증 → allowed: true일 때만 진행 (차단·D 오류는 결제 안 함 = BLOCKED)
  ③ 원장 기록 → 결과 APPROVED / FAILED / UNKNOWN (모두 200)

진짜 E는 별도 서버(Spring)다. 여기서는 같은 프로세스 안에서 함수로 흉내 내고,
HTTP로 따로 띄워 보는 서버는 scripts/fake_e_server.py.
"""

import os
import uuid
from collections.abc import Callable
from typing import Any

from app.core import clock
from app.core.errors import AppError
from app.fakes.scenario import ScenarioBook
from app.modules.orchestrator.ports import PaymentResponseLost

CheckAuthorizationFn = Callable[[dict[str, Any], str], dict[str, Any]]  # (D-06 body, request_id)


_FIELDS = (
    "request_id",
    "user_id",
    "execution_id",
    "approval_id",
    "quote_id",
    "proposal_hash",
    "payment_method_id",
    "charge_krw",
    # 분할결제(로컬 확장, 팀 합의 전): 없으면 None으로 같아 기존 요청과 동일하게 동작
    "local_currency_payment_method_id",
    "local_currency_charge_krw",
)

_D06_FIELDS = (
    "execution_id",
    "approval_id",
    "quote_id",
    "proposal_hash",
    "charge_krw",
    "payment_method_id",
    "local_currency_payment_method_id",
    "local_currency_charge_krw",
)


def _env_pairs(name: str) -> dict[str, str]:
    """'CARD001=값,CARD002=값' 형식의 환경 변수 → {'CARD001': '값', ...} (진짜 E의 nfc.parse_pairs와 같은 규칙)."""
    pairs = {}
    for item in os.getenv(name, "").split(","):
        pm_id, sep, value = item.partition("=")
        if sep and pm_id.strip() and value.strip():
            pairs[pm_id.strip()] = value.strip()
    return pairs


def _norm_serial(serial: str) -> str:
    return serial.replace(":", "").replace("-", "").replace(" ", "").strip().lower()


class FakePayment:
    def __init__(
        self, book: ScenarioBook, check_authorization: CheckAuthorizationFn, now=clock.now
    ):
        self.book = book
        self.check_authorization = check_authorization
        self.now = now
        self.ledger: dict[str, dict] = {}  # execution_id → 결제 기록
        self.requests: dict[str, dict] = {}  # execution_id → 처음 요청 body
        self.refunds: dict[tuple[str, str], dict] = {}  # (payment_id, 멱등 키) → 환불 기록 (로컬 확장)

    def verify_card_tap(self, body: dict[str, Any]) -> dict[str, Any]:
        """NFC 카드 태그 확인 (로컬 확장). 가짜 E에는 카드 등록표가 없어서 항상 통과시킨다."""
        return {"payment_method_id": body["payment_method_id"], "matched": True, "reason_code": None}

    # ---------------- 카드 혜택 화면 (로컬 확장, 팀 합의 전) ----------------
    def identify_card_tap(self, body: dict[str, Any]) -> dict[str, Any]:
        """태그한 카드가 어떤 가상카드인지 찾는다. 등록표는 진짜 E와 같은 환경 변수(E_NFC_TOKENS·E_NFC_CARDS)."""
        tap = body.get("card_tap") or {}
        token = (tap.get("token") or "").strip()
        serial = _norm_serial(tap.get("serial_number") or "")
        if not token and not serial:
            return {"payment_method_id": None, "matched": False, "reason_code": "NFC_TAP_EMPTY",
                    "message": "카드 정보를 읽지 못했어요. 다시 대 주세요."}
        for pm_id, value in _env_pairs("E_NFC_TOKENS").items():
            if token and value == token:
                return {"payment_method_id": pm_id, "matched": True, "reason_code": None, "message": None}
        for pm_id, value in _env_pairs("E_NFC_CARDS").items():
            if serial and _norm_serial(value) == serial:
                return {"payment_method_id": pm_id, "matched": True, "reason_code": None, "message": None}
        return {"payment_method_id": None, "matched": False, "reason_code": "NFC_CARD_NOT_REGISTERED",
                "message": "등록되지 않은 NFC 카드예요."}

    def list_card_payments(self, user_id: str, payment_method_id: str, limit: int = 3) -> dict[str, Any]:
        rows = [
            r for r in self.ledger.values()
            if r["user_id"] == user_id and payment_method_id in (
                r["payment_method_id"], *(leg["payment_method_id"] for leg in r.get("payments", [])))
        ]
        rows.sort(key=lambda r: r.get("approved_at") or "", reverse=True)
        items = []
        for r in rows[:limit]:
            item = self._out(r)
            legs = {leg["payment_method_id"]: leg["charge_krw"] for leg in r.get("payments", [])}
            item["share_krw"] = legs.get(payment_method_id, r["charge_krw"])
            item["created_at"] = r.get("approved_at")
            item["refundable_krw"] = r["charge_krw"] - r["refunded_krw"] if r["status"] == "APPROVED" else 0
            items.append(item)
        return {"payment_method_id": payment_method_id, "items": items}

    def request_refund(self, payment_id: str, body: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        """E-05 흉내: 바로 REFUNDED로 처리한다."""
        row = next((r for r in self.ledger.values() if r["payment_id"] == payment_id), None)
        if row is None:
            raise AppError(404, "PAYMENT_NOT_FOUND", "결제 내역이 없습니다.")
        if row["user_id"] != body["user_id"]:
            raise AppError(403, "FORBIDDEN_USER", "본인의 결제만 환불할 수 있습니다.")
        done = self.refunds.get((payment_id, idempotency_key))
        if done is not None:
            return done
        if row["status"] != "APPROVED":
            raise AppError(409, "INVALID_STATUS", "승인된 결제만 환불할 수 있습니다.")
        if body["amount_krw"] > row["charge_krw"] - row["refunded_krw"]:
            raise AppError(409, "REFUND_AMOUNT_EXCEEDED", "환불 가능 금액을 초과했습니다.")
        row["refunded_krw"] += body["amount_krw"]
        refund = {"refund_id": "RF" + uuid.uuid4().hex[:8].upper(), "status": "REFUNDED",
                  "amount_krw": body["amount_krw"], "requested_at": self.now().isoformat()}
        self.refunds[(payment_id, idempotency_key)] = refund
        return refund

    def get_refund(self, payment_id: str, refund_id: str) -> dict[str, Any]:
        for (pid, _), refund in self.refunds.items():
            if pid == payment_id and refund["refund_id"] == refund_id:
                return refund
        raise AppError(404, "REFUND_NOT_FOUND", "환불 요청이 없습니다.")

    def request_payment(self, body: dict[str, Any]) -> dict[str, Any]:
        """E-01. 응답 유실 시나리오면 결제는 하고 PaymentResponseLost를 던진다."""
        exe = body["execution_id"]
        sent = {k: body.get(k) for k in _FIELDS}
        if exe in self.requests:  # ① 멱등
            if self.requests[exe] != sent:
                raise AppError(409, "IDEMPOTENCY_CONFLICT", "같은 execution_id에 다른 내용입니다.")
            if exe in self.ledger:
                return self._out(self.ledger[exe])
        self.requests[exe] = sent

        # ② D-06
        try:
            check = self.check_authorization(
                {k: body[k] for k in _D06_FIELDS if body.get(k) is not None},
                body["request_id"],
            )
        except Exception:
            return {
                "payment_id": None,
                "execution_id": exe,
                "status": "BLOCKED",
                "reason_codes": ["DELEGATION_UNAVAILABLE"],
            }
        if not check.get("allowed"):
            del self.requests[exe]  # 결제를 만들지 않았으므로 같은 execution_id 재시도 허용
            return {
                "payment_id": None,
                "execution_id": exe,
                "status": "BLOCKED",
                "reason_codes": list(check.get("reason_codes") or []),
            }

        # ③ 원장
        rid = body["request_id"]
        row = {
            "payment_id": "PAY" + uuid.uuid4().hex[:8].upper(),
            "execution_id": exe,
            "transaction_id": "TXN" + uuid.uuid4().hex[:8].upper(),
            "request_id": rid,
            "user_id": body["user_id"],
            "status": "APPROVED",
            "charge_krw": body["charge_krw"],
            "refunded_krw": 0,
            "payment_method_id": body["payment_method_id"],
            "approved_at": self.now().isoformat(),
            "reason_codes": [],
        }
        local_pm = body.get("local_currency_payment_method_id")
        if local_pm:  # 분할결제: 지역화폐 몫 + 카드 몫으로 나눠 기록
            local = body["local_currency_charge_krw"]
            row["payments"] = [
                {"payment_method_id": local_pm, "charge_krw": local},
                {"payment_method_id": body["payment_method_id"], "charge_krw": body["charge_krw"] - local},
            ]
        if self.book.has(rid, "PAY_DECLINE"):
            row.update(status="FAILED", approved_at=None, reason_codes=["MOCK_DECLINED"])
        elif self.book.has(rid, "PAY_UNKNOWN"):
            row.update(
                status="UNKNOWN",
                approved_at=None,
                reason_codes=["FEP_TIMEOUT"],
                _resolve_to="APPROVED",
            )
        self.ledger[exe] = row
        if self.book.consume(rid, "PAY_LOST"):
            raise PaymentResponseLost("E-01 응답 유실 (#응답유실)")
        return self._out(row)

    def get_by_execution(self, execution_id: str) -> dict[str, Any]:
        """E-03. 미확정 건은 조회 시점에 기관 확인이 끝났다고 보고 확정한다 (대사 흉내)."""
        row = self.ledger.get(execution_id)
        if row is None:
            raise AppError(404, "PAYMENT_NOT_FOUND", "해당 실행 ID의 결제 요청이 없습니다.")
        if row["status"] == "UNKNOWN" and row.get("_resolve_to"):
            row["status"] = row.pop("_resolve_to")
            row["approved_at"] = self.now().isoformat()
            row["reason_codes"] = []
        return self._out(row)

    @staticmethod
    def _out(row: dict) -> dict[str, Any]:
        out = {k: v for k, v in row.items() if not k.startswith("_") and k != "user_id"}
        if not out.get("reason_codes"):
            out.pop("reason_codes", None)
        out["mock"] = True
        return out
