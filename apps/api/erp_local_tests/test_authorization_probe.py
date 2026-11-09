import asyncio
import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.erp_local.authorization_probe import ProbeInputError, execute, plan, verdict
from app.erp_local.core import ERPError
from app.erp_local.integration_registry import (
    authorization_snapshot,
    record_authorizations,
)

CREDS = {
    "access_token": "PRIVATE-TOKEN",
    "user_id": "123",
    "shop_id": "123",
    "partner_id": "456",
    "partner_key": "PRIVATE-KEY",
    "app_key": "app",
    "app_secret": "PRIVATE-SECRET",
    "shop_cipher": "cipher",
    "marketplace_id": "A2Q3Y263D00KWC",
    "api_key": "PRIVATE-API-KEY",
}


@pytest.mark.parametrize("platform", ["ml", "shopee", "amazon", "tiktok", "magalu", "carrefour"])
def test_fixed_get_probes_do_not_retain_mutated_credentials_or_expose_them_in_repr(platform):
    credentials = copy.deepcopy(CREDS)
    read = plan(platform, credentials, now=1700000000)
    assert credentials == CREDS
    assert "PRIVATE" not in repr(read)
    assert "refresh" not in read.path and "token" not in read.path
    calls = []

    async def run():
        def transport(request):
            calls.append(request)
            return httpx.Response(401, json={"message": "PRIVATE-SECRET"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            result = await execute(read, client)
        assert result["state"] == "auth_denied"
        assert "PRIVATE" not in json.dumps(result)

    asyncio.run(run())
    assert len(calls) == 1 and calls[0].method == "GET" and not calls[0].content


def test_no_refresh_fallback_or_credentials_supplied_host_override():
    with pytest.raises(ProbeInputError, match="credentials_missing"):
        plan("amazon", {"refresh_token": "PRIVATE"}, now=1)
    with pytest.raises(ProbeInputError, match="configuration_missing"):
        plan("bling", CREDS, now=1)
    read = plan("ml", {**CREDS, "base_url": "https://evil.invalid"}, now=1)
    assert read.url == "https://api.mercadolibre.com/users/me"
    with pytest.raises(ProbeInputError):
        asyncio.run(execute(replace(read, url="https://evil.invalid/users/me"), None))


def test_protocol_success_requires_identity_and_does_not_trust_http_200_alone():
    ml = plan("ml", CREDS, now=1)
    assert verdict(ml, 200, {"id": 999})["state"] == "identity_mismatch"
    assert verdict(ml, 200, {"id": True})["state"] == "invalid_response"
    assert verdict(ml, 200, {"id": 123})["identity_match"] is True
    shopee = plan("shopee", CREDS, now=1)
    assert verdict(shopee, 200, {"shop_name": "Test"})["state"] == "identity_review"
    assert verdict(shopee, 200, {"error": "error_token_expired"})["state"] == "auth_denied"
    assert verdict(shopee, 200, {"response": {"shop_name": "Test"}})["state"] == "identity_review"
    tt = plan("tiktok", CREDS, now=1)
    assert verdict(tt, 200, {"code": False})["state"] == "invalid_response"
    assert (
        verdict(tt, 200, {"code": 0, "data": {"shops": [{"cipher": "foreign"}]}})["identity_match"]
        is False
    )
    assert (
        verdict(tt, 200, {"code": 0, "data": {"shops": [{"cipher": "cipher"}]}})["state"]
        == "read_ok"
    )
    cf = plan("carrefour", CREDS, now=1)
    assert verdict(cf, 200, {"shop_id": 123, "shop_state": "SUSPENDED"})["state"] == "shop_inactive"
    amz = plan("amazon", CREDS, now=1)
    assert verdict(amz, 200, {"payload": []})["state"] == "identity_mismatch"
    assert (
        verdict(amz, 200, {"payload": [{"marketplace": {"id": "A2Q3Y263D00KWC"}}]})["state"]
        == "identity_review"
    )


def test_redirects_rate_limits_errors_and_signed_urls_are_not_leaked():
    read = plan("shopee", CREDS, now=1)
    assert verdict(read, 302, {})["state"] == "provider_unavailable"
    assert verdict(read, 429, {})["state"] == "rate_limited"

    async def run():
        def transport(request):
            raise httpx.ReadTimeout(str(request.url))

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            result = await execute(read, client)
        assert result["state"] == "provider_unavailable"
        assert "PRIVATE" not in json.dumps(result)

    asyncio.run(run())


def proof():
    now = datetime.now(UTC)
    return {
        "version": 1,
        "source": "davinci_native_authorization_read",
        "read_only": "on",
        "no_token_refresh": True,
        "external_business_writes": False,
        "captured_at": now.isoformat(),
        "accounts": [
            {
                "integration_id": "89acc64b-bc00-4840-87cb-2248105e201b",
                "platform": "ml",
                "credentials_revision": (now - timedelta(minutes=5)).isoformat(),
                "checked_at": now.isoformat(),
                "state": "read_ok",
                "http_status": 200,
                "authorization_valid": True,
                "identity_match": True,
            }
        ],
    }


def test_local_import_is_idempotent_preserves_publisher_and_invalidates_stale_revisions(erp):
    read = proof()
    row = read["accounts"][0]
    assert not record_authorizations(erp, read)["replayed"]
    assert record_authorizations(erp, read)["replayed"]
    data = {**row}
    with erp.connection() as db:
        snapshot = authorization_snapshot(db, data)
        assert snapshot["current"] is True
        assert snapshot["publication_enabled"] is False
        data["credentials_revision"] = read["captured_at"]
        assert authorization_snapshot(db, data)["current"] is False
        data = {**row, "platform": "shopee"}
        assert authorization_snapshot(db, data) is None
    assert erp.snapshot()["orders"] == []


@pytest.mark.parametrize(
    "tamper", ["secret", "refresh", "write", "false_success", "future", "duplicate"]
)
def test_local_import_rejects_unknown_secret_fields_and_inconsistent_claims_atomically(erp, tamper):
    read = proof()
    if tamper == "secret":
        read["accounts"][0]["access_token"] = "PRIVATE"  # noqa: S105 - fictitious rejection fixture
    elif tamper == "refresh":
        read["no_token_refresh"] = False
    elif tamper == "write":
        read["external_business_writes"] = True
    elif tamper == "false_success":
        read["accounts"][0]["identity_match"] = False
    elif tamper == "future":
        read["accounts"][0]["checked_at"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    else:
        read["accounts"].append(read["accounts"][0])
    with pytest.raises(ERPError, match="formato inválido"):
        record_authorizations(erp, read)
    with erp.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM integration_authorization_reads").fetchone()[0] == 0


def test_older_proof_cannot_replace_newer_authorization_read(erp):
    newer = proof()
    record_authorizations(erp, newer)
    older = copy.deepcopy(newer)
    older["accounts"][0]["checked_at"] = (
        datetime.fromisoformat(older["captured_at"]) - timedelta(minutes=1)
    ).isoformat()
    with pytest.raises(ERPError, match="mais recente"):
        record_authorizations(erp, older)
