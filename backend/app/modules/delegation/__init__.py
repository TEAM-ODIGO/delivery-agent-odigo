"""D · 위임·승인·Agent 통제 모듈.

다른 모듈(O 등)은 여기서 내보내는 것만 쓴다:
    from app.modules.delegation import DelegationService, schemas
"""

from app.modules.delegation import schemas
from app.modules.delegation.service import DelegationService

__all__ = ["DelegationService", "schemas"]
