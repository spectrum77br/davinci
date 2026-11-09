"""Native client tests with mocked HTTP, isolated from production package startup."""
# ruff: noqa: S105 - All tokens and secrets below are synthetic test fixtures.

import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest


@pytest.fixture
def temu(monkeypatch):
    name = "app.services.marketplaces"
    keys = [name, name + ".base", name + ".temu"]
    previous = {key: sys.modules.get(key) for key in keys}
    package = ModuleType(name)
    package.__path__ = [str(Path(__file__).parents[1] / "app/services/marketplaces")]
    monkeypatch.setitem(sys.modules, name, package)
    for key in keys[1:]:
        sys.modules.pop(key, None)
    module = importlib.import_module(name + ".temu")
    yield module
    for key in keys[1:]:
        if previous[key] is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = previous[key]


def mock_http(temu, monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        temu.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )


def test_native_request_keeps_secret_out_of_url_and_never_allows_auth_overrides(temu, monkeypatch):
    seen = []

    def respond(request):
        seen.append(request)
        assert str(request.url) == "https://openapi-b-global.temu.com/openapi/router"
        assert "synthetic-secret" not in str(request.url)
        body = json.loads(request.content)
        assert "app_secret" not in body and body["sign"]
        assert body["access_token"] == "synthetic-token"
        return httpx.Response(200, json={"success": True, "result": {}})

    mock_http(temu, monkeypatch, respond)
    client = temu.TemuClient(
        {
            "app_key": "synthetic-app",
            "app_secret": "synthetic-secret",
            "access_token": "synthetic-token",
        }
    )
    asyncio.run(client._request("bg.open.accesstoken.info.get"))
    with pytest.raises(ValueError):
        asyncio.run(
            client._request("bg.open.accesstoken.info.get", {"access_token": "another-account"})
        )
    assert len(seen) == 1


def test_authorization_url_preserves_state_and_rejects_unsafe_callbacks(temu):
    uri = "https://example.test/api/integrations/temu/callback"
    url = temu.TemuClient.authorization_url("synthetic-app", uri, "s" * 32)
    assert urlsplit(url).netloc == "seller.temu.com"
    assert parse_qs(urlsplit(url).query) == {
        "appKey": ["synthetic-app"],
        "redirect_uri": [uri],
        "state": ["s" * 32],
    }
    for unsafe in [
        "http://example.test/callback",
        "https://user:pass@example.test/callback",
        uri + "#fragment",
    ]:
        with pytest.raises(ValueError):
            temu.TemuClient.authorization_url("synthetic-app", unsafe, "s" * 32)


def test_authorization_check_does_not_mistake_successful_catalog_request_for_valid_account(
    temu, monkeypatch
):
    def respond(request):
        assert json.loads(request.content)["type"] == "bg.open.accesstoken.info.get"
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "mallId": "mall-test",
                    "apiScopeList": ["read-orders"],
                    "accessToken": "never-return-this",
                },
            },
        )

    mock_http(temu, monkeypatch, respond)
    result = asyncio.run(
        temu.TemuClient(
            {"app_key": "app", "app_secret": "secret", "access_token": "token"}
        ).test_connection()
    )
    assert result.ok and result.info["mall_id"] == "mall-test"
    assert "never-return-this" not in json.dumps(result.info)


def test_false_success_and_error_messages_never_leak_credentials(temu, monkeypatch):
    client = temu.TemuClient(
        {
            "app_key": "app",
            "app_secret": "sensitive-example-secret",
            "access_token": "sensitive-example-token",
        }
    )
    assert not client._is_success({"success": False, "errorCode": 0})
    assert (
        client._error_message({"error_msg": "sensitive-example-secret sensitive-example-token"})
        == "[redacted] [redacted]"
    )
    mock_http(
        temu,
        monkeypatch,
        lambda request: httpx.Response(
            400, text="sensitive-example-secret sensitive-example-token"
        ),
    )
    response = asyncio.run(client._request("bg.open.accesstoken.info.get"))
    assert response == {"success": False, "error_msg": "HTTP 400"}


def test_authorized_code_exchange_retains_account_identity_and_rejects_incomplete_result(
    temu, monkeypatch
):
    responses = [
        {
            "success": True,
            "result": {
                "accessToken": "new-synthetic-token",
                "mallId": 123,
                "apiScopeList": ["read-orders"],
            },
        },
        {"success": True, "result": {"accessToken": "no-account-identity"}},
    ]

    def respond(request):
        body = json.loads(request.content)
        assert (
            body["type"] == "bg.open.accesstoken.create"
            and body["code"] == body["access_token"] == "operator-authorized-code"
        )
        return httpx.Response(200, json=responses.pop(0))

    mock_http(temu, monkeypatch, respond)
    result = asyncio.run(
        temu.TemuClient.exchange_authorization_code("app", "secret", "operator-authorized-code")
    )
    assert result["mall_id"] == "123" and result["access_token"] == "new-synthetic-token"
    with pytest.raises(ValueError):
        asyncio.run(
            temu.TemuClient.exchange_authorization_code("app", "secret", "operator-authorized-code")
        )
