"""Test fixtures: isolated `davinci_test` schema in the same Postgres.

Strategy: override DATABASE_SCHEMA before app imports so all models bind to
`davinci_test`, then replace `app.db.engine` / `SessionLocal` with a NullPool
engine that bakes `search_path = davinci_test, public` into every new asyncpg
connection via `server_settings`.
"""
import os
import uuid
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio

TEST_SCHEMA = "davinci_test"

os.environ["DATABASE_SCHEMA"] = TEST_SCHEMA
os.environ.setdefault("OWNER_OPEN_ID", "email:owner@davinci-test.com")

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

import app.db as _db  # noqa: E402
from app.config import get_settings  # noqa: E402

# Build a test engine that pins search_path at the asyncpg connection level.
# NullPool guarantees every checkout opens a fresh connection — no chance of
# inheriting a pre-cached statement tied to the wrong type OID.
_test_engine = create_async_engine(
    get_settings().database_url,
    echo=False,
    poolclass=NullPool,
    connect_args={
        "server_settings": {
            "search_path": f"{TEST_SCHEMA},public",
            "jit": "off",
        },
    },
)
_test_session = async_sessionmaker(_test_engine, expire_on_commit=False, class_=AsyncSession)

# Swap the module-level objects so `get_session`, `session_scope`, etc. all
# resolve to the test engine via attribute lookup at call time.
_db.engine = _test_engine
_db.SessionLocal = _test_session

from app.deps.auth import (  # noqa: E402
    get_current_user,
    require_active_user,
    require_admin,
    require_user,
)
from app.main import app  # noqa: E402
from app.models import Base, User, UserRole, UserStatus  # noqa: E402


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_schema():
    """Drop+recreate the test schema, manually create the PG enums (the User
    model declares enums with `create_type=False`), then run `create_all`."""
    enums = {
        "user_role": ("admin", "user"),
        "user_status": ("pending", "active", "suspended"),
        "marketplace": (
            "ml", "shopee", "amazon", "aliexpress",
            "temu", "tiktok", "shein", "magalu", "site",
        ),
        "store_status": (
            "active", "inactive", "closing", "banned", "pending", "under_review",
        ),
        "cadastro_tipo": ("fone", "email", "dominio"),
        "cadastro_status": ("active", "inactive", "excluded"),
        "integration_platform": ("bling", "ml", "shopee", "amazon"),
    }
    async with _test_engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
        await conn.execute(text(f'CREATE SCHEMA "{TEST_SCHEMA}"'))
        for name, values in enums.items():
            vals = ", ".join(f"'{v}'" for v in values)
            await conn.execute(
                text(f'CREATE TYPE "{TEST_SCHEMA}".{name} AS ENUM ({vals})')
            )
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
    await _test_engine.dispose()


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    async with _test_session() as session:
        yield session


_CLEANUP_TABLES = (
    "oauth_states",
    "cadastros_stores",
    "cadastros",
    "integrations",
    "stores",
    "companies",
    "users",
)


async def _wipe(db: AsyncSession) -> None:
    for tbl in _CLEANUP_TABLES:
        await db.execute(text(f"DELETE FROM {tbl}"))
    await db.commit()


@pytest_asyncio.fixture(autouse=True)
async def _cleanup(db: AsyncSession):
    await _wipe(db)
    yield
    await _wipe(db)


async def _make_user(
    db: AsyncSession,
    *,
    email: str | None = None,
    role: UserRole = UserRole.USER,
    status: UserStatus = UserStatus.ACTIVE,
    permissions: dict | None = None,
) -> User:
    email = email or f"u-{uuid.uuid4().hex[:8]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=role,
        status=status,
        permissions=permissions or {},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.fixture
def make_user(db: AsyncSession):
    async def _f(**kwargs) -> User:
        return await _make_user(db, **kwargs)

    return _f


def _override_as(user: User) -> None:
    async def _get_current():
        return user

    async def _require():
        return user

    app.dependency_overrides[get_current_user] = _get_current
    app.dependency_overrides[require_user] = _require
    app.dependency_overrides[require_active_user] = _require
    app.dependency_overrides[require_admin] = _require


@pytest.fixture
def auth_as() -> Callable[[User | None], None]:
    def setter(user: User | None):
        if user is None:
            app.dependency_overrides.clear()
            return
        _override_as(user)
        if user.role != UserRole.ADMIN:
            app.dependency_overrides.pop(require_admin, None)

    yield setter
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
