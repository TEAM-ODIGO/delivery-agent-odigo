"""시나리오 태그: 주문 문장에 넣으면 가짜 모듈이 그 상황을 흉내 낸다.

예) "둘이서 2만원 이하로 안 매운 치킨 #품절"
    → B-01이 첫 번째로 고른 후보를 품절로 답한다.

태그는 가짜 A가 A-01에서 읽어 request_id별로 기억하고, 가짜 B·E가 같은 장부를 본다.
"""

import re
from dataclasses import dataclass, field

TAGS: dict[str, str] = {
    "#품절": "SOLDOUT_FIRST",  # B-01: 처음 고른 후보만 품절 → 남은 후보로 다시 선택
    "#모두품절": "SOLDOUT_ALL",  # B-01: 모든 후보 품절 → FAILED(NO_CANDIDATE)
    "#혜택불일치": "B05_STALE",  # B-05: 409 STALE_CALCULATION → BLOCKED
    "#결제거절": "PAY_DECLINE",  # E-01: 모의 기관 거절 → FAILED
    "#결제미확정": "PAY_UNKNOWN",  # E-01: UNKNOWN → 나중에 E-03에서 APPROVED
    "#응답유실": "PAY_LOST",  # E-01: E는 결제했지만 O가 응답을 못 받음 → E-03으로 확인
    "#B08장애": "B08_FAIL_ONCE",  # B-08: 첫 전달 실패 → 전달 대기로 남았다가 재전송
    "#A장애": "A_DOWN",  # A-01: 계속 503 → O-01 503 DEPENDENCY_UNAVAILABLE
}

_TAG_RE = re.compile(r"#\S+")


def split_tags(message: str) -> tuple[str, set[str]]:
    """문장에서 태그를 떼어 내고 (남은 문장, 플래그 집합)을 돌려준다. 모르는 태그는 무시."""
    flags = {TAGS[t] for t in _TAG_RE.findall(message) if t in TAGS}
    clean = _TAG_RE.sub("", message).strip()
    return re.sub(r"\s{2,}", " ", clean), flags


@dataclass
class ScenarioBook:
    """request_id별 시나리오 플래그. 가짜 A·B·C·E가 함께 본다."""

    flags: dict[str, set[str]] = field(default_factory=dict)

    def set(self, request_id: str, flags: set[str]) -> None:
        self.flags[request_id] = set(flags)

    def has(self, request_id: str | None, flag: str) -> bool:
        return bool(request_id) and flag in self.flags.get(request_id, set())

    def consume(self, request_id: str | None, flag: str) -> bool:
        """한 번만 일어나는 상황: 있으면 지우고 True."""
        if self.has(request_id, flag):
            self.flags[request_id].discard(flag)
            return True
        return False
