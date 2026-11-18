"""주문안 해시: 사용자가 '무엇을' 승인했는지를 64자리 지문 하나로 고정한다.

같은 내용이면 언제 계산해도 같은 값이 나와야 하므로
JSON 키를 정렬하고 공백을 없앤 '정규화 JSON'을 만든 뒤 sha256을 씌운다.
값이 1원이라도 바뀌면 완전히 다른 해시가 된다.
"""

import hashlib
import json
from typing import Any

from app.modules.delegation.schemas import OrderValidationRequest


def canonical_json(data: Any) -> str:
    """키 순서·공백과 상관없이 같은 내용이면 같은 문자열."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def proposal_payload(req: OrderValidationRequest) -> dict[str, Any]:
    """해시에 들어가는 내용 = 누가(user) · 어떤 요청에서 · 어떤 조건 버전으로 · 어떤 견적을.

    조건 버전을 넣어 두면, 조건이 바뀐 뒤 옛 승인으로 결제하려는 시도도 해시로 막힌다.
    """
    return req.model_dump(mode="json")


def proposal_hash(req: OrderValidationRequest) -> str:
    return "sha256:" + sha256_hex(canonical_json(proposal_payload(req)))
