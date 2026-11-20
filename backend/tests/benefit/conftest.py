"""C 모듈 테스트 준비물. C 라우터만 붙인 작은 앱으로 시험한다 (DB·다른 모듈 없이).

토큰은 팀 설정(settings)을 테스트용 값으로 바꿔 쓴다. 식당은 C 시드 데이터로 고정한다.
"""

from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import install_error_handlers
from app.modules.benefit import auth, ledger, seed, service, sources
from app.modules.benefit.router import router

SERVICE = {"Authorization": "Bearer dev-service-token"}
ADMIN = {"Authorization": "Bearer dev-admin-token"}


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(
        auth,
        "settings",
        replace(auth.settings, service_token="dev-service-token", admin_access_token="dev-admin-token"),
    )

    def reset():
        seed._INITIAL["restaurants"] = dict(seed.DEFAULT_RESTAURANTS)  # 공용 식당 데이터 연결과 무관하게 고정
        service.reset_state()
        ledger.set_ledger(None)
        sources.set_merchant_source(sources.MockMerchantSource())
        sources.set_inference(sources.UnavailableInference())

    reset()
    yield
    reset()


@pytest.fixture
def client():
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def svc():
    return SERVICE


@pytest.fixture
def admin():
    return ADMIN
