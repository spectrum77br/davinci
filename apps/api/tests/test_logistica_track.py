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


def test_sync_at_le_latest_sync_time_dos_providers():
    """`latest_sync_time` (quando o 17track consultou os Correios) vem em UTC
    com Z — é o que decide se vale forçar a reconsulta."""
    from datetime import UTC, datetime

    ti = {"tracking": {"providers": [{"latest_sync_time": "2026-09-08T06:36:53Z"}]}}
    assert logistica_track._sync_at(ti) == datetime(2026, 9, 8, 6, 36, 53, tzinfo=UTC)
    assert logistica_track._sync_at({"tracking": {"providers": [{}]}}) is None
    assert logistica_track._sync_at({}) is None


# ---- reconsulta forçada: cliente 17track com respostas simuladas -------------


@pytest.fixture
def fake_chamar(monkeypatch):
    """Substitui `_chamar` (o único ponto que fala HTTP nas funções novas) por
    respostas por endpoint. `respostas[endpoint]` = lista de corpos, consumida
    em ordem; `chamadas` guarda (endpoint, payload)."""
    chamadas: list[tuple[str, object]] = []
    respostas: dict[str, list] = {}

    async def _chamar(c, endpoint, payload):
        chamadas.append((endpoint, payload))
        fila = respostas.get(endpoint) or []
        if not fila:
            raise logistica_track.Track17Error(f"{endpoint}: sem resposta simulada")
        body = fila.pop(0)
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr(logistica_track, "_chamar", _chamar)
    monkeypatch.setattr(logistica_track, "_PAUSA_ENTRE_LOTES", 0)
    return chamadas, respostas


def _corpo(aceitos=(), recusados=()):
    return {
        "code": 0,
        "data": {
            "accepted": [{"number": n, "carrier": 2151} for n in aceitos],
            "rejected": [{"number": n, "error": {"code": code}} for n, code in recusados],
        },
    }


@pytest.mark.asyncio
async def test_parar_e_retomar_lote_a_lote_e_classifica_recusas(fake_chamar):
    """Stoptrack recusado por "já parado" entra no retrack mesmo assim; retrack
    recusado por "só uma vez" vira `ja_retomados` (caminho pago); número que o
    17track não conhece vira `nao_registrados`."""
    chamadas, respostas = fake_chamar
    respostas["stoptrack"] = [_corpo(["A1BR", "A2BR"], [("A3BR", -18019906), ("A4BR", -18019902)])]
    respostas["retrack"] = [_corpo(["A1BR", "A3BR"], [("A2BR", -18019905)])]

    out = await logistica_track.parar_e_retomar(["A1BR", "A2BR", "A3BR", "A4BR"])

    assert out == {
        "retomados": ["A1BR", "A3BR"],
        "ja_retomados": ["A2BR"],
        "nao_registrados": ["A4BR"],
        "parados": [],
    }
    assert [e for e, _ in chamadas] == ["stoptrack", "retrack"]
    assert [p["number"] for p in chamadas[1][1]] == ["A1BR", "A2BR", "A3BR"]


@pytest.mark.asyncio
async def test_parar_e_retomar_falha_no_retrack_marca_parados(fake_chamar):
    """Parou e a rede caiu antes de retomar: devolve em `parados` (a rodada
    seguinte retoma direto) em vez de sumir com o número."""
    _, respostas = fake_chamar
    respostas["stoptrack"] = [_corpo(["A1BR"])]
    respostas["retrack"] = [logistica_track.Track17Error("retrack: HTTP 503")]

    out = await logistica_track.parar_e_retomar(["A1BR"])

    assert out["parados"] == ["A1BR"] and out["retomados"] == []


@pytest.mark.asyncio
async def test_reregistrar_lote_a_lote_para_no_sem_quota(fake_chamar, monkeypatch):
    """Apaga e registra por lote; no primeiro "sem saldo" para de apagar e
    devolve os apagados-sem-registro pra quem chama desregistrar."""
    chamadas, respostas = fake_chamar
    monkeypatch.setattr(logistica_track, "_REGISTER_BATCH", 2)
    respostas["deletetrack"] = [_corpo(["A1BR", "A2BR"]), _corpo(["A3BR", "A4BR"])]
    registros = [
        {"ok": ["A1BR", "A2BR"], "sem_quota": False},
        {"ok": ["A3BR"], "sem_quota": True},
    ]

    async def _register(numbers):
        return registros.pop(0)

    monkeypatch.setattr(logistica_track, "register", _register)

    out = await logistica_track.reregistrar(["A1BR", "A2BR", "A3BR", "A4BR", "A5BR", "A6BR"])

    assert out == {
        "ok": ["A1BR", "A2BR", "A3BR"],
        "apagados_sem_registro": ["A4BR"],
        "sem_quota": True,
    }
    # Terceiro lote (A5/A6) NÃO foi apagado: só 2 deletetracks.
    assert [e for e, _ in chamadas] == ["deletetrack", "deletetrack"]


@pytest.mark.asyncio
async def test_estado_numeros_e_fetch_detalhado(fake_chamar):
    _, respostas = fake_chamar
    respostas["gettracklist"] = [
        {
            "code": 0,
            "page": {"data_total": 2, "page_total": 1},
            "data": {
                "accepted": [
                    {"number": "A1BR", "tracking_status": "Tracking", "is_retracked": False},
                    {
                        "number": "A2BR",
                        "tracking_status": "Stopped",
                        "is_retracked": True,
                        "stop_track_reason": "ByRequest",
                    },
                ]
            },
        }
    ]
    respostas["gettrackinfo"] = [
        {
            "code": 0,
            "data": {
                "accepted": [
                    {
                        "number": "A1BR",
                        "track_info": {
                            "latest_status": {
                                "status": "InTransit",
                                "sub_status": "InTransit_Other",
                            },
                            "latest_event": {
                                "description": "Objeto em transferência",
                                "location": "RS",
                                "address": {"city": "Passo Fundo"},
                            },
                            "tracking": {
                                "providers": [{"latest_sync_time": "2026-09-08T11:09:30Z"}]
                            },
                        },
                    }
                ],
                "rejected": [{"number": "A9BR", "error": {"code": -18019902}}],
            },
        }
    ]

    est = await logistica_track.estado_numeros(["A1BR", "A2BR", "A9BR"])
    assert est["A1BR"]["tracking_status"] == "Tracking" and est["A2BR"]["is_retracked"] is True
    assert "A9BR" not in est

    det = await logistica_track.fetch_detalhado(["A1BR", "A9BR"])
    assert det["desconhecidos"] == ["A9BR"]
    a1 = det["info"]["A1BR"]
    assert a1["localizacao"] == "Passo Fundo/RS — Objeto em transferência"
    assert a1["status"] == "InTransit" and a1["sync_at"].hour == 11


@pytest.mark.asyncio
async def test_chamar_repete_429_e_levanta_no_fim(monkeypatch):
    """429/5xx/rede: repete com pausa; se persistir, `Track17Error` (quem decide
    gastar crédito não pode tratar como "sem dados")."""
    import httpx

    monkeypatch.setattr(logistica_track.asyncio, "sleep", _sem_espera)
    tentativas = []

    class _Resp:
        def __init__(self, status, body=None):
            self.status_code = status
            self._body = body

        def json(self):
            if self._body is None:
                raise ValueError("no json")
            return self._body

    class _Client:
        def __init__(self, fila):
            self.fila = fila

        async def post(self, url, headers=None, json=None):
            tentativas.append(url.rsplit("/", 1)[-1])
            r = self.fila.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

    ok = _Resp(200, {"code": 0, "data": {"accepted": [], "rejected": []}})
    body = await logistica_track._chamar(
        _Client([_Resp(429), httpx.ConnectError("x"), ok]), "retrack", []
    )
    assert body["code"] == 0 and len(tentativas) == 3

    with pytest.raises(logistica_track.Track17Error):
        await logistica_track._chamar(_Client([_Resp(500), _Resp(500), _Resp(500)]), "retrack", [])
    with pytest.raises(logistica_track.Track17Error):
        await logistica_track._chamar(
            _Client([_Resp(200, {"code": 401, "data": None})]), "retrack", []
        )


async def _sem_espera(_s):
    return None
