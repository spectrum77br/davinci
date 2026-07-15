"""17track — parser do push + endpoints (webhook público + register admin).

O 17track empurra o evento novo dos Correios (`...BR`); o parser
(`logistica_track.parse_push`) aceita o formato v2.2 (latest_event.address) e o
v2.4 (providers[].events[]). O webhook é protegido por segmento secreto no path
(o 17track não assina o push). O register é gated por `logistica.edit`.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Logistica, User, UserRole, UserStatus
from app.redis_client import redis
from app.services import logistica_track

WEBHOOK_SECRET = "test-17track-webhook-secret-abcdef"
os.environ["LOGI_17TRACK_WEBHOOK_SECRET"] = WEBHOOK_SECRET
get_settings.cache_clear()  # type: ignore[attr-defined]


@pytest_asyncio.fixture(autouse=True)
async def _purge_dedup() -> None:
    keys = [k async for k in redis.scan_iter("17track:push:*")]
    if keys:
        await redis.delete(*keys)
    yield
    keys = [k async for k in redis.scan_iter("17track:push:*")]
    if keys:
        await redis.delete(*keys)


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def viewer(db: AsyncSession) -> User:
    email = f"vw-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={"logistica": {"view": True}},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# ---- parser (sem HTTP/DB) ----


def test_is_correios():
    assert logistica_track.is_correios("AP178494655BR")
    assert logistica_track.is_correios(" ap178494655br ")
    assert not logistica_track.is_correios("LP12345US")
    assert not logistica_track.is_correios(None)
    assert not logistica_track.is_correios("")


def test_parse_push_v22_latest_event():
    # v2.2 — latest_event com address estruturado.
    payload = {
        "event": "TRACKING_UPDATED",
        "data": {
            "accepted": [
                {
                    "number": "AP178494655BR",
                    "track_info": {
                        "latest_event": {
                            "address": {"city": "Campinas", "state": "SP"},
                            "description": "Objeto saiu para entrega ao destinatário",
                        }
                    },
                }
            ]
        },
    }
    out = logistica_track.parse_push(payload)
    assert out == [
        ("AP178494655BR", "Campinas/SP — Objeto saiu para entrega ao destinatário")
    ]


def test_parse_push_v24_providers_events():
    # v2.4 — providers[].events[]; o mais recente é events[0].
    payload = {
        "data": {
            "accepted": [
                {
                    "number": "AP085672954BR",
                    "track_info": {
                        "providers": [
                            {
                                "events": [
                                    {
                                        "location": "Recife/PE",
                                        "description": "Objeto entregue ao destinatário",
                                    }
                                ]
                            }
                        ]
                    },
                }
            ]
        }
    }
    out = logistica_track.parse_push(payload)
    assert out == [("AP085672954BR", "Recife/PE — Objeto entregue ao destinatário")]


def test_parse_push_item_unico_e_sem_localizacao():
    # `data` como item único + item sem localização é ignorado.
    payload = {
        "data": {
            "number": "AP111111111BR",
            "track_info": {"latest_event": {}},
        }
    }
    assert logistica_track.parse_push(payload) == []


# ---- webhook público ----


@pytest.mark.asyncio
async def test_webhook_secret_errado_nao_atualiza(
    client: AsyncClient, db: AsyncSession
):
    row = Logistica(rastreio="AP178494655BR", localizacao="Enviado")
    db.add(row)
    await db.commit()
    await db.refresh(row)

    payload = {
        "data": {
            "accepted": [
                {
                    "number": "AP178494655BR",
                    "track_info": {
                        "latest_event": {
                            "address": {"city": "Campinas", "state": "SP"},
                            "description": "Saiu para entrega",
                        }
                    },
                }
            ]
        }
    }
    r = await client.post("/api/webhooks/17track/errado", json=payload)
    assert r.json() == {"ack": False}
    await db.refresh(row)
    assert row.localizacao == "Enviado"  # intacto


@pytest.mark.asyncio
async def test_webhook_atualiza_localizacao(client: AsyncClient, db: AsyncSession):
    row = Logistica(rastreio="AP178494655BR", localizacao="Enviado")
    db.add(row)
    await db.commit()
    await db.refresh(row)

    payload = {
        "data": {
            "accepted": [
                {
                    "number": "AP178494655BR",
                    "track_info": {
                        "latest_event": {
                            "address": {"city": "Campinas", "state": "SP"},
                            "description": "Saiu para entrega",
                        }
                    },
                }
            ]
        }
    }
    r = await client.post(f"/api/webhooks/17track/{WEBHOOK_SECRET}", json=payload)
    body = r.json()
    assert body["ack"] is True
    assert body["rows"] == 1
    await db.refresh(row)
    assert row.localizacao == "Campinas/SP — Saiu para entrega"

    # Reenvio idêntico é deduplicado (Redis) — não reprocessa.
    r2 = await client.post(f"/api/webhooks/17track/{WEBHOOK_SECRET}", json=payload)
    assert r2.json() == {"ack": True, "duplicate": True}


# ---- register admin ----


@pytest.mark.asyncio
async def test_register_gated_por_edit(
    client: AsyncClient, viewer: User, auth_as: Callable[[User | None], None]
):
    auth_as(viewer)
    r = await client.post("/api/logistica/17track/register")
    assert r.status_code == 403
