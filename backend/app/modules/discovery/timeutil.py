"""A에서 쓰는 시간 도우미 (공통 규칙: ISO 8601 + 시간대 +09:00)."""

from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(KST)


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(KST).isoformat(timespec="seconds")
