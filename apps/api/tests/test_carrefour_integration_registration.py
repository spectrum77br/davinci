"""Carrefour account scope, credential handling and additive enum migration."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models import IntegrationPlatform, Marketplace, UserRole
from app.routers.integrations import (
    _connection_test_time,
    _validate_carrefour_store,
    _validated_carrefour_credentials,
)


def test_carrefour_cached_check_keeps_original_time_and_other_platforms_use_now():
    original = 1700000000
    expected = datetime.fromtimestamp(original, tz=UTC)
    assert (
        _connection_test_time(
            IntegrationPlatform.CARREFOUR,
            {
                "checked_at": original,
                "cached": True,
            },
        )
        == expected
    )
    before = datetime.now(UTC)
    assert _connection_test_time(IntegrationPlatform.ML, {"checked_at": original}) >= before
    assert _connection_test_time(IntegrationPlatform.CARREFOUR, {"checked_at": True}) >= before


def test_credential_validation_normalizes_shop_and_rejects_cache_or_url_input():
    assert _validated_carrefour_credentials({"api_key": " test-key ", "shop_id": "00123"}) == {
        "api_key": "test-key",
        "shop_id": "123",
    }
    for injected in ("base_url", "_carrefour_account_check", "access_token"):
        with pytest.raises(HTTPException) as exc:
            _validated_carrefour_credentials({"api_key": "test-key", "shop_id": 123, injected: "x"})
        assert exc.value.detail == {"code": "carrefour_credentials_invalid"}


@pytest.mark.parametrize(
    "shop_id", [None, "", 0, -1, True, "1.5", "1&shop_id=2", "１２３", "9223372036854775808"]
)
def test_credential_validation_rejects_invalid_shop_id_without_echo(shop_id):
    with pytest.raises(HTTPException) as exc:
        _validated_carrefour_credentials({"api_key": "private-test-key", "shop_id": shop_id})
    assert exc.value.detail == {"code": "carrefour_shop_id_invalid"}


@pytest.mark.parametrize("api_key", [None, "", " ", 123, "a\nb", "a\rb", "a\x00b", "a b", "á"])
def test_credential_validation_rejects_invalid_keys_without_echo(api_key):
    with pytest.raises(HTTPException) as exc:
        _validated_carrefour_credentials({"api_key": api_key, "shop_id": 123})
    assert set(exc.value.detail) == {"code"}
    assert exc.value.detail["code"] in {"carrefour_api_key_required", "carrefour_api_key_invalid"}


def test_credential_validation_rotates_key_but_cannot_retarget_existing_offers():
    existing = {"api_key": "old-key", "shop_id": "123", "_carrefour_account_check": {"ok": True}}
    assert _validated_carrefour_credentials({}, existing=existing) == existing
    rotated = _validated_carrefour_credentials({"api_key": "new-key"}, existing=existing)
    assert rotated == {"api_key": "new-key", "shop_id": "123"}
    assert existing["api_key"] == "old-key"
    with pytest.raises(HTTPException) as exc:
        _validated_carrefour_credentials({"shop_id": "456"}, existing=existing)
    assert exc.value.detail == {"code": "carrefour_shop_id_immutable"}


def test_store_validation_requires_carrefour_link_without_changing_other_platforms():
    _validate_carrefour_store(
        IntegrationPlatform.CARREFOUR, SimpleNamespace(marketplace=Marketplace.CARREFOUR)
    )
    _validate_carrefour_store(
        IntegrationPlatform.BLING, SimpleNamespace(marketplace=Marketplace.ML)
    )
    for store, code in (
        (None, "carrefour_store_required"),
        (SimpleNamespace(marketplace=Marketplace.ML), "integration_store_platform_mismatch"),
    ):
        with pytest.raises(HTTPException) as exc:
            _validate_carrefour_store(IntegrationPlatform.CARREFOUR, store)
        assert exc.value.detail == {"code": code}


def test_migration_only_adds_enum_value_without_creating_accounts(monkeypatch):
    path = Path(__file__).parents[1] / "alembic/versions/0430_integration_carrefour.py"
    spec = importlib.util.spec_from_file_location("carrefour_integration_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements = []
    monkeypatch.setattr(migration, "op", SimpleNamespace(execute=statements.append))
    monkeypatch.setattr(migration, "SCHEMA", "carrefour_unit_test")
    migration.upgrade()
    migration.downgrade()
    assert statements == [
        'ALTER TYPE "carrefour_unit_test".integration_platform '
        "ADD VALUE IF NOT EXISTS 'carrefour'",
    ]
    assert migration.down_revision == "0429_compra_contabilidade"


async def test_carrefour_api_registration_rotation_and_store_scope(client, db, make_user, auth_as):
    from sqlalchemy import select

    from app.models import Integration
    from app.security.cipher import decrypt_json, encrypt_json

    auth_as(await make_user(role=UserRole.ADMIN))
    company = (
        await client.post(
            "/api/companies", json={"razao_social": "Carrefour testes", "apelido": "teste"}
        )
    ).json()
    store = (
        await client.post(
            "/api/stores", json={"company_id": company["id"], "marketplace": "carrefour"}
        )
    ).json()
    body = {
        "platform": "carrefour",
        "name": "Carrefour teste",
        "credentials": {"api_key": "test-secret", "shop_id": 123},
    }
    response = await client.post("/api/integrations", json=body)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "carrefour_store_required"
    body["store_id"] = store["id"]
    response = await client.post("/api/integrations", json=body)
    assert response.status_code == 201, response.text
    iid = response.json()["id"]
    assert "test-secret" not in response.text
    assert "credentials" not in response.json()
    integration = (await db.execute(select(Integration).where(Integration.id == iid))).scalar_one()
    assert b"test-secret" not in integration.credentials
    integration.credentials = encrypt_json(
        {**decrypt_json(integration.credentials), "_carrefour_account_check": {"ok": True}}
    )
    integration.last_test_ok = True
    await db.commit()
    response = await client.patch(
        f"/api/integrations/{iid}", json={"credentials": {"shop_id": 456}}
    )
    assert response.status_code == 400
    response = await client.patch(
        f"/api/integrations/{iid}", json={"credentials": {"api_key": "rotated-secret"}}
    )
    assert response.status_code == 200
    assert response.json()["last_test_ok"] is None
    await db.refresh(integration)
    assert decrypt_json(integration.credentials) == {"api_key": "rotated-secret", "shop_id": "123"}


async def test_carrefour_api_old_check_cannot_mark_rotated_key_verified(
    client,
    db,
    make_user,
    auth_as,
    monkeypatch,
):
    import asyncio

    import respx
    from sqlalchemy import select

    from app.models import Integration
    from app.security.cipher import decrypt_json
    from app.services.marketplaces.carrefour import CARREFOUR_API_BASE, CarrefourClient

    auth_as(await make_user(role=UserRole.ADMIN))
    company = (
        await client.post(
            "/api/companies",
            json={
                "razao_social": "Carrefour race",
                "apelido": "race",
            },
        )
    ).json()
    store = (
        await client.post(
            "/api/stores",
            json={
                "company_id": company["id"],
                "marketplace": "carrefour",
            },
        )
    ).json()
    integration = (
        await client.post(
            "/api/integrations",
            json={
                "platform": "carrefour",
                "store_id": store["id"],
                "name": "Carrefour race",
                "credentials": {"api_key": "test-before", "shop_id": 123},
            },
        )
    ).json()
    iid = integration["id"]
    checked = asyncio.Event()
    rotated = asyncio.Event()
    actual_test = CarrefourClient.test_connection

    async def pause_after_check(self):
        result = await actual_test(self)
        checked.set()
        await asyncio.wait_for(rotated.wait(), timeout=5)
        return result

    monkeypatch.setattr(CarrefourClient, "test_connection", pause_after_check)
    with respx.mock as mocked:
        route = mocked.get(f"{CARREFOUR_API_BASE}/account").respond(
            200,
            json={
                "shop_id": 123,
                "shop_name": "VR",
                "shop_state": "OPEN",
            },
        )
        pending = asyncio.create_task(client.post(f"/api/integrations/{iid}/test"))
        try:
            await asyncio.wait_for(checked.wait(), timeout=5)
            rotation = await client.patch(
                f"/api/integrations/{iid}",
                json={
                    "credentials": {"api_key": "test-after"},
                },
            )
            assert rotation.status_code == 200, rotation.text
        finally:
            rotated.set()
        response = await asyncio.wait_for(pending, timeout=5)
        assert route.call_count == 1
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["code"] == "carrefour_credentials_changed"
    saved = (await db.execute(select(Integration).where(Integration.id == iid))).scalar_one()
    assert decrypt_json(saved.credentials) == {"api_key": "test-after", "shop_id": "123"}
    assert saved.last_test_ok is None
    assert saved.last_test_at is None
