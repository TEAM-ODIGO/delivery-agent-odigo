"""시간 도구. 모든 시각은 한국 시간(+09:00)을 붙인 '시간대 있는' datetime으로 다룬다."""

from datetime import UTC, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), name="KST")


def now() -> datetime:
    """현재 시각 (KST). 테스트에서는 이 함수 대신 고정된 시각을 직접 넘긴다."""
    return datetime.now(KST)


def to_kst(dt: datetime) -> datetime:
    """어떤 시각이든 KST로 맞춘다. 시간대가 없으면 UTC로 저장된 값으로 본다."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(KST)
