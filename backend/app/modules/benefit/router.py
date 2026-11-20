"""C 모듈 HTTP 라우터 (경로 접두어 /benefit). 팀 서버에서는 app.include_router(router)로 붙인다."""
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query

from . import service
from .auth import require_admin, require_service
from .schemas import CalculationRequest, ExtractionRequest, RecommendRequest, RuleVersionRequest, SyncRequest

def _mock(d):
    """명세 예시처럼 시드/mock 데이터로 응답할 때 mock: true를 붙인다."""
    return {**d, "mock": True}


router = APIRouter(prefix="/benefit", tags=["C · 혜택"])


@router.get("/health")                                               # C-09
def health():
    return {"service": "benefit", "status": "UP"}


@router.post("/recommendations", dependencies=[Depends(require_service)])     # C-01 결제수단 추천
def recommend(req: RecommendRequest, idempotency_key: str | None = Header(default=None)):
    return _mock(service.recommend_payment_methods(req, idempotency_key))


@router.get("/recommendations/{recommendation_id}", dependencies=[Depends(require_service)])   # C-04
def get_recommendation(recommendation_id: str):
    return _mock(service.view_recommendation(recommendation_id))


@router.get("/users/{user_id}/payment-methods", dependencies=[Depends(require_service)])      # C-02
def payment_methods(user_id: str, type: str | None = Query(default=None, pattern="^(CARD|LOCAL_CURRENCY)$"),
                    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                    x_request_id: str | None = Header(default=None)):
    return _mock(service.list_payment_methods(user_id, type, page, page_size, x_request_id))


@router.post("/calculations", dependencies=[Depends(require_service)])        # C-05 혜택 재계산
def calculations(req: CalculationRequest):
    return _mock(service.calculate_benefit(req))


@router.get("/local-currency/restaurants/{restaurant_id}", dependencies=[Depends(require_admin)])   # C-03
def local_currency_restaurant(restaurant_id: str):
    return _mock(service.get_local_currency_restaurant(restaurant_id))


@router.get("/rules", dependencies=[Depends(require_admin)])                 # C-06
def rules(product_id: str | None = None, type: str | None = None, status: str = "ACTIVE",
          benefit_rule_version: str | None = None, page: int = Query(default=1, ge=1),
          page_size: int = Query(default=20, ge=1, le=100)):
    return _mock(service.list_rules(product_id, type, status, benefit_rule_version, page, page_size))


@router.get("/rules/{rule_id}", dependencies=[Depends(require_admin)])       # C-07
def rule_detail(rule_id: str):
    return _mock(service.get_rule(rule_id))


@router.post("/local-currency/restaurants/sync", status_code=202, dependencies=[Depends(require_admin)])   # C-08
def sync_restaurants(req: SyncRequest, background: BackgroundTasks, idempotency_key: str | None = Header(default=None)):
    result = service.start_sync(req, idempotency_key)
    if result["status"] == "QUEUED":
        background.add_task(service.run_sync, result["sync_id"])
    return _mock(result)


@router.post("/rules/extractions", dependencies=[Depends(require_admin)])    # C-10
def extract_rules(req: ExtractionRequest, idempotency_key: str | None = Header(default=None)):
    return service.extract_benefit_rules(req, idempotency_key)


@router.post("/rule-versions", status_code=201, dependencies=[Depends(require_admin)])    # C-11
def activate_rules(req: RuleVersionRequest, idempotency_key: str | None = Header(default=None)):
    return service.activate_rules(req, idempotency_key)
