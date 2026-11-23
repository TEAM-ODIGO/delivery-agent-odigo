"""A(탐색·추천) 모듈의 업무 로직.

공통 규칙상 O·A는 같은 FastAPI 서버 안의 모듈이라, O는 HTTP 대신 이 함수들을 바로 호출하면 된다.
실패는 AppError로 던지며 HTTP로 노출할 때만 공통 에러 JSON으로 바뀐다.

    from app.modules.discovery import service as discovery
    result = discovery.create_request(db, CreateRequestBody(...))
"""

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.discovery.timeutil import now_kst, to_iso
from app.core.errors import AppError
from app.modules.discovery import llm
from app.modules.discovery.constants import (
    CLARIFICATION_TEMPLATES,
    CONFIRMED,
    CUISINES,
    NEED_CLARIFICATION,
    RECOMMENDED,
    REQUIRED_SLOTS,
    SELECTED,
    SOURCE_LLM,
    SOURCE_RULE,
    SOURCE_USER,
)
from app.modules.discovery.hours import filter_now
from app.modules.discovery.extractor import (
    extract_by_rules,
    grounded_cuisine,
    grounded_exclusions,
    grounded_menu_keywords,
    grounded_tastes,
    has_hint,
    normalize_cuisine,
)
from app.modules.discovery.models import Candidate, ConstraintsHistory, DiscoveryRequest, Exclusion
from app.modules.discovery.schemas import Constraints, ConstraintsPatch, CreateRequestBody, LlmExtraction, ordered_tastes
from app.modules.discovery.brands import find_brands
from app.modules.discovery.catalog_models import Restaurant
from app.modules.discovery.search import search

MESSAGE_MAX_LENGTH = 300
NO_CANDIDATES = "NO_CANDIDATES"  # A-05 응답 전용 상태: 추천할 가게가 없음 (요청 상태는 CONFIRMED 유지)

# 응답에서 normalization_notes를 내보낼 순서
NOTE_ORDER = [
    "people", "budget_krw", "cuisine", "taste_preferences", "max_delivery_minutes", "max_spice_level",
    "payment_requirements",
]


# ---------------------------------------------------------------- 공통 도우미

def _load_for_update(db: Session, request_id: str) -> DiscoveryRequest:
    row = db.scalars(
        select(DiscoveryRequest).where(DiscoveryRequest.request_id == request_id).with_for_update()
    ).first()
    if row is None:
        raise AppError(404, "REQUEST_NOT_FOUND", "주문 요청을 찾을 수 없습니다.")
    return row


def _missing_slots(constraints: dict) -> list[str]:
    missing = []
    for slot in REQUIRED_SLOTS:
        if constraints.get(slot) is None:
            missing.append(slot)
    return missing


def _questions_for(missing: list[str]) -> list[dict]:
    questions = []
    for slot in missing:
        questions.append(dict(CLARIFICATION_TEMPLATES[slot]))
    return questions


def _notes_list(notes: dict) -> list[str]:
    result = []
    for key in NOTE_ORDER:
        if key in notes:
            result.append(notes[key])
    for key, value in notes.items():
        if key not in NOTE_ORDER:
            result.append(value)
    return result


def _add_history(db: Session, request: DiscoveryRequest, source: str) -> None:
    db.add(
        ConstraintsHistory(
            request_id=request.request_id,
            version=request.constraints_version,
            source=source,
            constraints=request.constraints,
            created_at=now_kst(),
        )
    )


def _current_candidates(db: Session, request_id: str) -> list[Candidate]:
    return list(
        db.scalars(
            select(Candidate)
            .where(Candidate.request_id == request_id, Candidate.is_current.is_(True))
            .order_by(Candidate.rank)
        )
    )


def _retire_candidates(db: Session, request_id: str) -> None:
    db.execute(
        update(Candidate)
        .where(Candidate.request_id == request_id, Candidate.is_current.is_(True))
        .values(is_current=False)
    )


def _candidate_json(candidate: Candidate) -> dict:
    body = {"candidate_id": candidate.candidate_id, "rank": candidate.rank}
    body.update(candidate.body)
    return body


def _request_view(db: Session, request: DiscoveryRequest, include_candidates: bool) -> dict:
    view = {
        "request_id": request.request_id,
        "user_id": request.user_id,
        "original_message": request.original_message,
        "constraints_confirmed": request.status in (CONFIRMED, RECOMMENDED, SELECTED),
        "constraints": request.constraints,
        "status": request.status,
        "constraints_version": request.constraints_version,
        "clarification_questions": request.clarification_questions,
        "normalization_notes": _notes_list(request.normalization_notes),
    }
    if include_candidates:
        view["candidates"] = [_candidate_json(c) for c in _current_candidates(db, request.request_id)]
        view["selected_candidate_id"] = request.selected_candidate_id
        view["created_at"] = to_iso(request.created_at)
        view["updated_at"] = to_iso(request.updated_at)
    return view


def _clean_words(values: list[str] | None) -> list[str]:
    result = []
    for value in values or []:
        word = str(value).strip()
        if word and word not in result:
            result.append(word[:30])
    return result


# ---------------------------------------------------------------- A-01 조건 추출

def _merge(rule_constraints: dict, rule_notes: dict, extraction: LlmExtraction | None,
           message: str) -> tuple[dict, dict, str]:
    """규칙 결과를 먼저 쓰고, 규칙이 비워 둔 항목만 LLM 결과로 채운다.

    규칙은 문장에서 실제로 찾은 표현으로만 값을 정하므로, 둘 다 값이 있으면 규칙을 믿는다.
    LLM 값은 문장에 근거 단어가 있을 때만 받는다 (예: '만오천원'을 5000으로 읽거나,
    '국물'만 말했는데 음식 종류 '분식'·메뉴 '국물'을 지어내는 경우를 막는다).
    """
    if extraction is None:
        return rule_constraints, dict(rule_notes), SOURCE_RULE

    merged = dict(rule_constraints)

    # 숫자 조건: 규칙이 못 찾았고, 문장에 그 조건의 근거 단어가 있을 때만 LLM 값
    for key in ("budget_krw", "people", "max_spice_level", "max_delivery_minutes"):
        llm_value = getattr(extraction, key)
        if merged[key] is None and llm_value is not None and has_hint(key, message):
            merged[key] = llm_value

    # 범위를 좁히는 조건: 문장에 그대로 나온 것만 더한다
    if merged["cuisine"] is None:
        merged["cuisine"] = grounded_cuisine(message, extraction.cuisine)
    merged["excluded_ingredients"] = _clean_words(
        rule_constraints["excluded_ingredients"] + grounded_exclusions(message, extraction.excluded_ingredients)
    )
    merged["menu_keywords"] = _clean_words(
        rule_constraints["menu_keywords"] + grounded_menu_keywords(message, extraction.menu_keywords)
    )
    local_only = rule_constraints["payment_requirements"]["local_currency_only"]
    if extraction.local_currency_only and has_hint("local_currency_only", message):
        local_only = True
    merged["payment_requirements"] = {"local_currency_only": local_only}

    tastes = ordered_tastes(rule_constraints["taste_preferences"] + grounded_tastes(message, extraction.taste_preferences))
    # 맵기 상한이 1 이하('안 매운'·'덜 매운')인데 SPICY가 들어오면 뺀다
    if merged["max_spice_level"] is not None and merged["max_spice_level"] <= 1 and "SPICY" in tastes:
        tastes.remove("SPICY")
    merged["taste_preferences"] = tastes

    # 규칙 메모는 최종 값이 규칙 결과와 같을 때만 남긴다
    notes = {}
    for key, note in rule_notes.items():
        if merged.get(key) == rule_constraints.get(key):
            notes[key] = note
    return merged, notes, SOURCE_LLM


def create_request(db: Session, body: CreateRequestBody) -> dict:
    message = body.message.strip()
    if not 1 <= len(message) <= MESSAGE_MAX_LENGTH:
        raise AppError(400, "VALIDATION_ERROR", "주문 요청은 1~300자로 입력해 주세요.", {
            "fields": [{"loc": "body.message", "reason": "invalid_length"}],
        })

    existing = db.get(DiscoveryRequest, body.request_id)
    if existing is not None:
        return _replay_create(existing, body, message)

    rule_constraints, rule_notes = extract_by_rules(message)
    extraction = llm.extract_conditions(db, body.request_id, message)
    constraints, notes, source = _merge(rule_constraints, rule_notes, extraction, message)
    constraints["delivery_address"] = body.delivery_address.model_dump()
    constraints["brands"] = find_brands(message, list(db.scalars(select(Restaurant.name))))
    constraints = Constraints.model_validate(constraints).model_dump()

    missing = _missing_slots(constraints)
    now = now_kst()
    request = DiscoveryRequest(
        request_id=body.request_id,
        user_id=body.user_id,
        original_message=message,
        status=NEED_CLARIFICATION if missing else CONFIRMED,
        constraints=constraints,
        constraints_version=1,
        clarification_questions=_questions_for(missing),
        normalization_notes=notes,
        selected_candidate_id=None,
        search_count=0,
        initial_response={},
        created_at=now,
        updated_at=now,
    )
    request.initial_response = _request_view(db, request, include_candidates=False)

    try:
        db.add(request)
        db.flush()  # 조건 이력이 참조하므로 요청 행을 먼저 넣는다
        _add_history(db, request, source)
        db.commit()
    except IntegrityError:
        # 같은 request_id가 거의 동시에 두 번 들어온 경우: 먼저 저장된 결과를 돌려준다
        db.rollback()
        existing = db.get(DiscoveryRequest, body.request_id)
        if existing is None:
            raise
        return _replay_create(existing, body, message)

    return request.initial_response


def _replay_create(existing: DiscoveryRequest, body: CreateRequestBody, message: str) -> dict:
    """멱등: 같은 request_id + 같은 내용이면 처음 결과, 내용이 다르면 409."""
    first = existing.initial_response
    same_address = first["constraints"].get("delivery_address") == body.delivery_address.model_dump()
    if existing.user_id == body.user_id and existing.original_message == message and same_address:
        return first
    raise AppError(409, "IDEMPOTENCY_CONFLICT", "같은 request_id로 다른 내용의 요청이 이미 있습니다.", {
        "request_id": existing.request_id,
    })


# ---------------------------------------------------------------- A-02 조회

def get_request(db: Session, request_id: str) -> dict:
    request = db.get(DiscoveryRequest, request_id)
    if request is None:
        raise AppError(404, "REQUEST_NOT_FOUND", "주문 요청을 찾을 수 없습니다.")
    return _request_view(db, request, include_candidates=True)


# ---------------------------------------------------------------- A-04 조건 입력·수정

def _patched_value(field_name: str, patch: ConstraintsPatch):
    value = getattr(patch, field_name)

    if field_name == "cuisine":
        if value is None:
            return None
        normalized = normalize_cuisine(value)
        if normalized is None:
            raise AppError(400, "VALIDATION_ERROR", f"알 수 없는 음식 종류입니다: {value}", {
                "fields": [{"loc": "body.constraints.cuisine", "reason": "unknown_value"}],
                "allowed_values": list(CUISINES.keys()),
            })
        return normalized

    if field_name in ("excluded_ingredients", "menu_keywords", "brands"):
        return _clean_words(value)

    if field_name == "taste_preferences":
        return ordered_tastes(value)

    if field_name == "payment_requirements":
        if value is None:
            return {"local_currency_only": False}
        return value.model_dump()

    if field_name == "delivery_address":
        if value is None:
            raise AppError(400, "VALIDATION_ERROR", "delivery_address는 비울 수 없습니다.", {
                "fields": [{"loc": "body.constraints.delivery_address", "reason": "cannot_be_null"}],
            })
        return value.model_dump()

    return value


def patch_constraints(db: Session, request_id: str, patch: ConstraintsPatch) -> dict:
    sent_fields = patch.model_fields_set
    if not sent_fields:
        raise AppError(400, "VALIDATION_ERROR", "바꿀 조건을 하나 이상 보내 주세요.", {
            "fields": [{"loc": "body.constraints", "reason": "empty"}],
        })

    request = _load_for_update(db, request_id)
    if request.status == SELECTED:
        raise AppError(409, "INVALID_STATUS", "후보를 선택한 뒤에는 조건을 바꿀 수 없습니다.", {
            "status": request.status,
        })

    new_constraints = dict(request.constraints)
    for field_name in sent_fields:
        new_constraints[field_name] = _patched_value(field_name, patch)

    if new_constraints == request.constraints:
        db.rollback()
        return _request_view(db, request, include_candidates=False)

    # 바뀐 조건의 해석 메모는 더 이상 맞지 않으니 지운다
    notes = dict(request.normalization_notes)
    for field_name in sent_fields:
        notes.pop(field_name, None)

    missing = _missing_slots(new_constraints)
    request.constraints = new_constraints
    request.constraints_version += 1
    request.status = NEED_CLARIFICATION if missing else CONFIRMED
    request.clarification_questions = _questions_for(missing)
    request.normalization_notes = notes
    request.updated_at = now_kst()

    _add_history(db, request, SOURCE_USER)
    _retire_candidates(db, request_id)  # 조건이 바뀌었으니 이전 추천은 무효
    db.commit()
    return _request_view(db, request, include_candidates=False)


# ---------------------------------------------------------------- A-05 탐색·추천

def search_request(db: Session, request_id: str, top_n: int) -> dict:
    request = _load_for_update(db, request_id)
    if request.status == NEED_CLARIFICATION:
        raise AppError(409, "INVALID_STATUS", "조건 확정 전에는 탐색할 수 없습니다.", {"status": request.status})
    if request.status == SELECTED:
        raise AppError(409, "INVALID_STATUS", "후보를 선택한 뒤에는 다시 탐색할 수 없습니다.", {
            "status": request.status,
        })

    trace: list[dict] = []
    bodies, suggestions = search(db, request.constraints, top_n, now=filter_now(), trace=trace)
    # 요청한 메뉴 대신 비슷한 메뉴로 채웠을 때 그 이유 (이슈 #37, 화면 위 안내)
    notice = bodies[0].pop("search_notice", None) if bodies else None

    # 선택 사항: LLM으로 첫 문장을 다듬는다. 실패하면 기본 문장 그대로
    facts = [" ".join(body["reasons"]) for body in bodies]
    polished = llm.explain_candidates(db, request_id, facts)
    if polished is not None:
        for body, sentence in zip(bodies, polished):
            body["reasons"] = [sentence] + body["reasons"][1:]

    _retire_candidates(db, request_id)
    request.search_count += 1
    used = db.scalar(select(func.count()).select_from(Candidate).where(Candidate.request_id == request_id))

    now = now_kst()
    candidates = []
    for index, body in enumerate(bodies):
        candidate = Candidate(
            request_id=request_id,
            candidate_id=f"CAND{used + index + 1:03d}",
            search_round=request.search_count,
            constraints_version=request.constraints_version,
            rank=index + 1,
            restaurant_id=body["restaurant"]["restaurant_id"],
            score=body["score"],
            meets_all_constraints=body["meets_all_constraints"],
            body=body,
            is_current=True,
            created_at=now,
        )
        db.add(candidate)
        candidates.append(candidate)

    # 후보가 되지 못한 가게와 이유 (이슈 #48, 점주 대시보드용)
    for row in trace:
        db.add(Exclusion(request_id=request_id, search_round=request.search_count, created_at=now, **row))

    request.status = RECOMMENDED if candidates else CONFIRMED
    request.updated_at = now
    db.commit()

    return {
        "request_id": request_id,
        "status": RECOMMENDED if candidates else NO_CANDIDATES,
        "constraints_version": request.constraints_version,
        "candidates": [_candidate_json(c) for c in candidates],
        "relaxation_suggestions": suggestions,
        "search_notice": notice,
    }


# ---------------------------------------------------------------- A-06 후보 선택

def _select_response(request: DiscoveryRequest, candidate: Candidate) -> dict:
    return {
        "request_id": request.request_id,
        "status": SELECTED,
        "selected_candidate": _candidate_json(candidate),
        "constraints_version": candidate.constraints_version,
    }


def select_candidate(db: Session, request_id: str, candidate_id: str) -> dict:
    request = _load_for_update(db, request_id)
    candidate = db.get(Candidate, (request_id, candidate_id))

    if request.status == SELECTED:
        if request.selected_candidate_id == candidate_id and candidate is not None:
            db.rollback()
            return _select_response(request, candidate)  # 같은 선택을 다시 보냄 → 같은 결과
        raise AppError(409, "INVALID_STATUS", "이미 다른 후보를 선택했습니다.", {
            "selected_candidate_id": request.selected_candidate_id,
        })

    if request.status != RECOMMENDED:
        raise AppError(409, "INVALID_STATUS", "추천 후보가 없는 상태에서는 선택할 수 없습니다.", {
            "status": request.status,
        })

    if candidate is None or not candidate.is_current:
        raise AppError(409, "CANDIDATE_NOT_FOUND", "추천 후보에 없는 선택입니다. 후보를 다시 조회해 주세요.")

    request.status = SELECTED
    request.selected_candidate_id = candidate_id
    request.updated_at = now_kst()
    db.commit()
    return _select_response(request, candidate)
