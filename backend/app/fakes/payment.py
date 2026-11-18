"""가짜 E (모의결제): E-01 결제 요청, E-03 실행 ID로 결제 조회.

E-01 처리 순서는 명세 그대로:
  ① Idempotency-Key(= execution_id) 멱등 확인
  ② D-06 결제 직전 검증 → allowed: true일 때만 진행 (차단·D 오류는 결제 안 함 = BLOCKED)
  ③ 원장 기록 → 결과 APPROVED / FAILED / UNKNOWN (모두 200)

진짜 E는 별도 서버(Spring)다. 여기서는 같은 프로세스 안에서 함수로 흉내 내고,
HTTP로 따로 띄워 보는 서버는 scripts/fake_e_server.py.
"""

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


class FakePayment:
    def __init__(
        self, book: ScenarioBook, check_authorization: CheckAuthorizationFn, now=clock.now
    ):
        self.book = book
        self.check_authorization = check_authorization
        self.now = now
        self.ledger: dict[str, dict] = {}  # execution_id → 결제 기록
        self.requests: dict[str, dict] = {}  # execution_id → 처음 요청 body

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
