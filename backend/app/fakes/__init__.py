"""가짜 A(탐색)·B(주문)·C(혜택)·E(결제).

진짜 모듈이 생기기 전에 O와 D를 끝까지 돌려 보기 위한 대역이다.
- 요청·응답 모양은 팀 API 명세서(2026-11-15 v2)의 예시를 그대로 따른다.
- 데이터는 메모리에만 있다 (서버를 다시 켜면 사라짐).
- 주문 문장에 '#태그'를 넣으면 실패 상황을 고를 수 있다 (scenario.py).

모듈별 진짜 구현이 생기면 app/modules/orchestrator/wiring.py에서 하나씩 바꿔 끼운다.
"""

from app.fakes.world import FakeWorld

__all__ = ["FakeWorld"]
