"""가짜 A (탐색·추천): A-01 조건 추출, A-04 부족 조건 입력, A-05 탐색, A-06 후보 선택.

LLM 대신 간단한 정규식으로 조건을 뽑는다. 명세의 응답 모양만 정확히 맞춘다.
필수 조건: 인원(people)과 예산(budget_krw). 빠지면 NEED_CLARIFICATION + 선택·입력 질문.
"""

import copy
import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import AppError
from app.fakes import catalog
from app.fakes.scenario import ScenarioBook, split_tags

_PEOPLE_WORDS = {"혼자": 1, "둘이": 2, "셋이": 3, "넷이": 4}
_CUISINES = {"치킨": "치킨", "피자": "피자", "짬뽕": "중식", "짜장": "중식", "중식": "중식"}
_QUESTIONS = {
    "people": {
        "slot": "people",
        "question": "몇 명이 드실 건가요?",
        "input_type": "SELECT",
        "options": [1, 2, 3, 4],
        "required": True,
    },
    "budget_krw": {
        "slot": "budget_krw",
        "question": "예산은 얼마까지인가요?",
        "input_type": "TEXT",
        "options": [],
        "required": True,
    },
}


def extract(message: str) -> tuple[dict[str, Any], list[str]]:
    """문장 → (constraints 일부, normalization_notes)."""
    c: dict[str, Any] = {}
    notes: list[str] = []

    if m := re.search(r"(\d+)\s*(명|인분|인)", message):
        c["people"] = int(m.group(1))
    else:
        for word, n in _PEOPLE_WORDS.items():
            if word in message:
                c["people"] = n
                break

    if m := re.search(r"(\d+(?:\.\d+)?)\s*만\s*원", message):
        c["budget_krw"] = int(float(m.group(1)) * 10000)
    elif m := re.search(r"(\d[\d,]{3,})\s*원", message):
        c["budget_krw"] = int(m.group(1).replace(",", ""))

    for word, cuisine in _CUISINES.items():
        if word in message:
            c["cuisine"] = cuisine
            break

    if re.search(r"안\s*매운|안맵게|맵지\s*않", message):
        c["max_spice_level"] = 0
        notes.append("'안 매운' → max_spice_level 0")
    if "빨리" in message:
        c["max_delivery_minutes"] = 40
        notes.append("'빨리' → 배달예상 40분 이내 (기본 규칙)")
    excluded = re.findall(r"(\S+?)\s*(?:빼고|없이|알레르기)", message)
    if excluded:
        c["excluded_ingredients"] = excluded
    if "지역화폐" in message:
        c["payment_requirements"] = {"local_currency_only": True}
    return c, notes


def _base_constraints(delivery_address: dict | None) -> dict[str, Any]:
    return {
        "budget_krw": None,
        "people": None,
        "cuisine": None,
        "excluded_ingredients": [],
        "max_spice_level": None,
        "payment_requirements": {"local_currency_only": False},
        "delivery_address": delivery_address,
        "max_delivery_minutes": None,
        "menu_keywords": [],
    }


@dataclass
class _Req:
    request_id: str
    user_id: str
    original_message: str
    constraints: dict[str, Any]
    constraints_version: int
    status: str
    notes: list[str]
    candidates: list[dict] = field(default_factory=list)
    selected_candidate_id: str | None = None


class FakeDiscovery:
    def __init__(self, book: ScenarioBook):
        self.book = book
        self.requests: dict[str, _Req] = {}

    # --- 공통 -------------------------------------------------------------------
    def _down(self, request_id: str) -> None:
        if self.book.has(request_id, "A_DOWN"):
            raise AppError(503, "DISCOVERY_UNAVAILABLE", "A 응답 없음 (#A장애)")

    def _get(self, request_id: str) -> _Req:
        r = self.requests.get(request_id)
        if r is None:
            raise AppError(404, "REQUEST_NOT_FOUND", "주문 요청을 찾을 수 없습니다.")
        return r

    @staticmethod
    def _missing(c: dict) -> list[str]:
        return [slot for slot in ("people", "budget_krw") if c.get(slot) in (None, "")]

    def _view(self, r: _Req) -> dict[str, Any]:
        missing = self._missing(r.constraints)
        return {
            "request_id": r.request_id,
            "user_id": r.user_id,
            "original_message": r.original_message,
            "constraints_confirmed": not missing,
            "constraints": copy.deepcopy(r.constraints),
            "status": r.status,
            "constraints_version": r.constraints_version,
            "clarification_questions": [dict(_QUESTIONS[s]) for s in missing],
            "normalization_notes": list(r.notes),
        }

    # --- A-01 ---------------------------------------------------------------------
    def create_request(self, body: dict[str, Any]) -> dict[str, Any]:
        request_id = body["request_id"]
        if request_id in self.requests:  # 같은 request_id 재호출 = 처음 결과 (멱등)
            return self._view(self.requests[request_id])
        message = body.get("message") or ""
        clean, flags = split_tags(message)
        self.book.set(request_id, flags)
        self._down(request_id)
        if not 1 <= len(message) <= 300:
            raise AppError(400, "VALIDATION_ERROR", "주문 요청은 1~300자로 입력해 주세요.")

        found, notes = extract(clean)
        c = _base_constraints(body.get("delivery_address"))
        c.update(found)
        status = "NEED_CLARIFICATION" if self._missing(c) else "CONFIRMED"
        r = _Req(request_id, body["user_id"], message, c, 1, status, notes)
        self.requests[request_id] = r
        return self._view(r)

    # --- A-04 ---------------------------------------------------------------------
    def update_constraints(self, request_id: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self._get(request_id)
        self._down(request_id)
        if r.status == "SELECTED":
            raise AppError(409, "INVALID_STATUS", "후보를 선택한 뒤에는 조건을 바꿀 수 없습니다.")
        patch = dict(body.get("constraints") or {})
        if "budget_krw" in patch:
            try:
                patch["budget_krw"] = int(str(patch["budget_krw"]).replace(",", ""))
            except ValueError as exc:
                raise AppError(400, "VALIDATION_ERROR", "budget_krw는 숫자여야 합니다.") from exc
            if patch["budget_krw"] <= 0:
                raise AppError(
                    400,
                    "VALIDATION_ERROR",
                    "budget_krw는 0보다 커야 합니다.",
                    {
                        "fields": [
                            {"loc": "body.constraints.budget_krw", "reason": "must_be_positive"}
                        ]
                    },
                )
        if "people" in patch:
            try:
                patch["people"] = int(patch["people"])
            except (TypeError, ValueError) as exc:
                raise AppError(400, "VALIDATION_ERROR", "people은 숫자여야 합니다.") from exc
            if not 1 <= patch["people"] <= 50:
                raise AppError(400, "VALIDATION_ERROR", "people은 1~50이어야 합니다.")
        unknown = set(patch) - set(r.constraints)
        if unknown:
            raise AppError(400, "VALIDATION_ERROR", f"모르는 조건: {sorted(unknown)}")
        r.constraints.update(patch)
        r.constraints_version += 1
        r.status = "NEED_CLARIFICATION" if self._missing(r.constraints) else "CONFIRMED"
        r.candidates = []
        return self._view(r)

    # --- A-05 ---------------------------------------------------------------------
    def search(self, request_id: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self._get(request_id)
        self._down(request_id)
        if r.status not in ("CONFIRMED", "RECOMMENDED"):
            raise AppError(409, "INVALID_STATUS", "조건 확정 전에는 탐색할 수 없습니다.")
        top_n = int(body.get("top_n") or 3)
        exclude = set(body.get("exclude_restaurant_ids") or [])
        c = r.constraints
        found = []
        for rest in catalog.RESTAURANTS:
            if rest.restaurant_id in exclude:
                continue
            if c.get("cuisine") and rest.cuisine != c["cuisine"]:
                continue
            if (
                c["payment_requirements"].get("local_currency_only")
                and not rest.local_currency_merchant
            ):
                continue
            item = self._pick_menu(rest, c)
            if item is None:
                continue
            found.append((rest, item))

        cands = []
        for rest, (m, qty) in found:
            total = m.price_krw * qty + rest.delivery_fee_krw
            violations = []
            if c.get("budget_krw") and total > c["budget_krw"]:
                violations.append("BUDGET_EXCEEDED")
            if c.get("max_delivery_minutes") and rest.delivery_minutes > c["max_delivery_minutes"]:
                violations.append("DELIVERY_TOO_SLOW")
            score = round(rest.rating * 20 - rest.delivery_minutes / 5 - len(violations) * 30, 1)
            reasons = [
                f"{'예산 안' if 'BUDGET_EXCEEDED' not in violations else '예산 초과'}"
                f"({total:,}원), {rest.delivery_minutes}분 안에 도착 예정"
            ]
            if rest.local_currency_merchant:
                reasons.append("지역화폐 가능(추정)")
            cands.append(
                {
                    "restaurant": rest.summary(),
                    "items": [
                        {
                            "menu_id": m.menu_id,
                            "name": m.name,
                            "quantity": qty,
                            "option_ids": [],
                            "unit_price_krw": m.price_krw,
                        }
                    ],
                    "estimated_total_krw": total,
                    "meets_all_constraints": not violations,
                    "violations": violations,
                    "score": score,
                    "reasons": reasons,
                }
            )
        cands.sort(key=lambda x: (not x["meets_all_constraints"], -x["score"]))
        cands = cands[:top_n]
        if exclude and not cands:  # 재추천인데 더 없음: 지금 후보를 그대로 둔다 (이슈 #68)
            return {"request_id": request_id, "status": "NO_CANDIDATES", "candidates": [],
                    "relaxation_suggestions": [], "search_notice": None}
        for i, cand in enumerate(cands, start=1):
            cand["candidate_id"] = f"CAND{i:03d}"
            cand["rank"] = i
        r.candidates = [
            {"candidate_id": x.pop("candidate_id"), "rank": x.pop("rank"), **x} for x in cands
        ]
        r.status = "RECOMMENDED"
        return {
            "request_id": request_id,
            "status": r.status,
            "candidates": copy.deepcopy(r.candidates),
            "relaxation_suggestions": [],
            "search_notice": None,
        }

    @staticmethod
    def _pick_menu(rest: catalog.Restaurant, c: dict) -> tuple[catalog.Menu, int] | None:
        people = c.get("people") or 1
        ok = []
        for m in rest.menus:
            if c.get("max_spice_level") is not None and m.spice_level > c["max_spice_level"]:
                continue
            text = f"{m.name} {m.description}"
            if any(word in text for word in c.get("excluded_ingredients") or []):
                continue
            ok.append(m)
        if not ok:
            return None
        m = min(ok, key=lambda x: x.price_krw)
        return m, max(1, math.ceil(people / m.serves))

    # --- A-06 ---------------------------------------------------------------------
    def select(self, request_id: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self._get(request_id)
        self._down(request_id)
        cand = next(
            (x for x in r.candidates if x["candidate_id"] == body.get("candidate_id")), None
        )
        if cand is None:
            raise AppError(
                409,
                "CANDIDATE_NOT_FOUND",
                "추천 후보에 없는 선택입니다. 후보를 다시 조회해 주세요.",
            )
        # 추천 목록 안이면 다른 후보로 다시 선택하는 것도 허용 (명세 후순위 협의 ②)
        r.selected_candidate_id = cand["candidate_id"]
        r.status = "SELECTED"
        return {
            "request_id": request_id,
            "status": r.status,
            "selected_candidate": copy.deepcopy(cand),
            "constraints_version": r.constraints_version,
        }
