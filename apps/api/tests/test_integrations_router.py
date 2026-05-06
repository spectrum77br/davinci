"""Integrations router + OAuth state flow.

Test setup creates a company + store, then exercises CRUD + OAuth state lifecycle.
Bling token exchange is patched via monkeypatch (no live HTTP).
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.models import IntegrationPlatform, UserRole


async def _make_company_and_store(client) -> tuple[str, str]:
    r = await client.post("/api/companies", json={"razao_social": "AGUIAR", "apelido": "aguiar"})
    cid = r.json()["id"]
    r = await client.post(
        "/api/stores", json={"company_id": cid, "marketplace": "ml", "status": "active"}
    )
    return cid, r.json()["id"]


@pytest.mark.asyncio
async def test_create_integration_links_store(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    _cid, sid = await _make_company_and_store(client)
    r = await client.post(
        "/api/integrations",
        json={
            "store_id": sid,
            "platform": "ml",
            "name": "ml-aguiar",
            "credentials": {"access_token": "x"},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["store_id"] == sid
    assert "credentials" not in body  # never leaked

    # store now has integration_id set
    sr = await client.get(f"/api/stores?company_id={_cid}")
    assert sr.json()[0]["integration_id"] == body["id"]

    # second integration on same store → 409
    r2 = await client.post(
        "/api/integrations",
        json={"store_id": sid, "platform": "ml", "name": "dup", "credentials": {}},
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_credentials_round_trip(client, make_user, auth_as, db):
    """Credentials should be ciphered at rest (BYTEA != plaintext)."""
    from sqlalchemy import select

    from app.models import Integration

    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    _cid, sid = await _make_company_and_store(client)
    r = await client.post(
        "/api/integrations",
        json={
            "store_id": sid,
            "platform": "ml",
            "name": "ml",
            "credentials": {"access_token": "secret-token-xyz"},
        },
    )
    iid = r.json()["id"]

    # Inspect raw row
    integ = (await db.execute(select(Integration).where(Integration.id == iid))).scalar_one()
    assert isinstance(integ.credentials, bytes)
    assert b"secret-token-xyz" not in integ.credentials  # ciphered


@pytest.mark.asyncio
async def test_delete_integration_unlinks_store(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    _cid, sid = await _make_company_and_store(client)
    r = await client.post(
        "/api/integrations",
        json={"store_id": sid, "platform": "ml", "name": "ml", "credentials": {}},
    )
    iid = r.json()["id"]

    r = await client.delete(f"/api/integrations/{iid}")
    assert r.status_code == 204

    sr = await client.get(f"/api/stores?company_id={_cid}")
    assert sr.json()[0]["integration_id"] is None


@pytest.mark.asyncio
async def test_oauth_start_creates_signed_state(client, make_user, auth_as, db, monkeypatch):
    """`/api/oauth/bling/start?store_id=...` should produce URL + persisted state row."""
    from sqlalchemy import select

    from app.models import OAuthState

    # Bling auth url builder relies on env.
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "bling_client_id", "test-client", raising=False)
    monkeypatch.setattr(
        settings, "bling_redirect_uri", "https://test/api/oauth/bling/callback", raising=False
    )

    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    _cid, sid = await _make_company_and_store(client)

    r = await client.get(f"/api/oauth/bling/start?store_id={sid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "state" in body and len(body["state"]) >= 16
    assert "bling.com.br/Api/v3/oauth/authorize" in body["url"]
    assert "state=" + body["state"] in body["url"]

    row = (
        await db.execute(select(OAuthState).where(OAuthState.state == body["state"]))
    ).scalar_one()
    assert row.platform == IntegrationPlatform.BLING
    assert str(row.store_id) == sid
    assert row.expires_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_oauth_callback_exchanges_and_links(client, make_user, auth_as, db, monkeypatch):
    """Callback should exchange the code, persist ciphered creds, and link the store."""
    import time

    from sqlalchemy import select

    from app.models import Integration, OAuthState

    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    _cid, sid = await _make_company_and_store(client)

    # Pre-create state row.
    state_token = "test-state-12345678"
    db.add(OAuthState(
        state=state_token,
        platform=IntegrationPlatform.BLING,
        store_id=sid,
        user_id=admin.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    ))
    await db.commit()

    # Patch Bling token exchange.
    async def fake_exchange(code: str) -> dict:
        return {
            "access_token": "AT-1",
            "refresh_token": "RT-1",
            "token_type": "Bearer",
            "scope": "*",
            "expires_at": int(time.time()) + 21600,
        }

    from app.services.marketplaces import bling
    monkeypatch.setattr(bling.BlingClient, "exchange_code", staticmethod(fake_exchange))

    # FastAPI test transport does not follow redirects by default.
    r = await client.get(f"/api/oauth/bling/callback?code=THE-CODE&state={state_token}")
    assert r.status_code in (302, 307), r.text
    assert "/companies/" in r.headers["location"]

    # State consumed.
    row = (
        await db.execute(select(OAuthState).where(OAuthState.state == state_token))
    ).scalar_one()
    assert row.consumed_at is not None

    # Integration created and linked.
    integs = (await db.execute(select(Integration))).scalars().all()
    assert len(integs) == 1
    assert integs[0].platform == IntegrationPlatform.BLING
    assert str(integs[0].store_id) == sid

    # Replay should fail (state already consumed).
    r2 = await client.get(f"/api/oauth/bling/callback?code=AGAIN&state={state_token}")
    assert r2.status_code == 400
    assert r2.json()["detail"]["code"] == "state_consumed"


@pytest.mark.asyncio
async def test_test_connection_records_outcome(client, make_user, auth_as, db, monkeypatch):
    """`POST /integrations/{id}/test` should call the client and persist last_test_*."""
    from sqlalchemy import select

    from app.models import Integration
    from app.services.marketplaces.base import TestResult

    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    _cid, sid = await _make_company_and_store(client)
    r = await client.post(
        "/api/integrations",
        json={"store_id": sid, "platform": "bling", "name": "bling", "credentials": {}},
    )
    iid = r.json()["id"]

    async def fake_test(self):
        return TestResult(ok=True, info={"user": "stub"})

    from app.services.marketplaces import bling
    monkeypatch.setattr(bling.BlingClient, "test_connection", fake_test)

    rt = await client.post(f"/api/integrations/{iid}/test")
    assert rt.status_code == 200
    body = rt.json()
    assert body["ok"] is True
    assert body["info"] == {"user": "stub"}

    integ = (await db.execute(select(Integration).where(Integration.id == iid))).scalar_one()
    assert integ.last_test_ok is True
    assert integ.last_test_at is not None
    assert integ.last_error is None


@pytest.mark.asyncio
async def test_oauth_unknown_provider_404(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    _cid, sid = await _make_company_and_store(client)
    r = await client.get(f"/api/oauth/walmart/start?store_id={sid}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_integration_requires_empresa_edit(client, make_user, auth_as):
    user = await make_user(role=UserRole.USER, permissions={})
    auth_as(user)
    r = await client.post(
        "/api/integrations",
        json={
            "store_id": "00000000-0000-0000-0000-000000000000",
            "platform": "ml",
            "name": "x",
            "credentials": {},
        },
    )
    assert r.status_code == 403
