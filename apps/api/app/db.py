from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    # `server_settings.application_name` é honrado pelo asyncpg na
    # negociação de cada conexão. Aparece em pg_stat_activity e fica
    # disponível via `current_setting('application_name', true)` dentro
    # de triggers (audit_em_andamento_data_fn — migration 0135),
    # identificando qual processo escreveu sem precisar rastrear PID.
    connect_args={
        "server_settings": {"application_name": _settings.app_name},
    },
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
