"""O가 부르는 A의 입구: 명세 body(dict) → 명세 응답(dict). 세션은 호출마다 열고 닫는다.

A 서비스(service.py)는 (db, pydantic) 모양이라, O의 DiscoveryPort(dict)에 맞게 여기서 바꿔 준다.
업무 오류는 팀 공통 AppError로 그대로 올라간다.
"""

from collections.abc import Callable

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.errors import AppError
from app.modules.discovery import service
from app.modules.discovery.schemas import ConstraintsPatch, CreateRequestBody

Body = dict


def _validated(model, data):
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        fields = [
            {"loc": ".".join(str(p) for p in e["loc"]), "reason": e["type"]} for e in exc.errors()
        ]
        raise AppError(400, "VALIDATION_ERROR", "요청 형식이 올바르지 않습니다.", {"fields": fields}) from exc


class DiscoveryFacade:
    def __init__(self, session_factory: Callable[[], Session] | None = None):
        self._sessions = session_factory or SessionLocal

    def create_request(self, body: Body) -> Body:  # A-01
        with self._sessions() as db:
            return service.create_request(db, _validated(CreateRequestBody, body))

    def update_constraints(self, request_id: str, body: Body) -> Body:  # A-04
        patch = _validated(ConstraintsPatch, body.get("constraints") or {})
        with self._sessions() as db:
            return service.patch_constraints(db, request_id, patch)

    def search(self, request_id: str, body: Body) -> Body:  # A-05
        with self._sessions() as db:
            return service.search_request(db, request_id, int((body or {}).get("top_n", 3)))

    def select(self, request_id: str, body: Body) -> Body:  # A-06
        with self._sessions() as db:
            return service.select_candidate(db, request_id, body["candidate_id"])
