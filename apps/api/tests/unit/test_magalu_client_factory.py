"""Magalu construction paths retain the account identity without doing I/O.

Run with pytest --noconftest; no database or marketplace calls are made.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models import IntegrationPlatform
from app.security.cipher import decrypt_json, encrypt_json
from app.services import auto_link, listings_import
from app.services.marketplaces.factory import client_for
from app.services.marketplaces.magalu import MagaluClient


@pytest.fixture
def credentials():
    return {
        "access_token": "test-access",
        "refresh_token": "test-refresh",
        "expires_at": 1900000000,
    }


def test_factory_preserves_magalu_integration_id_and_callback(credentials):
    integration_id = uuid4()
    callback = AsyncMock()

    client = client_for(
        IntegrationPlatform.MAGALU,
        credentials,
        on_token_refresh=callback,
        integration_id=integration_id,
    )

    assert isinstance(client, MagaluClient)
    assert client._integration_id == integration_id
    assert client._on_refresh is callback
    assert client.creds == credentials
    assert client.creds is not credentials
    callback.assert_not_awaited()


@pytest.mark.parametrize(
    "constructor", [MagaluClient, lambda creds: client_for(IntegrationPlatform.MAGALU, creds)]
)
def test_magalu_constructor_without_integration_id_remains_supported(constructor, credentials):
    client = constructor(credentials)

    assert isinstance(client, MagaluClient)
    assert client._integration_id is None
    assert client.creds == credentials


@pytest.mark.parametrize(
    "builder",
    [
        lambda integ, session: auto_link._magalu_client_for(integ, session),
        lambda integ, session: listings_import._client_for_listings(session, integ),
    ],
    ids=["auto-link", "listings-import"],
)
def test_import_clients_keep_each_encrypted_accounts_identity(builder, credentials):
    session = SimpleNamespace(commit=AsyncMock())
    integrations = [
        SimpleNamespace(
            id=uuid4(),
            platform=IntegrationPlatform.MAGALU,
            credentials=encrypt_json({**credentials, "access_token": f"test-account-{number}"}),
        )
        for number in range(2)
    ]

    clients = [builder(integration, session) for integration in integrations]

    for client, integration in zip(clients, integrations, strict=True):
        assert isinstance(client, MagaluClient)
        assert client._integration_id == integration.id
        assert client.creds == decrypt_json(integration.credentials)
        assert callable(client._on_refresh)
    assert clients[0]._integration_id != clients[1]._integration_id
    session.commit.assert_not_awaited()
