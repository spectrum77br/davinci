"""Piloto de celulares sem banco, rede, Docker ou credenciais reais.

Executar com pytest --noconftest. A aplicação mínima mantém as dependências
reais de usuário ativo/admin; só a obtenção da pessoa é substituída.
"""

import asyncio
import json
import tempfile
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.services import celulares as svc

BASE = "https://phones.example.test"
TOKEN = "unit-test-agent-token-not-a-real-secret"  # noqa: S105


def phone(name="cel5", **extra):
    return {
        "name": name, "managed": True, "running": True, "boot_completed": True,
        "desired": "running", "sidecar_running": True,
        "proxy": {
            "state": "up", "host": "private.example.test", "username": "private-user",
            "password": TOKEN, "resolved_ip": "192.0.2.1",
        },
        "egress": "proxy", "isolamento": {
            "celguard": True, "bridge_ipv6_off": True, "dns_proprio": True,
        },
        "mem_usage_mb": 1024.2, "cpu_pct": 12.5,
        "identity": {"serial": "private-serial"}, "adb_port": 5551,
        "adb_serial": "127.0.0.1:5551", "data_dir": "/private/data",
        "last_error": f"old error {TOKEN}", **extra,
    }


@pytest.fixture
def configured(monkeypatch):
    settings = SimpleNamespace(
        celulares_api_url=BASE,
        celulares_admin_token=SecretStr(TOKEN),
        celulares_allowlist="cel5,cel6",
    )
    monkeypatch.setattr(svc, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def upstream(monkeypatch):
    state = SimpleNamespace(requests=[], response=None, payload=[phone()])

    def handler(request):
        state.requests.append(request)
        if callable(state.response):
            return state.response(request)
        if state.response is not None:
            return state.response
        return httpx.Response(200, json=state.payload)

    def client(_config=None):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False,
        )

    monkeypatch.setattr(svc, "_client", client)
    return state


@pytest.fixture
def app(monkeypatch):
    # Imports de auth/db precisam de Settings, mas nenhum .env do usuário é lido.
    from app import config

    safe = config.Settings(
        _env_file=None, database_url="postgresql+asyncpg://test:test@127.0.0.1:1/unused",
    )
    monkeypatch.setattr(config, "get_settings", lambda: safe)
    from app.deps.auth import get_current_user
    from app.models import UserRole, UserStatus
    from app.routers.celulares import router
    from app.services import celulares_stream

    monkeypatch.setattr(celulares_stream, "get_settings", lambda: safe)

    application = FastAPI()
    application.include_router(router)
    application.state.user = SimpleNamespace(
        id="2f52c7e4-9de7-499a-b46b-19c403c38d91",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )

    async def current():
        return application.state.user

    application.dependency_overrides[get_current_user] = current
    return application


async def request(app, method="GET", path="/api/celulares", **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.parametrize("path", [
    "/api/celulares", "/api/celulares/cel5/start", "/api/celulares/cel5/stop",
    "/api/celulares/cel5/restart",
])
@pytest.mark.parametrize("role,status,expected", [
    (None, None, 401), ("user", "active", 403), ("admin", "suspended", 403),
    ("admin", "pending", 403),
])
async def test_real_admin_and_active_guards(
    app, configured, upstream, path, role, status, expected,
):
    app.state.user = None if role is None else SimpleNamespace(role=role, status=status)
    # require_active_user reports the enum's value for inactive accounts.
    if status is not None:
        from app.models import UserStatus

        app.state.user.status = UserStatus(status)
    result = await request(app, "GET" if path == "/api/celulares" else "POST", path)
    assert result.status_code == expected
    assert upstream.requests == []


async def test_list_filters_allowlist_and_legacy_and_redacts(app, configured, upstream):
    configured.celulares_allowlist = "cel1,cel5,cel6"
    upstream.payload = [phone("cel1", managed=False), phone("hidden"), phone("cel6"), phone()]
    result = await request(app, headers={"Authorization": "Bearer browser-secret"})
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    data = result.json()
    assert data["enabled"] is True
    assert [p["name"] for p in data["phones"]] == ["cel5", "cel6"]
    row = data["phones"][0]
    assert row["network"] == {
        "egress": "proxy", "proxy_configured": True,
        "proxy_tcp_reachable": True, "internet_confirmed": None,
    }
    assert row["has_error"] is True
    assert row["camera_available"] is False
    assert row["memory_mb"] == 1024.2
    assert row["isolation"]["celguard"] is True
    for private in (TOKEN, "browser-secret", "private-user", "private.example", "private-serial",
                    "192.0.2.1", "adb_port", "adb_serial", "/private/data", "last_error"):
        assert private not in result.text
    sent = upstream.requests[0]
    assert sent.url == f"{BASE}/v1/phones"
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert "cookie" not in sent.headers


@pytest.mark.parametrize("enabled_names,changes,expected", [
    ("vr", {}, True), (" vr ,cel5", {}, True),
    ("", {}, False), (None, {}, False), (["vr"], {}, False),
    ("VR", {}, False), ("vr,", {}, False), ("vr,../other", {}, False),
    ("other", {}, False), ("vr", {"managed": False}, False),
    ("vr", {"running": False}, False), ("vr", {"boot_completed": False}, False),
    ("vr", {"last_error": "unavailable"}, False),
])
def test_camera_badge_requires_explicit_validated_configuration(
    configured, enabled_names, changes, expected,
):
    configured.celulares_camera_enabled_names = enabled_names
    raw = phone("vr", last_error=None)
    raw.update(changes)
    result = svc.phone_out(raw)
    assert result.camera_available is expected
    assert result.camera_test is False  # Availability does not grant the offline exception.


@pytest.mark.parametrize("attr,value", [
    ("celulares_api_url", ""), ("celulares_admin_token", SecretStr("")),
    ("celulares_allowlist", ""), ("celulares_allowlist", " , "),
])
async def test_absent_config_disables_without_io(app, configured, upstream, attr, value):
    setattr(configured, attr, value)
    result = await request(app)
    assert result.status_code == 200
    assert result.json() == {"enabled": False, "phones": [], "stream_enabled": False}
    action = await request(app, "POST", "/api/celulares/cel5/start")
    assert action.status_code == 503
    assert action.json()["detail"]["code"] == "celulares_desativados"
    assert upstream.requests == []


async def test_unadded_settings_keep_feature_off(app, monkeypatch, upstream):
    monkeypatch.setattr(svc, "get_settings", lambda: SimpleNamespace())
    assert (await request(app)).json() == {"enabled": False, "phones": [], "stream_enabled": False}
    assert upstream.requests == []


@pytest.mark.parametrize("url", [
    "http://phones.example.test", "http://192.168.1.10", "ftp://phones.example.test",
    "https://user:password@phones.example.test", "https://phones.example.test?token=x",
    "https://phones.example.test/#private", "https://phones.example.test/v1",
    "https://phones.example.test:0", "https://phones.example.test:99999",
    "https://phones.example.test\\@evil.test", "https://phones.example.test\n",
    "http://localhost.evil.test", "http://127.0.0.1.evil.test", "//phones.example.test",
])
async def test_invalid_endpoint_never_receives_secret(app, configured, upstream, url):
    configured.celulares_api_url = url
    result = await request(app)
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "celulares_configuracao_invalida"
    assert TOKEN not in result.text
    assert upstream.requests == []


@pytest.mark.parametrize("url", [
    "http://localhost:8090", "http://127.0.0.1:8090", "http://[::1]:8090",
    "https://phones.example.test/",
])
async def test_https_or_explicit_loopback_allowed(configured, upstream, url):
    configured.celulares_api_url = url
    result = await svc.listar()
    assert result.enabled
    assert str(upstream.requests[0].url) == f"{url.rstrip('/')}/v1/phones"


@pytest.mark.parametrize("names", ["cel5,../other", ["cel5", 12], {"cel5": True}, "CEL5", "x" * 21])
async def test_bad_allowlist_disables_access(app, configured, upstream, names):
    configured.celulares_allowlist = names
    result = await request(app)
    assert result.status_code == 503
    assert upstream.requests == []


@pytest.mark.parametrize("token", ["short", "a" * 24 + "\nInjected:yes", "a" * 24 + "é"])
async def test_invalid_token_never_sent(app, configured, upstream, token):
    configured.celulares_admin_token = SecretStr(token)
    assert (await request(app)).status_code == 503
    assert upstream.requests == []


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
async def test_explicit_action_preflights_managed_phone(app, configured, upstream, action):
    upstream.payload = phone(running=action != "stop", boot_completed=False)
    result = await request(app, "POST", f"/api/celulares/cel5/{action}")
    assert result.status_code == 200
    assert result.json()["action"] == action
    assert result.json()["phone"]["name"] == "cel5"
    assert [(r.method, r.url.path) for r in upstream.requests] == [
        ("GET", "/v1/phones/cel5"), ("POST", f"/v1/phones/cel5/{action}"),
    ]
    assert all(r.content == b"" for r in upstream.requests)


async def test_legacy_phone_cannot_be_acted_on_even_allowlisted(app, configured, upstream):
    configured.celulares_allowlist = "cel1"
    upstream.payload = phone("cel1", managed=False)
    result = await request(app, "POST", "/api/celulares/cel1/restart")
    assert result.status_code == 404
    assert [r.method for r in upstream.requests] == ["GET"]


@pytest.mark.parametrize("name", ["cel1", "cel5-net", "cel5/../cel1", "cel5?name=cel1", "cel5%2f"])
async def test_nonallowed_or_malformed_name_never_reaches_agent(configured, upstream, name):
    with pytest.raises(svc.CelularesError) as caught:
        await svc.executar(name, "restart")
    assert caught.value.status == 404
    assert upstream.requests == []


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/celulares"), ("DELETE", "/api/celulares/cel5"),
    ("PUT", "/api/celulares/cel5/proxy"), ("POST", "/api/celulares/cel5/delete"),
    ("POST", "/api/celulares/reconcile"),
])
async def test_dangerous_agent_endpoints_are_not_exposed(app, configured, upstream, method, path):
    result = await request(app, method, path)
    assert result.status_code in (404, 405)
    assert upstream.requests == []


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 422, 429, 500])
async def test_upstream_errors_are_bounded_without_secret_or_redirect(
    app, configured, upstream, status, capsys,
):
    upstream.response = httpx.Response(
        status, headers={"Location": f"https://other.example.test/?token={TOKEN}"},
        json={"detail": TOKEN * 100},
    )
    result = await request(app)
    assert result.status_code == 503
    assert len(upstream.requests) == 1
    assert len(result.content) < 400
    assert TOKEN not in result.text
    assert TOKEN not in capsys.readouterr().out


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_transport_errors_do_not_echo_private_details(
    app, configured, upstream, error, capsys,
):
    def fail(_):
        raise error(TOKEN)

    upstream.response = fail
    result = await request(app)
    assert result.status_code == 503
    assert TOKEN not in result.text
    assert TOKEN not in capsys.readouterr().out
    assert len(upstream.requests) == 1


async def test_action_timeout_is_not_retried(app, configured, upstream):
    def handler(req):
        if req.method == "POST":
            raise httpx.ReadTimeout(TOKEN)
        return httpx.Response(200, json=phone())

    upstream.response = handler
    result = await request(app, "POST", "/api/celulares/cel5/restart")
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "celulares_tempo_esgotado"
    assert [r.method for r in upstream.requests] == ["GET", "POST"]


@pytest.mark.parametrize("payload", [
    {"name": "cel5"}, [None], [phone(), phone()], [phone(running="true")],
    [phone(boot_completed=1)],
])
async def test_malformed_list_is_not_reported_as_healthy(app, configured, upstream, payload):
    upstream.payload = payload
    result = await request(app)
    assert result.status_code == 502
    assert TOKEN not in result.text


async def test_response_size_is_bounded(app, configured, upstream, monkeypatch):
    monkeypatch.setattr(svc, "MAX_RESPONSE_BYTES", 100)
    upstream.response = httpx.Response(200, content=b" " * 101, headers={
        "Content-Type": "application/json",
    })
    assert (await request(app)).status_code == 502


@pytest.mark.parametrize("media,content", [("text/html", b"<h1>private</h1>"),
                                           ("application/json", b"not json")])
async def test_non_json_success_is_not_forwarded(app, configured, upstream, media, content):
    upstream.response = httpx.Response(200, content=content, headers={"Content-Type": media})
    result = await request(app)
    assert result.status_code == 502
    assert "private" not in result.text


async def test_action_response_must_belong_to_selected_phone(app, configured, upstream):
    upstream.response = lambda req: httpx.Response(
        200, json=phone("cel5" if req.method == "GET" else "cel6"),
    )
    result = await request(app, "POST", "/api/celulares/cel5/start")
    assert result.status_code == 502


@pytest.mark.parametrize("sidecar,state,expected", [
    (True, "up", True), (True, "down", False), (False, "down", None),
    (None, "up", None), (True, "unexpected", None),
])
def test_tcp_reachability_is_never_internet_confirmation(configured, sidecar, state, expected):
    raw = phone(sidecar_running=sidecar)
    raw["proxy"]["state"] = state
    result = svc.phone_out(raw)
    assert result.network.proxy_tcp_reachable is expected
    assert result.network.internet_confirmed is None


def test_optional_telemetry_and_unknown_fields_do_not_leak(configured):
    raw = deepcopy(phone())
    raw.update({"mem_usage_mb": float("inf"), "cpu_pct": TOKEN, "isolamento": [], "proxy": None})
    result = svc.phone_out(raw)
    assert result.memory_mb is None
    assert result.cpu_percent is None
    assert result.network.proxy_configured is False
    assert result.network.proxy_tcp_reachable is None
    assert result.isolation.celguard is None
    assert TOKEN not in result.model_dump_json()


async def test_production_http_client_disables_redirects_and_environment_proxies():
    async with svc._client() as client:
        assert client.follow_redirects is False
        assert client.trust_env is False
        assert client.timeout.connect == 3.0
        assert client.timeout.read == 5.0


def test_config_repr_never_contains_admin_token(configured):
    assert TOKEN not in repr(svc.configuracao())


@pytest.mark.parametrize("url,path", [
    ("https://phones.example.test", "/run/davinci-celulares/agent.sock"),
    ("http://localhost:8090", "/run/davinci-celulares/agent.sock"),
    ("http://localhost", "/var/run/docker.sock"),
    ("http://localhost", "/run/davinci-celulares/../docker.sock"),
])
def test_private_socket_requires_fixed_path_and_local_http(configured, url, path):
    configured.celulares_api_url = url
    configured.celulares_api_socket = path
    with pytest.raises(svc.CelularesError, match="celulares_configuracao_invalida"):
        svc.configuracao()


def test_private_socket_configuration(configured):
    configured.celulares_api_url = "http://localhost"
    configured.celulares_api_socket = "/run/davinci-celulares/agent.sock"
    assert svc.configuracao().socket_path == "/run/davinci-celulares/agent.sock"


async def test_http_agent_request_travels_through_unix_socket():
    received = []

    async def handler(reader, writer):
        received.append(await reader.readuntil(b"\r\n\r\n"))
        body = json.dumps([phone()]).encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                     + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                     + body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    with tempfile.TemporaryDirectory(prefix="cel-", dir="/tmp") as folder:
        path = f"{folder}/agent.sock"
        server = await asyncio.start_unix_server(handler, path=path)
        async with server:
            cfg = svc.AgentConfig("http://localhost", TOKEN, frozenset({"cel5"}), path)
            result = await svc._request(cfg, "GET", "/v1/phones")
    assert result[0]["name"] == "cel5"
    assert len(received) == 1
    assert b"GET /v1/phones HTTP/1.1" in received[0]
    assert f"Authorization: Bearer {TOKEN}".encode() in received[0]


async def test_missing_private_socket_never_falls_back_to_tcp():
    tcp_calls = []

    async def tcp_handler(_reader, writer):
        tcp_calls.append(True)
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(tcp_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        with tempfile.TemporaryDirectory(prefix="cel-", dir="/tmp") as folder:
            cfg = svc.AgentConfig(f"http://127.0.0.1:{port}", TOKEN, frozenset({"cel5"}),
                                  f"{folder}/missing.sock")
            with pytest.raises(svc.CelularesError, match="celulares_indisponiveis"):
                await svc._request(cfg, "GET", "/v1/phones")
    assert tcp_calls == []
