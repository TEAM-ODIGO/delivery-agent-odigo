"""모놀리스 서버의 시작점. 모듈별 router를 여기서 연결한다.

실행: uv run uvicorn app.main:app --reload --port 8000
문서: http://localhost:8000/docs  (Swagger, 자동 생성)
"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.db import SessionLocal, engine, init_db
from app.core.errors import install_error_handlers
from app.modules.benefit.catalog_source import use_db_restaurants, use_shared_restaurants
from app.modules.benefit.router import router as benefit_router
from app.modules.delegation.router import router as delegation_router
from app.modules.discovery.hours import ensure_column as ensure_hours_column
from app.modules.discovery.hours import sync_from_csv as sync_hours
from app.modules.discovery.loader import find_restaurant_csv, load_if_empty, sync_if_newer
from app.modules.discovery.router import router as discovery_router
from app.modules.orchestrator.router import router as orchestrator_router
from app.modules.ordering.router import router as ordering_router

log = logging.getLogger("app")
if not log.handlers:  # 따로 설정이 없으면 "app" 로그(INFO)도 uvicorn 로그처럼 터미널에 보이게
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)


def reconcile_once() -> dict:
    """결제 결과를 모르는 주문·B-08 전달 대기를 마저 처리 (서버 시작 시 + 주기적으로)."""
    from app.modules.orchestrator.wiring import orchestrator_service

    with SessionLocal() as s, SessionLocal() as d:
        return orchestrator_service(s, d).reconcile_pending()


async def _reconcile_loop(seconds: float) -> None:
    while True:
        try:
            await run_in_threadpool(reconcile_once)
        except Exception:  # 한 번 실패해도 다음 주기에 다시
            log.exception("reconcile 실패")
        await asyncio.sleep(seconds)


def prepare_catalog() -> None:
    """식당·메뉴 데이터(요기요 수집본)를 비어 있으면 적재하고, C가 같은 데이터를 읽게 연결한다."""
    if settings.use_fake_a and settings.use_fake_b:
        return  # 가짜 식당(app/fakes/catalog.py)을 쓰는 구성
    ensure_hours_column(engine)  # 예전 DB에 영업시간 열 추가
    with SessionLocal() as s:
        if load_if_empty(s) is None:
            sync_if_newer(s)  # 재수집한 CSV가 더 새로우면 DB에 맞춤
        sync_hours(s, find_restaurant_csv())  # CSV의 영업시간을 DB에 맞춤
    use_db_restaurants(SessionLocal)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    prepare_catalog()
    task = None
    if settings.reconcile_seconds > 0:
        task = asyncio.create_task(_reconcile_loop(settings.reconcile_seconds))
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    app = FastAPI(title="Delivery Agent (모듈형 모놀리스)", lifespan=lifespan)
    install_error_handlers(app)

    app.include_router(orchestrator_router)
    app.include_router(delegation_router)
    app.include_router(discovery_router)  # A: /discovery/... (점검용 직접 호출. O는 함수로 호출)
    app.include_router(ordering_router)  # B: /ordering/... (점검용 직접 호출. O는 함수로 호출)
    app.include_router(benefit_router)  # C: /benefit/... (관리자·점검용 직접 호출 + 모듈 간 함수 호출)
    use_shared_restaurants()  # C가 팀 공용 식당 데이터를 읽도록 연결

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/docs")  # 첫 화면 = Swagger

    return app


app = create_app()
