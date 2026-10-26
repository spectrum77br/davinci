"""Real row-lock tests, only on an explicitly supplied disposable LOCAL database.

Run --noconftest with CARREFOUR_TEST_DATABASE_URL. Every test creates/drops its
own UUID schema; HTTP is fully mocked. No production schema is used.
"""
# ruff: noqa: S608

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import respx
from sqlalchemy import text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.services.marketplaces.carrefour import (
    ACCOUNT_CACHE_KEY,
    CARREFOUR_API_BASE,
    CarrefourClient,
)

TEST_DATABASE = os.environ.get("CARREFOUR_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.skipif(not TEST_DATABASE, reason="requires a disposable local PostgreSQL"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest_asyncio.fixture(loop_scope="session")
async def database(monkeypatch):
    from app import db
    from app.models import Integration, IntegrationPlatform
    from app.security.cipher import decrypt_json, encrypt_json

    url = make_url(TEST_DATABASE)
    assert url.host in {"127.0.0.1", "localhost", "::1"}, "test database must be local"
    schema = "carrefour_cache_test_" + uuid4().hex
    engine = create_async_engine(
        url, poolclass=NullPool,
        execution_options={"schema_translate_map": {Integration.__table__.schema: schema}},
        connect_args={"server_settings": {"search_path": schema + ",public"}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(
                f'CREATE TYPE "{schema}".integration_platform AS ENUM (\'carrefour\')'
            ))
            for table in ("users", "stores"):
                await connection.execute(text(
                    f'CREATE TABLE "{schema}".{table} (id uuid PRIMARY KEY)'
                ))
            await connection.run_sync(
                lambda sync: Integration.__table__.create(sync, checkfirst=True)
            )
        monkeypatch.setattr(db, "SessionLocal", sessions)

        async def insert(creds):
            async with sessions.begin() as session:
                owner = uuid4()
                await session.execute(
                    text(f'INSERT INTO "{schema}".users (id) VALUES (:id)'), {"id": owner},
                )
                integration = Integration(
                    user_id=owner, platform=IntegrationPlatform.CARREFOUR,
                    name="Isolated Carrefour test", credentials=encrypt_json(creds),
                )
                session.add(integration)
                await session.flush()
                return integration.id

        async def read(integration_id):
            async with sessions() as session:
                integration = await session.get(Integration, integration_id)
                return decrypt_json(integration.credentials)

        async def replace(integration_id, creds):
            async with sessions.begin() as session:
                await session.execute(
                    update(Integration).where(Integration.id == integration_id).values(
                        credentials=encrypt_json(creds),
                    )
                )

        yield SimpleNamespace(insert=insert, read=read, replace=replace)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


@respx.mock
async def test_concurrent_saved_clients_only_call_account_once(database):
    credentials = {"api_key": "test-current-key", "shop_id": 123, "preserve": "metadata"}
    integration_id = await database.insert(credentials)
    callback = AsyncMock()

    async def respond(request):
        await asyncio.sleep(0.05)  # allow other DB connections to reach the row lock
        return httpx.Response(200, json={"shop_id": 123, "shop_name": "VR", "shop_state": "OPEN"})

    route = respx.get(f"{CARREFOUR_API_BASE}/account").mock(side_effect=respond)
    clients = [CarrefourClient(credentials, callback, integration_id) for _ in range(5)]
    results = await asyncio.gather(*[client.test_connection() for client in clients])

    assert all(result.ok for result in results), [result.detail for result in results]
    assert sum(not result.info["cached"] for result in results) == 1
    assert route.call_count == 1
    callback.assert_not_awaited()
    saved = await database.read(integration_id)
    assert saved[ACCOUNT_CACHE_KEY]["ok"]
    assert saved["api_key"] == credentials["api_key"]
    assert saved["preserve"] == "metadata"


@respx.mock
async def test_saved_client_rereads_rotated_credentials_before_http(database):
    stale = {"api_key": "test-old-key", "shop_id": 123}
    current = {"api_key": "test-new-key", "shop_id": 456, "keep": "new-metadata"}
    integration_id = await database.insert(stale)
    client = CarrefourClient(stale, integration_id=integration_id)
    await database.replace(integration_id, current)
    route = respx.get(f"{CARREFOUR_API_BASE}/account").respond(
        200, json={"shop_id": 456, "shop_name": "Poofy", "shop_state": "OPEN"},
    )

    result = await client.test_connection()

    assert result.ok, result.detail
    assert route.calls[0].request.headers["Authorization"] == "test-new-key"
    assert route.calls[0].request.url.params["shop_id"] == "456"
    saved = await database.read(integration_id)
    assert saved["api_key"] == current["api_key"] and saved["keep"] == "new-metadata"
    assert client.creds["api_key"] == current["api_key"]


@respx.mock
async def test_rotation_during_account_query_cannot_be_undone_by_cache_write(database):
    current = {"api_key": "test-key-before", "shop_id": 123}
    rotated = {"api_key": "test-key-after", "shop_id": 456, "rotated": True}
    integration_id = await database.insert(current)
    in_http = asyncio.Event()
    release_http = asyncio.Event()
    callback = AsyncMock()

    async def respond(request):
        in_http.set()
        await asyncio.wait_for(release_http.wait(), timeout=5)
        return httpx.Response(200, json={"shop_id": 123, "shop_name": "VR", "shop_state": "OPEN"})

    respx.get(f"{CARREFOUR_API_BASE}/account").mock(side_effect=respond)
    check = asyncio.create_task(
        CarrefourClient(current, callback, integration_id).test_connection()
    )
    await asyncio.wait_for(in_http.wait(), timeout=5)
    rotation = asyncio.create_task(database.replace(integration_id, rotated))
    try:
        await asyncio.sleep(0.05)
        assert not rotation.done(), "the credential update must wait for the account check row lock"
    finally:
        release_http.set()
    result, _ = await asyncio.wait_for(asyncio.gather(check, rotation), timeout=5)

    assert result.ok, result.detail
    assert await database.read(integration_id) == rotated
    callback.assert_not_awaited()


@respx.mock
async def test_missing_saved_integration_does_not_query_marketplace(database):
    result = await CarrefourClient(
        {"api_key": "test-key", "shop_id": 123}, integration_id=uuid4(),
    ).test_connection()
    assert not result.ok
    assert not respx.calls
