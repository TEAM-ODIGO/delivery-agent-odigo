"""HTTP 테스트: 라우터·에러 형식·서버 간 키·동시 요청."""

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.db import get_session
from app.main import create_app
from app.modules.delegation import audit
from app.modules.delegation.models import Execution
from app.modules.delegation.router import get_benefit_port, get_service, get_service_token
from app.modules.delegation.schemas import AuthorizationCheckRequest
from app.modules.delegation.service import DelegationService
from tests.conftest import T0, approve, make_policy, make_quote

KEY = "test-service-key"


@pytest.fixture
def client(session_factory, benefits):
    app = create_app()

    def _session():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _session

    def _service():
        # 실제 시계를 쓰면 예시 견적(T0+30분 만료)이 시간이 지나 만료된다 → 고정 시계
        with session_factory() as s:
            yield DelegationService(s, benefits, now=lambda: T0)

    app.dependency_overrides[get_benefit_port] = lambda: benefits
    app.dependency_overrides[get_service] = _service
    app.dependency_overrides[get_service_token] = lambda: KEY
    with TestClient(
        app, headers={"Authorization": f"Bearer {KEY}"}
    ) as c:  # lifespan(init_db)은 기본 DB용이라 테스트 DB에는 영향 없음
        yield c


def full_flow_until_approved(client):
    policy = client.post("/delegation/policies", json=make_policy().model_dump(mode="json"))
    assert policy.status_code == 201
    p = policy.json()
    validated = client.post(
        "/delegation/validations/orders",
        json={
            "request_id": p["request_id"],
            "user_id": "U001",
            "policy_id": p["policy_id"],
            "policy_version": p["policy_version"],
            "approval_target_id": "target_001",
            "order_version": 1,
            "quote": make_quote(),
        },
    ).json()
    assert validated["valid"], validated
    confirmed = client.post(
        f"/delegation/approvals/{validated['approval_id']}/confirm",
        json={"user_id": "U001", "proposal_hash": validated["proposal_hash"]},
    ).json()
    return validated, confirmed


def pay_body(validated, confirmed, **over):
    body = {
        "execution_id": confirmed["execution_id"],
        "approval_id": validated["approval_id"],
        "quote_id": "QT001",
        "proposal_hash": validated["proposal_hash"],
        "charge_krw": 33000,
        "payment_method_id": "CARD001",
    }
    body.update(over)
    return body


def test_full_flow_over_http(client):
    validated, confirmed = full_flow_until_approved(client)
    r = client.post(
        "/delegation/authorizations/check",
        json=pay_body(validated, confirmed),
        headers={"Authorization": f"Bearer {KEY}", "X-Request-ID": "REQ001"},
    )
    assert r.status_code == 200 and r.json()["allowed"] is True
    assert (
        client.get(f"/delegation/approvals/{validated['approval_id']}").json()["status"]
        == "CONSUMED"
    )


@pytest.mark.parametrize(
    "headers", [{"Authorization": ""}, {"Authorization": "Bearer wrong"}, {"Authorization": KEY}]
)
def test_d06_requires_service_key(client, headers):
    validated, confirmed = full_flow_until_approved(client)
    r = client.post(
        "/delegation/authorizations/check", json=pay_body(validated, confirmed), headers=headers
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED_SERVICE"


def test_every_d_api_except_health_needs_token(client):
    body = make_policy().model_dump(mode="json")
    r = client.post("/delegation/policies", json=body, headers={"Authorization": ""})
    assert r.status_code == 401
    assert client.get("/delegation/approvals/x", headers={"Authorization": ""}).status_code == 401
    assert client.get("/delegation/health", headers={"Authorization": ""}).status_code == 200


def test_unknown_action_is_rejected_at_the_door(client):
    body = make_policy().model_dump(mode="json")
    body["delegation"]["allowed_actions"] = ["SEARCH", "TRANSFER_MONEY"]
    r = client.post("/delegation/policies", json=body)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "TRANSFER_MONEY" not in r.text  # 입력값을 응답에 되돌려 주지 않는다


def test_extra_fields_rejected(client):
    body = make_policy().model_dump(mode="json") | {"approved": True}
    assert client.post("/delegation/policies", json=body).status_code == 400


def test_not_found_uses_common_error_format(client):
    r = client.get("/delegation/approvals/nope", headers={"X-Request-ID": "REQ777"})
    assert r.status_code == 404
    assert r.json() == {
        "error": {
            "code": "APPROVAL_NOT_FOUND",
            "message": "승인 정보를 찾을 수 없습니다.",
            "details": {},
        },
        "request_id": "REQ777",  # X-Request-ID를 그대로 돌려줌
    }
    # 형식이 이상한 헤더 값은 되돌려 주지 않는다
    bad = client.get("/delegation/approvals/nope", headers={"X-Request-ID": "<script>"})
    assert bad.json()["request_id"] is None


def test_accepts_constraints_from_a_but_drops_address(client, session_factory):
    body = make_policy().model_dump(mode="json")
    body["constraints"] |= {
        "delivery_address": "역삼동 회사",
        "max_delivery_minutes": 40,
        "menu_keywords": ["치킨"],
    }
    r = client.post("/delegation/policies", json=body)
    assert r.status_code == 201
    from app.modules.delegation.models import Policy

    with session_factory() as s:
        stored = s.get(Policy, r.json()["policy_id"]).constraints
    assert stored["max_delivery_minutes"] == 40
    assert "delivery_address" not in stored


def test_health(client):
    assert client.get("/delegation/health").json() == {"service": "delegation", "status": "UP"}


def test_concurrent_payment_checks_claim_once(session_factory, benefits):
    """같은 결제 요청 8개가 동시에 와도 실행 권한 행은 1개, 로그 사슬은 온전하다."""
    with session_factory() as s:
        validated, confirmed = approve(DelegationService(s, benefits, now=lambda: T0))
    req = AuthorizationCheckRequest(
        execution_id=confirmed.execution_id,
        approval_id=validated.approval_id,
        quote_id="QT001",
        proposal_hash=validated.proposal_hash,
        charge_krw=33000,
        payment_method_id="CARD001",
    )
    results, errors = [], []
    barrier = threading.Barrier(8)

    def worker():
        with session_factory() as s:
            barrier.wait()
            try:
                results.append(
                    DelegationService(s, benefits, now=lambda: T0).check_authorization(req).allowed
                )
            except Exception as exc:  # 실패는 '허용 아님'으로 취급되어야 한다
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Execution)) == 1
        assert audit.verify_chain(s) == (True, None)
    print(f"\n허용 {sum(results)}건, 차단 {len(results) - sum(results)}건, 예외 {len(errors)}건")
    assert any(results)  # 최소 한 번은 허용됨 (같은 execution_id라 재응답도 허용)
