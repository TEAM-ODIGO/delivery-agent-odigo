"""O가 쓸 A·B·C·D·E를 고르는 곳 (조립).

지금 (MVP 1 개발 중)
- A·B: 진짜 모듈 (app/modules/discovery·ordering의 facade).
  USE_FAKE_A=1 / USE_FAKE_B=1이면 가짜로 되돌린다.
  가짜 B는 가짜 A의 식당(STORE101…), 진짜 B는 진짜 A의 식당을 쓰므로 짝을 맞춰 쓴다.
- C: 진짜 모듈 (app/modules/benefit/facade.py). USE_FAKE_C=1이면 가짜 C로 되돌린다.
- D: 진짜 모듈. D가 혜택을 다시 확인할 때(C-05·C-02)도 같은 C를 쓴다 → 금액 기준이 하나.
- E: E_PAYMENT_URL이 있으면 HTTP(진짜 E 또는 scripts/fake_e_server.py),
  없으면 같은 프로세스의 가짜 E.
  가짜 E도 결제 직전에 진짜 D-06을 부른다.
"""

import os

from sqlalchemy.orm import Session

from app.core.clock import now as clock_now
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.fakes import FakeWorld
from app.fakes.scenario import split_tags
from app.modules.benefit import facade as benefit_facade
from app.modules.delegation import DelegationService
from app.modules.delegation.benefits import CBenefitPort
from app.modules.delegation.schemas import AuthorizationCheckRequest
from app.modules.discovery.facade import DiscoveryFacade
from app.modules.orchestrator.ports import HttpPaymentPort, PaymentPort, PaymentResponseLost
from app.modules.orchestrator.service import OrchestratorService
from app.modules.ordering import config as ordering_config
from app.modules.ordering import ports as ordering_ports
from app.modules.ordering.facade import OrderingFacade

_world: FakeWorld | None = None
_http_payment: PaymentPort | None = None


class _RealBenefit:
    """진짜 C를 O·B·D가 기대하는 모양(recommend · calculate · list_methods)으로 보여 준다."""

    recommend = staticmethod(benefit_facade.recommend)
    calculate = staticmethod(benefit_facade.calculate)
    list_methods = staticmethod(benefit_facade.list_payment_methods)


def _d_benefit_port() -> CBenefitPort:
    w = world()
    return CBenefitPort(calculate=w.benefit.calculate, list_methods=w.benefit.list_methods)


def delegation_service(session: Session) -> DelegationService:
    return DelegationService(
        session, _d_benefit_port(), approval_ttl_minutes=settings.approval_ttl_minutes
    )


def _read_approval(approval_id: str) -> dict:  # B → D-04 (읽기 전용)
    with SessionLocal() as s:
        return delegation_service(s).get_approval(approval_id).model_dump(mode="json")


def _read_approval_for_b(approval_id: str) -> dict:
    """B-07 내부 호출. 승인 원본 해시는 D 서비스에서만 읽는다."""
    with SessionLocal() as s:
        return delegation_service(s).get_approval_for_execution(approval_id)


def _check_authorization(body: dict, request_id: str) -> dict:  # E → D-06
    with SessionLocal() as s:
        req = AuthorizationCheckRequest.model_validate(body)
        return (
            delegation_service(s)
            .check_authorization(req, request_id_hint=request_id)
            .model_dump(mode="json")
        )


class _WithScenarioTags:
    """진짜 A 앞에서 주문 문장의 #태그(#결제거절 등)를 떼어 시나리오 장부에 적는다.

    가짜 E가 장부를 보고 결제 거절·미확정 등을 흉내 낸다.
    진짜 A·B·C로도 결제 시나리오를 볼 수 있다.
    (#품절·#B08장애 같은 B 관련 태그는 가짜 B일 때만 효과가 있다. 진짜 A는 태그를 모른다.)
    """

    def __init__(self, inner, book):
        self._inner, self._book = inner, book

    def create_request(self, body):
        clean, flags = split_tags(body.get("message") or "")
        self._book.set(body["request_id"], flags)
        return self._inner.create_request({**body, "message": clean or body.get("message")})

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _ScenarioHttpPayment:
    """시연 모드에서만 주문별 태그를 별도 E의 모의 결과 헤더로 전달한다."""

    def __init__(self, inner: HttpPaymentPort, book):
        self.inner, self.book = inner, book

    def request_payment(self, body: dict) -> dict:
        request_id = body["request_id"]
        outcome = next(
            (value for flag, value in (("PAY_DECLINE", "DECLINE"), ("PAY_UNKNOWN", "UNKNOWN"))
             if self.book.has(request_id, flag)),
            None,
        )
        result = self.inner.request_payment(body, mock_outcome=outcome)
        if self.book.consume(request_id, "PAY_LOST"):
            raise PaymentResponseLost("시연용 E-01 응답 유실")
        return result

    def get_by_execution(self, execution_id: str) -> dict:
        return self.inner.get_by_execution(execution_id)


class _BenefitForB:
    """B → C-05. 진짜·가짜 C의 calculate(dict)를 B의 포트 모양으로 감싼다.

    C가 4xx로 거절하면 DependencyRejected, 그 밖의 실패는 DependencyUnavailable(503).
    """

    def __init__(self, calculate):
        self._calculate = calculate

    def calculate(self, body: dict) -> dict:
        try:
            return self._calculate(body)
        except AppError as exc:
            if exc.status < 500:
                raise ordering_ports.DependencyRejected(exc.status, exc.code, exc.message) from exc
            raise ordering_ports.DependencyUnavailable(exc.code) from exc
        except (TimeoutError, ConnectionError, OSError) as exc:
            raise ordering_ports.DependencyUnavailable(type(exc).__name__) from exc


class _ApprovalForB:
    """B → D-04 (읽기 전용)."""

    def get_approval(self, approval_id: str) -> dict:
        try:
            return _read_approval_for_b(approval_id)
        except AppError as exc:
            if exc.status < 500:
                raise ordering_ports.DependencyRejected(exc.status, exc.code, exc.message) from exc
            raise ordering_ports.DependencyUnavailable(exc.code) from exc


def real_ordering(calculate, now=None) -> OrderingFacade:
    """진짜 B(김민지 버전)를 O의 OrderingPort 모양으로 조립한다.

    - 가격·품절·배달비: 팀 공용 식당 데이터(restaurant·menu) = A와 같은 데이터 (B가 직접 읽음)
    - 혜택(C-05): `calculate(dict) -> dict` (진짜 C 또는 가짜 C)
    - 승인 상태(D-04): 진짜 D (읽기 전용)
    """
    # B는 자기 설정을 환경 변수에서 읽는다. 팀 설정과 같은 값(서비스 토큰·견적 유효시간)을 맞춰 준다
    if settings.service_token:
        os.environ.setdefault("INTERNAL_SERVICE_TOKEN", settings.service_token)
    os.environ.setdefault("ORDERING_QUOTE_TTL_SECONDS", str(settings.ordering_quote_ttl_seconds))
    ordering_config.get_settings.cache_clear()
    benefit, approvals = _BenefitForB(calculate), _ApprovalForB()
    # /ordering/... 로 B를 HTTP로 직접 부를 때도 같은 C·D를 쓰게 한다
    ordering_ports.configure(benefit=benefit, approvals=approvals)
    return OrderingFacade(SessionLocal, benefit, approvals, now or clock_now)


def world() -> FakeWorld:
    global _world
    if _world is None:
        w = FakeWorld(
            read_approval=_read_approval,
            check_authorization=_check_authorization,
            benefit=None if settings.use_fake_c else _RealBenefit(),
        )
        if not settings.use_fake_a:
            w.discovery = _WithScenarioTags(DiscoveryFacade(), w.book)
        if not settings.use_fake_b:
            w.ordering = real_ordering(w.benefit.calculate)
        _world = w
    return _world


def payment_port() -> PaymentPort:
    global _http_payment
    if settings.e_payment_url:
        if _http_payment is None:
            http = HttpPaymentPort(settings.e_payment_url, settings.service_token)
            demo = os.getenv("O_DEMO_E_OVERRIDE", "").lower() in ("1", "true")
            _http_payment = _ScenarioHttpPayment(http, world().book) if demo else http
        return _http_payment
    return world().payment


def orchestrator_service(session: Session, d_session: Session) -> OrchestratorService:
    w = world()
    return OrchestratorService(
        session,
        discovery=w.discovery,
        ordering=w.ordering,
        benefit=w.benefit,
        payment=payment_port(),
        delegation=delegation_service(d_session),
        retry_delay=settings.orchestrator_retry_delay,
    )
