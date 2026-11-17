"""Write activation is explicit per Carrefour account; secrets stay private."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models import IntegrationPlatform
from app.routers.integrations import (
    _apply_carrefour_write_flags,
    _carrefour_write_flags,
    _validated_carrefour_credentials,
)
from app.schemas.integrations import IntegrationPatch
from app.security.cipher import decrypt_json, encrypt_json


def account(**extra):
    return SimpleNamespace(
        platform=IntegrationPlatform.CARREFOUR,
        credentials=encrypt_json({"api_key": "test-secret", "shop_id": "123", **extra}),
    )


def test_flags_default_off_and_are_isolated_per_account():
    poofy, other = account(), account()
    assert not any(_carrefour_write_flags(poofy).values())
    _apply_carrefour_write_flags(poofy, {"carrefour_stock_write_enabled": True})
    assert _carrefour_write_flags(poofy) == {
        "carrefour_stock_write_enabled": True,
        "carrefour_price_write_enabled": False,
    }
    assert not any(_carrefour_write_flags(other).values())
    assert "test-secret" not in str(_carrefour_write_flags(poofy))


def test_toggle_preserves_key_shop_and_verification_and_can_disable():
    integration = account(_carrefour_account_check={"ok": True})
    _apply_carrefour_write_flags(
        integration,
        {
            "carrefour_price_write_enabled": True,
            "carrefour_stock_write_enabled": True,
        },
    )
    _apply_carrefour_write_flags(integration, {"carrefour_stock_write_enabled": False})
    credentials = decrypt_json(integration.credentials)
    assert credentials["api_key"] == "test-secret"
    assert credentials["shop_id"] == "123"
    assert credentials["_carrefour_account_check"] == {"ok": True}
    assert _carrefour_write_flags(integration) == {
        "carrefour_stock_write_enabled": False,
        "carrefour_price_write_enabled": True,
    }


@pytest.mark.parametrize("value", ["true", "false", 1, 0, [], {}])
def test_patch_requires_actual_boolean(value):
    with pytest.raises(ValidationError):
        IntegrationPatch(carrefour_stock_write_enabled=value)


def test_flags_cannot_be_injected_through_credentials_or_other_platform():
    with pytest.raises(HTTPException):
        _validated_carrefour_credentials(
            {
                "api_key": "key",
                "shop_id": "123",
                "_carrefour_stock_write_enabled": True,
            }
        )
    other = account()
    other.platform = IntegrationPlatform.ML
    with pytest.raises(HTTPException) as error:
        _apply_carrefour_write_flags(other, {"carrefour_price_write_enabled": True})
    assert error.value.detail["code"] == "carrefour_only_setting"


async def test_edit_api_only_activates_requested_account(client, db, make_user, auth_as):
    from app.models import UserRole

    auth_as(await make_user(role=UserRole.ADMIN))
    ids = []
    for name in ("Poofy", "Other"):
        company = (
            await client.post(
                "/api/companies",
                json={
                    "razao_social": name,
                    "apelido": name,
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
        response = await client.post(
            "/api/integrations",
            json={
                "platform": "carrefour",
                "name": name,
                "store_id": store["id"],
                "credentials": {"api_key": "test-secret", "shop_id": len(ids) + 1},
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["carrefour_stock_write_enabled"] is False
        ids.append(response.json()["id"])
    response = await client.patch(
        f"/api/integrations/{ids[0]}",
        json={
            "carrefour_stock_write_enabled": True,
            "carrefour_price_write_enabled": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["carrefour_stock_write_enabled"] is True
    assert "test-secret" not in response.text
    listed = (await client.get("/api/integrations")).json()
    other = next(row for row in listed if row["id"] == ids[1])
    assert other["carrefour_stock_write_enabled"] is False
    assert other["carrefour_price_write_enabled"] is False
    status = await client.get(f"/api/integrations/{ids[0]}/carrefour-writes")
    assert status.status_code == 200, status.text
    assert status.json() == {
        "linked": 0,
        "pending": 0,
        "review": 0,
        "confirmed": 0,
        "blocked": False,
        "last_error": None,
    }
    assert "test-secret" not in status.text
