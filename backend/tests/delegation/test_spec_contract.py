"""계약 테스트: 팀 공유 명세서(엑셀)의 D 예시를 그대로 보내 D가 같은 모양으로 답하는지 확인한다.

spec_examples.json은 명세서에서 뽑은 D-01~D-09, C-05·C-02 예시다.
명세가 바뀌면 이 파일을 다시 뽑고, 이 테스트가 깨지면 코드나 명세 중 하나가 어긋난 것이다.
→ 각자 개발한 모듈을 합쳤을 때 필드 이름·모양 때문에 깨지는 일을 미리 막는다.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.clock import KST
from app.core.db import get_session
from app.main import create_app
from app.modules.delegation.benefits import CBenefitPort
from app.modules.delegation.router import get_benefit_port, get_service, get_service_token
from app.modules.delegation.service import DelegationService

SPEC = json.loads((Path(__file__).parent / "spec_examples.json").read_text())["apis"]
KEY = "test-service-key"
SPEC_NOW = datetime(2026, 11, 13, 10, 40, tzinfo=KST)  # 명세 예시 견적(11:00 만료) 직전


def keys(obj) -> set[str]:
    return set(obj)


@pytest.fixture
def spec_c_port():
    """C를 명세 C-05·C-02 예시 응답으로 흉내 낸다 → D가 C의 실제 응답 모양으로 동작하는지 확인."""
    calls = []

    def calculate(body):
        calls.append(body)
        assert set(body) == set(SPEC["C-05"]["request"]), "D가 보내는 C-05 요청이 명세와 다름"
        return SPEC["C-05"]["response"]

    def list_methods(user_id):
        return SPEC["C-02"]["response"]

    port = CBenefitPort(calculate=calculate, list_methods=list_methods)
    port.calls = calls
    return port


@pytest.fixture
def client(session_factory, spec_c_port):
    app = create_app()

    def _session():
        with session_factory() as s:
            yield s

    def _service():
        with session_factory() as s:
            yield DelegationService(s, spec_c_port, now=lambda: SPEC_NOW)

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_benefit_port] = lambda: spec_c_port
    app.dependency_overrides[get_service] = _service
    app.dependency_overrides[get_service_token] = lambda: KEY
    with TestClient(app, headers={"Authorization": f"Bearer {KEY}", "X-Request-ID": "REQ001"}) as c:
        yield c


def test_spec_examples_whole_flow(client, spec_c_port):
    """D-01 → D-02 → D-03 → D-04 → D-06 → D-08 → D-09, 명세 예시 요청 그대로."""
    # D-01
    r = client.post("/delegation/policies", json=SPEC["D-01"]["request"])
    assert r.status_code == SPEC["D-01"]["status"], r.text
    p = r.json()
    assert keys(p) == keys(SPEC["D-01"]["response"])

    # D-02: 명세 예시의 policy_id만 실제 값으로
    body = {**SPEC["D-02"]["request"], "policy_id": p["policy_id"]}
    r = client.post("/delegation/validations/orders", json=body)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["valid"], v
    assert keys(v) == keys(SPEC["D-02"]["response"])
    assert spec_c_port.calls, "D-02가 C-05를 부르지 않음"

    # D-03
    r = client.post(
        f"/delegation/approvals/{v['approval_id']}/confirm",
        json={**SPEC["D-03"]["request"], "proposal_hash": v["proposal_hash"]},
    )
    assert r.status_code == 200, r.text
    c = r.json()
    assert keys(c) == keys(SPEC["D-03"]["response"])

    # D-04
    r = client.get(f"/delegation/approvals/{v['approval_id']}")
    assert r.status_code == 200
    assert keys(r.json()) == keys(SPEC["D-04"]["response"])

    # D-06: E가 E-01 요청 값 그대로 넘긴다고 가정 (E-01 요청 ⊇ D-06 요청)
    e01 = SPEC["E-01"]["request"]
    d06 = {k: e01[k] for k in SPEC["D-06"]["request"]}
    d06.update(
        execution_id=c["execution_id"],
        approval_id=v["approval_id"],
        proposal_hash=v["proposal_hash"],
    )
    r = client.post("/delegation/authorizations/check", json=d06)
    assert r.status_code == 200
    a = r.json()
    assert a["allowed"], a
    assert keys(a) == keys(SPEC["D-06"]["response"])

    # D-08: O가 E-01 응답 값을 그대로 넘긴다 (이름이 같아야 함)
    e01r = SPEC["E-01"]["response"]
    d08 = {k: e01r[k] for k in SPEC["D-08"]["request"]}
    d08["execution_id"] = c["execution_id"]
    r = client.post("/delegation/payments/results", json=d08)
    assert r.status_code == 200, r.text
    assert keys(r.json()) == keys(SPEC["D-08"]["response"])

    # D-09
    r = client.get("/delegation/audit-logs", params={"request_id": "REQ001"})
    assert r.status_code == 200
    page = r.json()
    assert keys(page) == keys(SPEC["D-09"]["response"])
    assert keys(page["results"][0]) == keys(SPEC["D-09"]["response"]["results"][0])
    assert [x["event_type"] for x in page["results"]] == [
        "POLICY_REGISTERED",
        "ORDER_VALIDATED",
        "APPROVAL_CONFIRMED",
        "PAYMENT_CHECK",
        "PAYMENT_RESULT",
    ]


def test_spec_revoke_and_health(client):
    p = client.post("/delegation/policies", json=SPEC["D-01"]["request"]).json()
    v = client.post(
        "/delegation/validations/orders",
        json={**SPEC["D-02"]["request"], "policy_id": p["policy_id"]},
    ).json()
    r = client.post(
        f"/delegation/approvals/{v['approval_id']}/revoke", json=SPEC["D-05"]["request"]
    )
    assert r.status_code == 200
    assert keys(r.json()) == keys(SPEC["D-05"]["response"])
    h = client.get("/delegation/health", headers={"Authorization": ""})
    assert h.status_code == 200 and keys(h.json()) == keys(SPEC["D-07"]["response"])


def test_spec_error_codes_are_listed(client):
    """D가 실제로 내는 에러 코드가 명세 실패 칸에 적혀 있는지 (대표 경우)."""
    cases = [
        (
            "D-02",
            client.post(
                "/delegation/validations/orders",
                json={**SPEC["D-02"]["request"], "policy_id": "NOPE"},
            ),
        ),
        ("D-04", client.get("/delegation/approvals/NOPE")),
        (
            "D-08",
            client.post(
                "/delegation/payments/results",
                json={**SPEC["D-08"]["request"], "execution_id": "NOPE"},
            ),
        ),
        ("D-09", client.get("/delegation/audit-logs", params={"page_size": 101})),
        (
            "D-06",
            client.post(
                "/delegation/authorizations/check",
                json=SPEC["D-06"]["request"],
                headers={"Authorization": "Bearer wrong"},
            ),
        ),
    ]
    for api_id, res in cases:
        body = res.json()
        assert set(body) == {"error", "request_id"} and body["request_id"] == "REQ001"
        assert body["error"]["code"] in SPEC[api_id]["error_codes"], (api_id, body["error"]["code"])


def test_auth_matches_spec(client):
    """명세의 인증 칸대로: health만 토큰 없이, 나머지는 서비스 토큰."""
    no_auth = {"Authorization": ""}
    for api_id, info in SPEC.items():
        if not api_id.startswith("D-"):
            continue
        path = info["endpoint"].replace("{approval_id}", "X")
        res = client.request(info["method"], path, json=info["request"], headers=no_auth)
        if info["auth"] == "none":
            assert res.status_code == 200, api_id
        else:
            assert res.status_code == 401, (api_id, res.status_code)
