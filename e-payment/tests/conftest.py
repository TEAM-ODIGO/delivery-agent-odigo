import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from e_payment.config import Settings
from e_payment.db import Base, make_engine
from e_payment.main import create_app

TOKEN = "test-token"
H = {"Authorization": f"Bearer {TOKEN}", "X-Request-ID": "REQ001"}


class FakeD:
    """D-06 대역. allowed / reasons / raises 를 테스트에서 바꾼다."""

    def __init__(self):
        self.allowed, self.reasons, self.raises = True, [], False
        self.calls = []

    def check(self, body, request_id):
        self.calls.append((body, request_id))
        if self.raises:
            raise ConnectionError("D 응답 없음")
        return {"execution_id": body["execution_id"], "allowed": self.allowed, "reason_codes": self.reasons}


@pytest.fixture
def d():
    return FakeD()


@pytest.fixture
def engine(tmp_path):
    url = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'e.db'}"
    eng = make_engine(url)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def make_client(engine, d, **cfg):
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    base = dict(service_token=TOKEN, mock_mode="APPROVE", mock_refund_mode="REFUNDED",
                allow_mock_override=False, reconcile_on_read=True, reconcile_seconds=0)
    settings = Settings(**{**base, **cfg})
    app = create_app(settings, d, sessions)
    return TestClient(app)


@pytest.fixture
def client(engine, d):
    with make_client(engine, d) as c:  # with → 시드 적재(lifespan)
        yield c


@pytest.fixture
def make(engine, d):
    clients = []

    def _make(**cfg):
        c = make_client(engine, d, **cfg)
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


def pay_body(**over):
    body = {
        "request_id": "REQ001", "user_id": "U001", "execution_id": "EXE-" + uuid.uuid4().hex[:10],
        "approval_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7", "quote_id": "QT001",
        "proposal_hash": "sha256:" + "a" * 64, "payment_method_id": "CARD001", "charge_krw": 18000,
    }
    body.update(over)
    return body


@pytest.fixture
def pay(client):
    def _pay(body=None, headers=None, **over):
        body = body or pay_body(**over)
        h = {**H, "Idempotency-Key": body["execution_id"], **(headers or {})}
        return client.post("/payments", json=body, headers=h)

    return _pay
