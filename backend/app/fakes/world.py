"""가짜 A·B·C·E를 한 묶음으로 만든다. 같은 시나리오 장부와 같은 C를 함께 쓴다.

C는 benefit 인자로 진짜 모듈(facade)을 받을 수 있다.
D와 연결이 필요한 두 곳은 함수로 받는다 (D는 진짜 모듈):
- read_approval(approval_id) → D-04 결과 dict        (B-07이 승인 상태를 읽을 때)
- check_authorization(body, request_id) → D-06 결과 dict  (E-01이 결제 직전에 물을 때)
"""

from app.core import clock
from app.fakes.benefit import FakeBenefit
from app.fakes.discovery import FakeDiscovery
from app.fakes.ordering import ApprovalReader, FakeOrdering
from app.fakes.payment import CheckAuthorizationFn, FakePayment
from app.fakes.scenario import ScenarioBook


class FakeWorld:
    def __init__(
        self,
        *,
        read_approval: ApprovalReader | None = None,
        check_authorization: CheckAuthorizationFn | None = None,
        benefit=None,
        discovery=None,
        ordering=None,
        now=clock.now,
    ):
        self.book = ScenarioBook()
        self.discovery = discovery or FakeDiscovery(self.book)
        # benefit: 진짜 C(facade)를 끼울 때 넘긴다. 없으면 가짜 C
        self.benefit = benefit or FakeBenefit(now=now)
        self.ordering = ordering or FakeOrdering(
            self.book, self.benefit.calculate, read_approval, now=now
        )
        self.payment = FakePayment(self.book, check_authorization or _deny_all, now=now)


def _deny_all(body, request_id):
    return {
        "execution_id": body["execution_id"],
        "allowed": False,
        "reason_codes": ["DELEGATION_NOT_CONNECTED"],
    }
