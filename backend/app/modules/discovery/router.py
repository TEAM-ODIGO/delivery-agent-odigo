"""A(탐색·추천) HTTP 엔드포인트. 경로 접두어 /discovery, 호출자는 O(서비스 토큰)."""

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_session as get_db
from app.core.errors import AppError
from app.modules.discovery.security import require_service_token
from app.modules.discovery import llm, service
from app.modules.discovery.schemas import CreateRequestBody, PatchConstraintsBody, SearchBody, SelectBody

router = APIRouter(prefix="/discovery", tags=["A · 탐색·추천"])


def check_request_id_header(header_value: str | None, request_id: str) -> None:
    """X-Request-ID가 오면 경로·본문의 request_id와 같아야 한다 (추적 키가 섞이지 않게)."""
    if header_value is not None and header_value != request_id:
        raise AppError(400, "VALIDATION_ERROR", "X-Request-ID와 request_id가 다릅니다.", {
            "fields": [{"loc": "header.X-Request-ID", "reason": "mismatch"}],
        })


@router.post("/requests", status_code=201, dependencies=[Depends(require_service_token)],
             summary="A-01 자연어 주문 요청 생성·조건 추출")
def create_request(
    body: CreateRequestBody,
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
):
    check_request_id_header(x_request_id, body.request_id)
    return service.create_request(db, body)


@router.get("/requests/{request_id}", dependencies=[Depends(require_service_token)],
            summary="A-02 주문 요청 조회")
def get_request(
    request_id: str,
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
):
    check_request_id_header(x_request_id, request_id)
    return service.get_request(db, request_id)


@router.patch("/requests/{request_id}/constraints", dependencies=[Depends(require_service_token)],
              summary="A-04 부족 조건 입력·조건 직접 수정 (MVP 1 재질문)")
def patch_constraints(
    request_id: str,
    body: PatchConstraintsBody,
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
):
    check_request_id_header(x_request_id, request_id)
    return service.patch_constraints(db, request_id, body.constraints)


@router.post("/requests/{request_id}/search", dependencies=[Depends(require_service_token)],
             summary="A-05 음식점·메뉴 탐색 및 추천")
def search_request(
    request_id: str,
    body: SearchBody | None = None,
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
):
    check_request_id_header(x_request_id, request_id)
    top_n = body.top_n if body is not None else 3
    exclude = body.exclude_restaurant_ids if body is not None else []
    return service.search_request(db, request_id, top_n, exclude)


@router.post("/requests/{request_id}/select", dependencies=[Depends(require_service_token)],
             summary="A-06 후보 선택 기록")
def select_candidate(
    request_id: str,
    body: SelectBody,
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
):
    check_request_id_header(x_request_id, request_id)
    expected_key = f"{request_id}:select:{body.candidate_id}"
    if idempotency_key is not None and idempotency_key != expected_key:
        raise AppError(400, "VALIDATION_ERROR", f"Idempotency-Key는 {expected_key} 형식이어야 합니다.", {
            "fields": [{"loc": "header.Idempotency-Key", "reason": "mismatch"}],
        })
    return service.select_candidate(db, request_id, body.candidate_id)


@router.get("/health", summary="A 상태 확인 (인증 없음)")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # DB 연결 실패 종류가 많아 넓게 받는다
        return JSONResponse(status_code=503, content={"service": "discovery", "status": "DOWN"})
    return {"service": "discovery", "status": "UP", "llm": llm.check_llm()}
