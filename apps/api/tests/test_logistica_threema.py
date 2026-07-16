"""Threema Gateway (Basic mode) — client + endpoint enviar-threema.

O client faz um POST form-urlencoded por destinatário em
`msgapi.threema.ch/send_simple` (Basic mode = o servidor cifra). O endpoint
`/api/logistica/status/{id}/enviar-threema` envia a `mensagem_threema` da linha
pra todos os destinatários configurados. Config vem do `.env`
(threema_gateway_id/secret/recipients); vazia levanta ThreemaConfigError.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User, UserRole, UserStatus
from app.services import threema

os.environ["THREEMA_GATEWAY_ID"] = "*3MAGW01"
os.environ["THREEMA_GATEWAY_SECRET"] = "test-secret"
os.environ["THREEMA_RECIPIENTS"] = "ABCD1234, EFGH5678"
get_settings.cache_clear()  # type: ignore[attr-defined]


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# ---- parse_recipients (sem HTTP/DB) ----


def test_parse_recipients_variados():
    assert threema.parse_recipients("abcd1234, efgh5678") == ["ABCD1234", "EFGH5678"]
    assert threema.parse_recipients("ABCD1234 EFGH5678;IJKL9012") == [
        "ABCD1234",
        "EFGH5678",
        "IJKL9012",
    ]
    assert threema.parse_recipients("") == []
    assert threema.parse_recipients(None) == []


# ---- client (respx) ----


@pytest.mark.asyncio
async def test_send_simple_ok():
    cli = threema.ThreemaClient(gateway_id="*3MAGW01", secret="s3cr3t")
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        route = router.post("/send_simple").mock(
            return_value=httpx.Response(200, text="0a1b2c3d4e5f6789")
        )
        mid = await cli.send_simple("abcd1234", "olá")
    assert mid == "0a1b2c3d4e5f6789"
    # confere o form-urlencoded enviado (to em upper).
    sent = route.calls.last.request
    body = sent.content.decode()
    assert "from=%2A3MAGW01" in body
    assert "to=ABCD1234" in body
    assert "secret=s3cr3t" in body


@pytest.mark.asyncio
async def test_send_simple_erro_levanta():
    cli = threema.ThreemaClient(gateway_id="*3MAGW01", secret="s3cr3t")
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        router.post("/send_simple").mock(return_value=httpx.Response(401, text="access denied"))
        with pytest.raises(threema.ThreemaSendError) as ei:
            await cli.send_simple("abcd1234", "oi")
    assert ei.value.status == 401


@pytest.mark.asyncio
async def test_send_to_all_particiona_sent_failed():
    cli = threema.ThreemaClient(gateway_id="*3MAGW01", secret="s3cr3t")
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        def _resp(request: httpx.Request) -> httpx.Response:
            if b"to=EFGH5678" in request.content:
                return httpx.Response(404, text="recipient not found")
            return httpx.Response(200, text="msgid")

        router.post("/send_simple").mock(side_effect=_resp)
        out = await cli.send_to_all("aviso", recipients=["ABCD1234", "EFGH5678"])
    assert out == {"sent": ["ABCD1234"], "failed": ["EFGH5678"]}


def test_require_config_faltando():
    cli = threema.ThreemaClient(gateway_id="*3MAGW01", secret="s3cr3t")
    cli.gateway_id = ""
    cli.secret = ""
    with pytest.raises(threema.ThreemaConfigError) as ei:
        cli._require_config()
    assert "threema_gateway_id_missing" in str(ei.value)


# ---- endpoint /status/{id}/enviar-threema ----


async def _cria_status(client: AsyncClient, **extra) -> str:
    r = await client.post("/api/logistica/status", json=extra)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_enviar_threema_ok(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    sid = await _cria_status(
        client,
        status_plataforma="Devolução em trânsito",
        mensagem_threema="cliente devolveu, avisar equipe",
    )
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        router.post("/send_simple").mock(return_value=httpx.Response(200, text="mid"))
        r = await client.post(f"/api/logistica/status/{sid}/enviar-threema")
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(body["sent"]) == ["ABCD1234", "EFGH5678"]
    assert body["failed"] == []


@pytest.mark.asyncio
async def test_enviar_threema_sem_mensagem_422(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    sid = await _cria_status(client, status_plataforma="x")
    r = await client.post(f"/api/logistica/status/{sid}/enviar-threema")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "logistica_sem_mensagem_threema"


@pytest.mark.asyncio
async def test_enviar_threema_status_inexistente_404(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post(f"/api/logistica/status/{uuid.uuid4()}/enviar-threema")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "logistica_status_not_found"
