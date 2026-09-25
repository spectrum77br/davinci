"""Real row-lock tests against an explicitly supplied disposable PostgreSQL.

Run with --noconftest and MAGALU_TEST_DATABASE_URL pointing at a local test
cluster. Each test creates/removes its own schema; HTTP is mocked with respx.
Schema identifiers are generated UUIDs and every credential is a test value.
"""
# ruff: noqa: S105, S608

import asyncio
import os
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import respx
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.services.marketplaces.magalu import MAGALU_API_BASE, MAGALU_TOKEN_URL, MagaluClient

TEST_DATABASE = os.environ.get("MAGALU_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.skipif(not TEST_DATABASE, reason="requires a disposable local PostgreSQL"),
    pytest.mark.asyncio(loop_scope="session"),
]


def creds(access="old-access", refresh="old-refresh", expired=False):
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": int(time.time()) + (-60 if expired else 7200),
        "client_id": "test-client",
        "client_secret": "test-client-secret",
    }


def token_response(access="new-access", refresh="new-refresh"):
    return httpx.Response(200, json={
        "access_token": access, "refresh_token": refresh, "expires_in": 7200,
    })


@pytest_asyncio.fixture(loop_scope="session")
async def database(monkeypatch):
    from app import db
    from app.models import Integration, IntegrationPlatform
    from app.security.cipher import decrypt_json, encrypt_json

    url = make_url(TEST_DATABASE)
    assert url.host in {"127.0.0.1", "localhost", "::1"}, "test database must be local"
    schema = "magalu_refresh_test_" + uuid4().hex
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        execution_options={"schema_translate_map": {Integration.__table__.schema: schema}},
        connect_args={"server_settings": {"search_path": schema + ",public"}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(
                f'CREATE TYPE "{schema}".integration_platform AS ENUM (\'magalu\', \'bling\')'
            ))
            for table in ("users", "stores"):
                await connection.execute(text(
                    f'CREATE TABLE "{schema}".{table} (id uuid PRIMARY KEY)'
                ))
            await connection.run_sync(
                lambda sync: Integration.__table__.create(sync, checkfirst=True)
            )
        monkeypatch.setattr(db, "SessionLocal", sessions)

        async def insert(value):
            async with sessions.begin() as session:
                owner_id = uuid4()
                await session.execute(
                    text(f'INSERT INTO "{schema}".users (id) VALUES (:id)'), {"id": owner_id},
                )
                integration = Integration(
                    user_id=owner_id, platform=IntegrationPlatform.MAGALU,
                    name="Isolated test", credentials=encrypt_json(value),
                )
                session.add(integration)
                await session.flush()
                return integration.id

        async def read(integration_id):
            async with sessions() as session:
                integration = await session.get(Integration, integration_id)
                return SimpleNamespace(
                    credentials=decrypt_json(integration.credentials),
                    ciphertext=integration.credentials,
                    token_expires_at=integration.token_expires_at,
                )

        yield SimpleNamespace(
            insert=insert, read=read, sessions=sessions, engine=engine, schema=schema,
        )
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def test_two_clients_rotate_once_and_commit_before_adopting(database):
    original = creds(expired=True)
    integration_id = await database.insert(original)
    entered = asyncio.Event()
    release = asyncio.Event()
    second_select = asyncio.Event()
    selections = 0
    rotations = 0

    def selecting(connection, cursor, statement, parameters, context, executemany):
        nonlocal selections
        if "FOR NO KEY UPDATE" in statement:
            selections += 1
            if selections == 2:
                second_select.set()

    event.listen(database.engine.sync_engine, "before_cursor_execute", selecting)

    async def must_not_run_callback(value):
        raise AssertionError("saved integrations must not call the caller's session callback")

    first = MagaluClient(original, must_not_run_callback, integration_id)
    second = MagaluClient(original, must_not_run_callback, integration_id)

    async def rotating(request):
        nonlocal rotations
        rotations += 1
        entered.set()
        await release.wait()
        return token_response()

    with respx.mock as router:
        route = router.post(MAGALU_TOKEN_URL).mock(side_effect=rotating)
        one = asyncio.create_task(first.refresh())
        await asyncio.wait_for(entered.wait(), 2)
        two = asyncio.create_task(second.refresh())
        try:
            await asyncio.wait_for(second_select.wait(), 2)
            assert rotations == 1
            assert first.creds == original
            release.set()
            await asyncio.wait_for(asyncio.gather(one, two), 3)
        finally:
            release.set()
            await asyncio.gather(one, two, return_exceptions=True)
            event.remove(database.engine.sync_engine, "before_cursor_execute", selecting)
        assert route.call_count == 1
    stored = await database.read(integration_id)
    assert first.creds == second.creds == stored.credentials
    assert stored.credentials["refresh_token"] == "new-refresh"
    assert b"new-refresh" not in stored.ciphertext
    assert stored.token_expires_at == datetime.fromtimestamp(first.expires_at, tz=UTC)


async def test_expired_client_adopts_fresh_database_token_without_refresh(database):
    original = creds(expired=True)
    fresh = creds("already-new", "already-rotated")
    integration_id = await database.insert(fresh)
    client = MagaluClient(original, integration_id=integration_id)
    with respx.mock(assert_all_called=False) as router:
        refresh = router.post(MAGALU_TOKEN_URL).mock(return_value=token_response())
        api = router.get(MAGALU_API_BASE + "/test").mock(return_value=httpx.Response(200))
        response = await client._request("GET", "/test")
        assert response.status_code == 200
        assert refresh.call_count == 0
        assert api.calls.last.request.headers["Authorization"] == "Bearer already-new"
    assert client.creds == fresh


@pytest.mark.parametrize("same_client", [False, True])
async def test_delayed_401_uses_the_token_sent_and_adopts_completed_rotation(database, same_client):
    original = creds()
    integration_id = await database.insert(original)
    client = MagaluClient(original, integration_id=integration_id)
    other = client if same_client else MagaluClient(original, integration_id=integration_id)
    old_request_sent = asyncio.Event()
    rotated = asyncio.Event()
    authorizations = []

    async def api_response(request):
        authorization = request.headers["Authorization"]
        authorizations.append(authorization)
        if authorization == "Bearer old-access":
            old_request_sent.set()
            await rotated.wait()
            return httpx.Response(401)
        return httpx.Response(200)

    with respx.mock as router:
        refresh = router.post(MAGALU_TOKEN_URL).mock(return_value=token_response())
        router.get(MAGALU_API_BASE + "/test").mock(side_effect=api_response)
        task = asyncio.create_task(client._request("GET", "/test"))
        try:
            await asyncio.wait_for(old_request_sent.wait(), 2)
            await other.refresh()
            rotated.set()
            assert (await asyncio.wait_for(task, 2)).status_code == 200
        finally:
            rotated.set()
            await asyncio.gather(task, return_exceptions=True)
        assert refresh.call_count == 1
    assert authorizations == ["Bearer old-access", "Bearer new-access"]
    assert client.creds == other.creds


async def test_401_on_current_token_forces_one_refresh(database):
    original = creds()
    integration_id = await database.insert(original)
    client = MagaluClient(original, integration_id=integration_id)
    with respx.mock as router:
        refresh = router.post(MAGALU_TOKEN_URL).mock(return_value=token_response())
        api = router.get(MAGALU_API_BASE + "/test").mock(side_effect=[
            httpx.Response(401), httpx.Response(200),
        ])
        assert (await client._request("GET", "/test")).status_code == 200
        assert refresh.call_count == 1
        assert api.call_count == 2


async def test_different_integrations_can_refresh_in_parallel(database):
    first_id = await database.insert(creds())
    second_id = await database.insert(creds())
    both_entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def rotating(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            both_entered.set()
        await release.wait()
        return token_response()

    with respx.mock as router:
        router.post(MAGALU_TOKEN_URL).mock(side_effect=rotating)
        tasks = [asyncio.create_task(MagaluClient(creds(), integration_id=value).refresh())
                 for value in (first_id, second_id)]
        try:
            await asyncio.wait_for(both_entered.wait(), 2)
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 3)
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert calls == 2


async def test_commit_failure_does_not_adopt_or_send_unpersisted_tokens(database):
    original = creds(expired=True)
    integration_id = await database.insert(original)
    async with database.engine.begin() as connection:
        await connection.execute(text(f'''
            CREATE FUNCTION "{database.schema}".reject_commit() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'simulated persistence failure'; END; $$
        '''))
        await connection.execute(text(f'''
            CREATE CONSTRAINT TRIGGER reject_magalu_commit AFTER UPDATE
            ON "{database.schema}".integrations DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION "{database.schema}".reject_commit()
        '''))
    client = MagaluClient(original, integration_id=integration_id)
    with respx.mock(assert_all_called=False) as router:
        refresh = router.post(MAGALU_TOKEN_URL).mock(return_value=token_response())
        api = router.get(MAGALU_API_BASE + "/test").mock(return_value=httpx.Response(200))
        with pytest.raises(DBAPIError, match="simulated persistence failure"):
            await client._request("GET", "/test")
        assert refresh.call_count == 1
        assert api.call_count == 0
    assert client.creds == original
    assert (await database.read(integration_id)).credentials == original


async def test_refresh_does_not_wait_for_callers_uncommitted_foreign_key_insert(database):
    original = creds(expired=True)
    integration_id = await database.insert(original)
    async with database.engine.begin() as connection:
        await connection.execute(text(f'''
            CREATE TABLE "{database.schema}".pending_listing (
                integration_id uuid REFERENCES "{database.schema}".integrations(id)
            )
        '''))
    client = MagaluClient(original, integration_id=integration_id)
    async with database.sessions.begin() as caller:
        # A real FK check obtains KEY SHARE on the parent until caller commits.
        await caller.execute(
            text(f'INSERT INTO "{database.schema}".pending_listing VALUES (:id)'),
            {"id": integration_id},
        )
        with respx.mock as router:
            refresh = router.post(MAGALU_TOKEN_URL).mock(return_value=token_response())
            await asyncio.wait_for(client.refresh(), 2)
            assert refresh.call_count == 1
        assert client.creds["access_token"] == "new-access"
