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


# =============================================================================
# Mercado Livre integration-bound OAuth (mirrors the TikTok "login" flow).
# =============================================================================


async def _make_ml_integration(client, credentials: dict) -> tuple[str, str]:
    """Create a company + ML store + ML integration; returns (store_id, integration_id)."""
    _cid, sid = await _make_company_and_store(client)
    r = await client.post(
        "/api/integrations",
        json={"store_id": sid, "platform": "ml", "name": "ml-oauth", "credentials": credentials},
    )
    assert r.status_code == 201, r.text
    return sid, r.json()["id"]


@pytest.mark.asyncio
async def test_ml_oauth_start_creates_state(client, make_user, auth_as, db, monkeypatch):
    """`/api/integrations/ml/start` builds an ML authorize URL using the
    integration's own client_id and persists an integration-bound state row."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.models import OAuthState

    settings = get_settings()
    monkeypatch.setattr(
        settings, "ml_redirect_uri", "https://test/api/integrations/ml/callback", raising=False
    )

    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    _sid, iid = await _make_ml_integration(
        client, {"client_id": "CID-123", "client_secret": "CSEC-abc"}
    )

    r = await client.get(f"/api/integrations/ml/start?integrationId={iid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "state" in body and len(body["state"]) >= 16
    assert "auth.mercadolivre.com.br/authorization" in body["url"]
    assert "client_id=CID-123" in body["url"]
    assert "state=" + body["state"] in body["url"]

    row = (
        await db.execute(select(OAuthState).where(OAuthState.state == body["state"]))
    ).scalar_one()
    assert row.platform == IntegrationPlatform.ML
    assert row.code_verifier == iid  # threads the integration id to the callback


@pytest.mark.asyncio
async def test_ml_oauth_start_missing_client_id(client, make_user, auth_as):
    """Start must refuse an ML integration that has no client_id yet."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    _sid, iid = await _make_ml_integration(client, {"client_secret": "only-secret"})
    r = await client.get(f"/api/integrations/ml/start?integrationId={iid}")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "missing_client_id"


@pytest.mark.asyncio
async def test_ml_oauth_callback_exchanges_and_saves(client, make_user, auth_as, db, monkeypatch):
    """Callback exchanges the code with the integration's creds, merges the
    tokens back, and consumes the state (replay-proof)."""
    import time

    from sqlalchemy import select

    from app.models import Integration, OAuthState
    from app.security.cipher import decrypt_json

    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    sid, iid = await _make_ml_integration(
        client, {"client_id": "CID-123", "client_secret": "CSEC-abc"}
    )

    state_token = "ml-state-12345678"
    db.add(OAuthState(
        state=state_token,
        platform=IntegrationPlatform.ML,
        store_id=sid,
        user_id=admin.id,
        code_verifier=iid,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    ))
    await db.commit()

    async def fake_exchange(code, *, client_id=None, client_secret=None, redirect_uri=None):
        assert client_id == "CID-123" and client_secret == "CSEC-abc"
        return {
            "access_token": "AT-ml",
            "refresh_token": "RT-ml",
            "user_id": 123456,
            "scope": "read write offline_access",
            "expires_at": int(time.time()) + 21600,
            "client_id": client_id,
            "client_secret": client_secret,
        }

    from app.services.marketplaces import ml
    monkeypatch.setattr(ml.MercadoLivreClient, "exchange_code", staticmethod(fake_exchange))

    r = await client.get(f"/api/integrations/ml/callback?code=THE-CODE&state={state_token}")
    assert r.status_code in (302, 307), r.text
    assert "/companies/" in r.headers["location"]
    assert "platform=ml" in r.headers["location"]

    # State consumed.
    row = (
        await db.execute(select(OAuthState).where(OAuthState.state == state_token))
    ).scalar_one()
    assert row.consumed_at is not None

    # Tokens merged into the integration, client creds preserved, expiry set.
    integ = (
        await db.execute(select(Integration).where(Integration.id == iid))
    ).scalar_one()
    creds = decrypt_json(integ.credentials)
    assert creds["access_token"] == "AT-ml"
    assert creds["refresh_token"] == "RT-ml"
    assert creds["user_id"] == 123456
    assert creds["client_id"] == "CID-123"
    assert integ.status == "active"
    assert integ.token_expires_at is not None

    # Replay should fail (state already consumed).
    r2 = await client.get(f"/api/integrations/ml/callback?code=AGAIN&state={state_token}")
    assert r2.status_code == 400
    assert r2.json()["detail"]["code"] == "state_consumed"


async def _make_shopee_integration(client, credentials: dict) -> tuple[str, str]:
    """Create a company + store + Shopee integration; returns (store_id, integration_id)."""
    _cid, sid = await _make_company_and_store(client)
    r = await client.post(
        "/api/integrations",
        json={
            "store_id": sid,
            "platform": "shopee",
            "name": "shopee-oauth",
            "credentials": credentials,
        },
    )
    assert r.status_code == 201, r.text
    return sid, r.json()["id"]


@pytest.mark.asyncio
async def test_shopee_oauth_start_creates_state(client, make_user, auth_as, db, monkeypatch):
    """`/api/integrations/shopee/start` builds an auth_partner URL from the
    integration's own partner_id and threads the state through the redirect path."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.models import OAuthState

    settings = get_settings()
    monkeypatch.setattr(
        settings, "shopee_redirect_uri",
        "https://test/api/integrations/shopee/callback", raising=False,
    )
    monkeypatch.setattr(settings, "shopee_use_sandbox", False, raising=False)

    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    _sid, iid = await _make_shopee_integration(
        client, {"partner_id": "2012455", "partner_key": "KEY-abc"}
    )

    r = await client.get(f"/api/integrations/shopee/start?integrationId={iid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "state" in body and len(body["state"]) >= 16
    assert "partner.shopeemobile.com/api/v2/shop/auth_partner" in body["url"]
    assert "partner_id=2012455" in body["url"]
    # state is url-safe, so it survives verbatim inside the (encoded) redirect param.
    assert body["state"] in body["url"]

    row = (
        await db.execute(select(OAuthState).where(OAuthState.state == body["state"]))
    ).scalar_one()
    assert row.platform == IntegrationPlatform.SHOPEE
    assert row.code_verifier == iid  # threads the integration id to the callback


@pytest.mark.asyncio
async def test_shopee_oauth_start_missing_partner_key(client, make_user, auth_as, monkeypatch):
    """Start must refuse a Shopee integration with no partner_key (and no env fallback)."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "shopee_partner_key", "", raising=False)
    monkeypatch.setattr(
        settings, "shopee_redirect_uri",
        "https://test/api/integrations/shopee/callback", raising=False,
    )
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    _sid, iid = await _make_shopee_integration(client, {"partner_id": "2012455"})
    r = await client.get(f"/api/integrations/shopee/start?integrationId={iid}")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "missing_partner_credentials"


@pytest.mark.asyncio
async def test_shopee_oauth_callback_exchanges_and_saves(
    client, make_user, auth_as, db, monkeypatch
):
    """Callback exchanges code+shop_id with the integration's creds, merges the
    tokens back (incl. shop_id), and consumes the state (replay-proof)."""
    import time

    from sqlalchemy import select

    from app.models import Integration, OAuthState
    from app.security.cipher import decrypt_json

    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    sid, iid = await _make_shopee_integration(
        client, {"partner_id": "2012455", "partner_key": "KEY-abc"}
    )

    state_token = "shopee-state-12345678"
    db.add(OAuthState(
        state=state_token,
        platform=IntegrationPlatform.SHOPEE,
        store_id=sid,
        user_id=admin.id,
        code_verifier=iid,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    ))
    await db.commit()

    async def fake_exchange(code, shop_id, *, partner_id=None, partner_key=None, use_sandbox=False):
        assert code == "THE-CODE"
        assert str(shop_id) == "778899"
        assert partner_id == "2012455" and partner_key == "KEY-abc"
        return {
            "access_token": "AT-shopee",
            "refresh_token": "RT-shopee",
            "expires_at": int(time.time()) + 14400,
            "shop_id": 778899,
        }

    from app.services.marketplaces import shopee
    monkeypatch.setattr(shopee.ShopeeClient, "exchange_code", staticmethod(fake_exchange))

    r = await client.get(
        f"/api/integrations/shopee/callback/{state_token}?code=THE-CODE&shop_id=778899"
    )
    assert r.status_code in (302, 307), r.text
    assert "/companies/" in r.headers["location"]
    assert "platform=shopee" in r.headers["location"]

    # State consumed.
    row = (
        await db.execute(select(OAuthState).where(OAuthState.state == state_token))
    ).scalar_one()
    assert row.consumed_at is not None

    # Tokens merged into the integration, partner creds preserved, expiry set.
    integ = (
        await db.execute(select(Integration).where(Integration.id == iid))
    ).scalar_one()
    creds = decrypt_json(integ.credentials)
    assert creds["access_token"] == "AT-shopee"
    assert creds["refresh_token"] == "RT-shopee"
    assert creds["shop_id"] == 778899
    assert creds["partner_id"] == "2012455"  # kept alongside the new tokens
    assert integ.status == "active"
    assert integ.token_expires_at is not None

    # Replay should fail (state already consumed).
    r2 = await client.get(
        f"/api/integrations/shopee/callback/{state_token}?code=AGAIN&shop_id=778899"
    )
    assert r2.status_code == 400
    assert r2.json()["detail"]["code"] == "state_consumed"
