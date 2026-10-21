"""Ingresso do piloto, ASGI e interoperabilidade local Python/Node.

Nenhum teste usa servidor, banco, aparelhos ou credenciais reais.
Executar junto a test_celulares.py com pytest --noconftest.
"""

import base64
import json
import os
import hashlib
import hmac
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from pydantic import SecretStr

from app.services import celulares_stream as stream
from tests.unit import test_celulares as helpers

# Reuse the same isolated settings, ASGI and MockTransport fixtures.
app = helpers.app
configured = helpers.configured
upstream = helpers.upstream
phone = helpers.phone
request = helpers.request

ORIGIN = "https://screen.example.test"
LOGIN_ORIGIN = "https://davinci.example.test"
KEY = "synthetic-stream-key-never-used-outside-tests"  # noqa: S105
USER_ID = "2f52c7e4-9de7-499a-b46b-19c403c38d91"
NOW = 1_800_000_000


@pytest.fixture
def stream_configured(monkeypatch, configured):
    configured.celulares_stream_origin = ORIGIN
    configured.celulares_stream_key = SecretStr(KEY)
    configured.jwt_secret = "unrelated-test-only-login-key"  # noqa: S105
    monkeypatch.setattr(stream, "get_settings", lambda: configured)
    return configured


@pytest.mark.parametrize("role,status,expected", [
    (None, None, 401), ("user", "active", 403), ("admin", "suspended", 403),
    ("admin", "pending", 403),
])
async def test_session_uses_real_active_admin_guards(
    app, stream_configured, upstream, role, status, expected,
):
    from app.models import UserStatus

    app.state.user = None if role is None else SimpleNamespace(
        id=USER_ID, role=role, status=UserStatus(status),
    )
    result = await request(app, "POST", "/api/celulares/cel5/session")
    assert result.status_code == expected
    assert "ticket" not in result.text
    assert upstream.requests == []


async def test_session_returns_only_fixed_https_post_target_and_ticket(
    app, stream_configured, upstream, monkeypatch,
):
    monkeypatch.setattr(stream.time, "time", lambda: NOW)
    upstream.payload = phone()
    result = await request(
        app, "POST", "/api/celulares/cel5/session",
        json={"url": "https://attacker.example/session", "phone": "cel6", "udid": "other"},
        headers={"Origin": "https://attacker.example", "Authorization": "Bearer browser-secret"},
    )
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["referrer-policy"] == "no-referrer"
    assert result.headers["x-content-type-options"] == "nosniff"
    data = result.json()
    assert set(data) == {"url", "ticket"}
    assert data["url"] == ORIGIN + "/session"
    assert urlsplit(data["url"]).query == ""
    assert data["ticket"] not in data["url"]
    assert all(secret not in result.text for secret in (KEY, "browser-secret", "attacker.example"))
    parts = data["ticket"].split(".")
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    assert payload["sub"] == USER_ID
    assert payload["phone"] == "cel5"
    assert payload["udid"] == "127.0.0.1:5551"
    assert payload["exp"] - payload["iat"] == 60
    assert [(r.method, r.url.path) for r in upstream.requests] == [("GET", "/v1/phones/cel5")]
    expected_signature = base64.urlsafe_b64encode(hmac.new(
        KEY.encode(), f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256,
    ).digest()).decode().rstrip("=")
    assert hmac.compare_digest(parts[2], expected_signature)


@pytest.mark.parametrize("change", [
    {"running": False}, {"boot_completed": False},
    {"running": "true"}, {"boot_completed": 1},
    {"isolamento": None}, {"isolamento": {}}, {"isolamento": ["celguard"]},
    {"isolamento": "all good"},
    *[{"egress": value} for value in ("direct-LEAK", "direct", "blocked", "unknown", None)],
    *[{"isolamento": {"celguard": True, "bridge_ipv6_off": True, "dns_proprio": True,
                      key: value}}
      for key in ("celguard", "bridge_ipv6_off", "dns_proprio")
      for value in (False, None, "true", 1)],
])
async def test_unready_or_unconfirmed_isolation_never_issues_session(
    app, stream_configured, upstream, change,
):
    upstream.payload = phone(**change)
    result = await request(app, "POST", "/api/celulares/cel5/session")
    malformed_state = any(
        key in change and not isinstance(change[key], bool)
        for key in ("running", "boot_completed")
    )
    assert result.status_code == (502 if malformed_state else 409)
    assert result.json()["detail"]["code"] == (
        "celulares_resposta_invalida" if malformed_state else "celular_nao_pronto"
    )
    assert "ticket" not in result.text
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("serial", [None, "", "192.0.2.1:5555", "127.0.0.1:65536",
                                        "127.0.0.1:5555?secret", "localhost:5555"])
async def test_invalid_agent_target_is_bounded_without_ticket(
    app, stream_configured, upstream, serial,
):
    upstream.payload = phone(adb_serial=serial)
    result = await request(app, "POST", "/api/celulares/cel5/session")
    assert result.status_code == 502
    assert result.json()["detail"]["code"] == "celular_acesso_invalido"
    assert "ticket" not in result.text
    assert result.headers["cache-control"] == "no-store"


async def test_session_rejects_unlisted_phone_without_contacting_agent(
    app, stream_configured, upstream,
):
    result = await request(app, "POST", "/api/celulares/cel1/session")
    assert result.status_code == 404
    assert result.headers["cache-control"] == "no-store"
    assert upstream.requests == []


async def test_allowlisted_legacy_phone_cannot_open_session(app, stream_configured, upstream):
    stream_configured.celulares_allowlist = "cel1"
    upstream.payload = phone("cel1", managed=False)
    result = await request(app, "POST", "/api/celulares/cel1/session")
    assert result.status_code == 404
    assert "ticket" not in result.text
    assert len(upstream.requests) == 1


@pytest.mark.parametrize("attr,value", [
    ("celulares_stream_origin", ""), ("celulares_stream_key", SecretStr("")),
    ("celulares_stream_key", SecretStr("short")), ("jwt_secret", KEY),
    ("jwt_secret", SecretStr(KEY)),
])
async def test_missing_or_shared_configuration_hides_stream_and_rejects_session(
    app, stream_configured, upstream, attr, value,
):
    setattr(stream_configured, attr, value)
    listing = await request(app)
    assert listing.status_code == 200
    assert listing.json()["stream_enabled"] is False
    upstream.requests.clear()
    result = await request(app, "POST", "/api/celulares/cel5/session")
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "celulares_acesso_em_preparacao"
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["referrer-policy"] == "no-referrer"
    assert upstream.requests == []
    assert KEY not in result.text


async def test_valid_configuration_reports_stream_capability(app, stream_configured, upstream):
    result = await request(app)
    assert result.status_code == 200
    assert result.json()["stream_enabled"] is True


def camera_phone(**changes):
    return phone(changes.pop("name", "vrcamera"), **{
        "read_only": True, "purpose": "camera-test", "pilot_verified": True,
        "egress": "blocked", "proxy": None, "adb_serial": "127.0.0.1:5559",
        "isolamento": {"docker_internal": True}, "last_error": None, **changes,
    })


async def test_explicit_camera_pilot_opens_without_claiming_proxy(app, stream_configured, upstream):
    stream_configured.celulares_allowlist = "vrcamera"
    stream_configured.celulares_camera_pilot_enabled = True
    upstream.payload = [camera_phone()]
    row = (await request(app)).json()["phones"][0]
    assert row["read_only"] is True and row["camera_test"] is True
    assert row["network"]["egress"] == "blocked"
    assert row["network"]["proxy_configured"] is False
    assert row["isolation"]["celguard"] is None
    upstream.payload = camera_phone()
    result = await request(app, "POST", "/api/celulares/vrcamera/session")
    assert result.status_code == 200
    assert "ticket" in result.json()


@pytest.mark.parametrize("change", [
    {"read_only": False}, {"purpose": None}, {"pilot_verified": False},
    {"pilot_verified": 1}, {"egress": "proxy"}, {"egress": "direct-LEAK"},
    {"proxy": {}}, {"adb_serial": "127.0.0.1:5557"},
    {"isolamento": {"docker_internal": False}}, {"isolamento": None},
    {"last_error": "test failure"}, {"running": False}, {"boot_completed": False},
])
async def test_camera_pilot_requires_every_verified_condition(
    app, stream_configured, upstream, change,
):
    stream_configured.celulares_allowlist = "vrcamera"
    stream_configured.celulares_camera_pilot_enabled = True
    upstream.payload = camera_phone(**change)
    result = await request(app, "POST", "/api/celulares/vrcamera/session")
    assert result.status_code == 409
    assert "ticket" not in result.text


async def test_camera_pilot_disabled_by_default_even_if_agent_reports_ready(
    app, stream_configured, upstream,
):
    stream_configured.celulares_allowlist = "vrcamera"
    upstream.payload = camera_phone()
    result = await request(app, "POST", "/api/celulares/vrcamera/session")
    assert result.status_code == 409
    assert "ticket" not in result.text


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
async def test_camera_pilot_lifecycle_never_reaches_agent(app, stream_configured, upstream, action):
    stream_configured.celulares_allowlist = "vrcamera"
    stream_configured.celulares_camera_pilot_enabled = True
    upstream.payload = camera_phone()
    result = await request(app, "POST", f"/api/celulares/vrcamera/{action}")
    assert result.status_code == 409
    assert [r.method for r in upstream.requests] == ["GET"]


async def test_camera_flags_cannot_relax_regular_phone(app, stream_configured, upstream):
    stream_configured.celulares_camera_pilot_enabled = True
    upstream.payload = camera_phone(name="cel5")
    result = await request(app, "POST", "/api/celulares/cel5/session")
    assert result.status_code == 409
    assert "ticket" not in result.text


@pytest.mark.parametrize("origin", [
    "http://screen.example.test", "http://127.0.0.1:8000", "//screen.example.test",
    "https://screen.example.test:444", "https://screen.example.test:99999",
    "https://screen.example.test/session", "https://screen.example.test?ticket=x",
    "https://screen.example.test/#fragment", "https://user:secret@screen.example.test",
    "https://@screen.example.test", "https://screen.example.test\n",
    " https://screen.example.test", "https://screen.example.test\\suffix",
])
def test_stream_origin_must_be_https_fixed_origin(stream_configured, origin):
    stream_configured.celulares_stream_origin = origin
    assert stream.configuration() is None


def test_unicode_settings_do_not_raise_and_cannot_share_login_key(stream_configured):
    # Both Python and Node encode the dedicated HMAC key as UTF-8.
    stream_configured.celulares_stream_key = SecretStr("á" * 40)
    assert stream.configuration() == (ORIGIN, "á" * 40)
    stream_configured.jwt_secret = "á" * 40
    assert stream.configuration() is None


async def test_upstream_failure_does_not_leak_secrets_in_session(
    app, stream_configured, upstream,
):
    upstream.response = httpx.Response(500, json={"detail": KEY * 100})
    result = await request(app, "POST", "/api/celulares/cel5/session")
    assert result.status_code == 503
    assert KEY not in result.text
    assert "ticket" not in result.text
    assert len(result.text) < 300
    assert result.headers["cache-control"] == "no-store"


def exchange_in_node(ticket, **changes):
    """Use the actual local gateway verifier. No server process or socket is opened."""
    configured_module = os.environ.get("CELULARES_GATEWAY_TEST_MODULE")
    if not configured_module:
        pytest.skip("Optional external gateway contract: set CELULARES_GATEWAY_TEST_MODULE")
    gateway = Path(configured_module).resolve()
    assert gateway.is_file(), "Configured gateway verifier does not exist"
    node = shutil.which("node")
    assert node, "Node is required for the explicitly configured gateway contract"
    data = {
        "module": str(gateway), "key": KEY, "phones": {"cel5": "127.0.0.1:5551"},
        "publicOrigin": ORIGIN, "loginOrigin": LOGIN_ORIGIN, "origin": LOGIN_ORIGIN,
        "boot": (NOW - 10) * 1000, "now": NOW * 1000, "ticket": ticket, **changes,
    }
    script = """
const fs = require('node:fs');
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
const { Sessions } = require(data.module);
let current = data.boot;
const sessions = new Sessions({...data, now: () => current});
current = data.now;
const result = sessions.exchange(data.ticket, data.origin);
if (!result) { process.stdout.write(JSON.stringify({accepted:false})); }
else {
  const {id,proof,session} = result;
  const cookie = sessions.cookie(id,proof);
  process.stdout.write(JSON.stringify({accepted:true,user:session.user,phone:session.phone,
    target:session.udid,replayAccepted:!!sessions.exchange(data.ticket,data.origin),
    cookieWorks:!!sessions.get(cookie.split(';')[0],id),
    cookieSecure:cookie.includes('; Secure'),cookieHttpOnly:cookie.includes('; HttpOnly'),
    expires:session.expires,player:sessions.playerUrl(session,id)}));
}
"""
    result = subprocess.run(  # noqa: S603
        [node, "-e", script], input=json.dumps(data), capture_output=True, text=True,
        check=True, timeout=5,
    )
    return json.loads(result.stdout)


def issue_for_test(**changes):
    return stream.issue_ticket(**{
        "user_id": USER_ID, "phone": "cel5", "udid": "127.0.0.1:5551", "key": KEY,
        "now": NOW, **changes,
    })


def test_python_ticket_is_accepted_once_by_actual_node_gateway():
    ticket = issue_for_test()
    result = exchange_in_node(ticket)
    assert result["accepted"] is True
    assert result["user"] == USER_ID
    assert result["phone"] == "cel5"
    assert result["target"] == "127.0.0.1:5551"
    assert result["replayAccepted"] is False
    assert result["cookieWorks"] and result["cookieSecure"] and result["cookieHttpOnly"]
    assert result["expires"] == (NOW + 900) * 1000
    assert ticket not in result["player"]


@pytest.mark.parametrize("changes", [
    {"key": "a-different-synthetic-gateway-secret-key"},
    {"origin": "https://other.example.test"}, {"now": (NOW + 60) * 1000},
    {"now": (NOW - 6) * 1000}, {"phones": {"cel6": "127.0.0.1:5551"}},
    {"phones": {"cel5": "127.0.0.1:5552"}},
    {"boot": (NOW + 1) * 1000, "now": (NOW + 10) * 1000},
])
def test_actual_gateway_rejects_wrong_context_or_expired_python_ticket(changes):
    assert exchange_in_node(issue_for_test(), **changes) == {"accepted": False}


def test_actual_gateway_rejects_tampered_python_payload():
    version, body, signature = issue_for_test().split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["sub"] = "other-admin"
    tampered_body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    assert exchange_in_node(f"{version}.{tampered_body}.{signature}") == {"accepted": False}


def test_each_issued_ticket_has_unique_nonce():
    assert issue_for_test() != issue_for_test()


def test_utf8_python_hmac_key_is_interoperable_with_node():
    key = "á" * 40
    assert exchange_in_node(issue_for_test(key=key), key=key)["accepted"] is True
