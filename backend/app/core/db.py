"""DB 연결과 공용 타입.

- 모듈마다 models.py에서 Base를 상속해 테이블을 정의한다.
- 테이블 이름 앞에 모듈 이름을 붙인다 (delegation_approval, orchestrator_run …).
"""

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, MetaData, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.core.clock import to_kst
from app.core.config import settings


class Base(DeclarativeBase):
    # 제약 이름 규칙 (B의 마이그레이션이 이 규칙으로 만들어져 있어 같은 규칙을 쓴다)
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


# PostgreSQL에서는 JSONB, SQLite에서는 JSON으로 저장된다.
JsonType = JSON().with_variant(JSONB(), "postgresql")


class KstDateTime(TypeDecorator):
    """시각을 DB에는 UTC로 저장하고, 읽을 때는 KST로 돌려준다.

    SQLite는 시간대 정보를 버리기 때문에, 이 타입으로 통일해 두면
    SQLite(개발)와 PostgreSQL(시연)에서 똑같이 동작한다.
    """

    impl = DateTime(timezone=False)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("시간대가 없는 datetime은 저장할 수 없습니다.")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        return None if value is None else to_kst(value)


def make_engine(url: str):
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI 의존성: 요청 하나에 세션 하나."""
    with SessionLocal() as session:
        yield session


def init_db(bind=None) -> None:
    """테이블 생성. MVP 1은 create_all로 충분하고, 스키마가 안정되면 Alembic으로 바꾼다."""
    import app.modules.cart.models  # noqa: F401  (요기요 장바구니 작업)
    import app.modules.delegation.models  # noqa: F401  (테이블 등록용 import)
    import app.modules.discovery.catalog_models  # noqa: F401  (restaurant·menu)
    import app.modules.discovery.models  # noqa: F401  (A)
    import app.modules.livecheck.models  # noqa: F401  (요기요 실시간 식당 확인)
    import app.modules.orchestrator.models  # noqa: F401
    import app.modules.ordering.models  # noqa: F401  (B)

    Base.metadata.create_all(bind or engine)
