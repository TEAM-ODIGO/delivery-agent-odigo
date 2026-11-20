"""DB 연결. 기본 SQLite 파일, PostgreSQL이면 E_DATABASE_URL만 바꾼다."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import JSON, DateTime, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from e_payment.config import settings

KST = timezone(timedelta(hours=9))
JsonType = JSON().with_variant(JSONB(), "postgresql")


def now() -> datetime:
    return datetime.now(KST)


class Base(DeclarativeBase):
    pass


class KstDateTime(TypeDecorator):
    """DB에는 UTC로 저장하고 읽을 때 KST(+09:00)로 돌려준다 (SQLite·PostgreSQL 동일 동작)."""

    impl = DateTime(timezone=False)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("시간대가 없는 datetime은 저장할 수 없습니다.")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=UTC).astimezone(KST)


def make_engine(url: str):
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    return create_engine(url, **kwargs)


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with SessionLocal() as s:
        yield s
