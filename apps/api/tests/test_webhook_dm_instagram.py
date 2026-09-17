"""Webhook de DM do Instagram — recepção (Eduardo, 16/09/2026).

Cobre o que a Meta exige e o que quebra na prática:

  • handshake devolvendo o `hub.challenge` em TEXTO PURO (JSON com aspas é a
    causa nº 1 de "The URL couldn't be validated");
  • assinatura DURA — ao contrário do webhook do Bling, que loga e nunca
    recusa, aqui payload sem assinatura válida é 403. É endpoint público que
    alimenta um robô que manda mensagem no nome da marca;
  • HMAC sobre o corpo CRU (o teste usa acento de propósito: reserializar o
    JSON quebraria a assinatura só em português);
  • `mid` repetido não duplica — a Meta reentrega por horas enquanto não vir
    200, e deploy reiniciando a api devolve não-200;
  • `is_echo` vira direção `eco` e CALA o robô na conversa: sem isso ele
    responde à própria mensagem, laço que termina com a conta bloqueada.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import DmConta, DmConversa, DmMensagem, Marca, RedeSocial
from app.security.cipher import encrypt_json

APP_SECRET = "test-meta-app-secret-0123456789"  # noqa: S105
VERIFY_TOKEN = "test-meta-verify-token-abcdef"  # noqa: S105
# Force-overwrite (como no test_webhook_bling): valor vazio vindo do .env via
# env_file sombrearia o segredo do teste se usássemos setdefault.
os.environ["META_APP_SECRET"] = APP_SECRET
os.environ["META_WEBHOOK_VERIFY_TOKEN"] = VERIFY_TOKEN
get_settings.cache_clear()  # type: ignore[attr-defined]

IG_CONTA = "17841400000000000"
IGSID = "9876543210"
URL = "/api/webhooks/meta/instagram"


def _assinar(corpo: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), corpo, hashlib.sha256).hexdigest()


def _evento(
    *,
    mid: str = "mid.abc123",
    texto: str | None = "Olá, vocês entregam em São Paulo?",
    eco: bool = False,
    apagada: bool = False,
) -> dict[str, Any]:
    msg: dict[str, Any] = {"mid": mid}
    if texto is not None:
        msg["text"] = texto
    if eco:
        msg["is_echo"] = True
    if apagada:
        msg["is_deleted"] = True
    # No eco a CONTA é quem manda; na recebida, quem recebe.
    remetente = IG_CONTA if eco else IGSID
    destino = IGSID if eco else IG_CONTA
    return {
        "object": "instagram",
        "entry": [
            {
                "id": IG_CONTA,
                "time": 1_757_000_000,
                "messaging": [
                    {
                        "sender": {"id": remetente},
                        "recipient": {"id": destino},
                        "timestamp": 1_757_000_000_000,
                        "message": msg,
                    }
                ],
            }
        ],
    }


async def _conta(db: AsyncSession) -> RedeSocial:
    m = Marca(nome="Charlots", slug="charlots-dm")
    db.add(m)
    await db.flush()
    r = RedeSocial(marca_id=m.id, plataforma="instagram", conta="charlots_br", ativo=True)
    db.add(r)
    await db.flush()
    db.add(
        DmConta(
            rede_social_id=r.id,
            ig_user_id=IG_CONTA,
            status="ok",
            token_enc=encrypt_json({"access_token": "x", "expires_at": None, "scopes": []}),
        )
    )
    await db.commit()
    await db.refresh(r)
    return r


async def _post(client: AsyncClient, payload: dict[str, Any], *, assinar: bool = True):
    corpo = json.dumps(payload, ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json"}
    if assinar:
        headers["X-Hub-Signature-256"] = _assinar(corpo)
    return await client.post(URL, content=corpo, headers=headers)


# ─────────────────────────── handshake ───────────────────────────


@pytest.mark.asyncio
async def test_handshake_devolve_challenge_em_texto_puro(client: AsyncClient):
    r = await client.get(
        URL,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "1158201444",
        },
    )
    assert r.status_code == 200
    # Sem aspas, sem JSON: a Meta compara o corpo com o challenge que mandou.
    assert r.text == "1158201444"
    assert not r.text.startswith('"')


@pytest.mark.asyncio
async def test_handshake_recusa_token_errado(client: AsyncClient):
    r = await client.get(
        URL,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "token-errado",
            "hub.challenge": "1158201444",
        },
    )
    assert r.status_code == 403


# ─────────────────────────── assinatura ───────────────────────────


@pytest.mark.asyncio
async def test_sem_assinatura_recusa(client: AsyncClient):
    r = await _post(client, _evento(), assinar=False)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_assinatura_errada_recusa(client: AsyncClient):
    corpo = json.dumps(_evento(), ensure_ascii=False).encode()
    r = await client.post(
        URL,
        content=corpo,
        headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert r.status_code == 403


# ─────────────────────────── recepção ───────────────────────────


@pytest.mark.asyncio
async def test_grava_conversa_e_mensagem(client: AsyncClient, db: AsyncSession):
    rede = await _conta(db)

    r = await _post(client, _evento())
    assert r.status_code == 200
    assert r.json()["gravadas"] == 1

    conversa = await db.scalar(
        select(DmConversa).where(DmConversa.rede_social_id == rede.id)
    )
    assert conversa is not None
    assert conversa.participante_id == IGSID
    assert conversa.ultima_recebida_em is not None  # o relógio da janela de 24h
    assert conversa.auto is True

    msg = await db.scalar(select(DmMensagem).where(DmMensagem.conversa_id == conversa.id))
    assert msg is not None
    assert msg.direcao == "recebida"
    assert msg.tipo == "texto"
    assert msg.texto == "Olá, vocês entregam em São Paulo?"
    assert msg.payload  # evento cru guardado: é o material da semana seca


@pytest.mark.asyncio
async def test_reentrega_nao_duplica(client: AsyncClient, db: AsyncSession):
    await _conta(db)
    ev = _evento(mid="mid.repetido")

    assert (await _post(client, ev)).status_code == 200
    assert (await _post(client, ev)).status_code == 200

    total = await db.scalar(
        select(func.count()).select_from(DmMensagem).where(DmMensagem.mid == "mid.repetido")
    )
    assert total == 1


@pytest.mark.asyncio
async def test_eco_cala_o_robo(client: AsyncClient, db: AsyncSession):
    rede = await _conta(db)

    r = await _post(client, _evento(mid="mid.eco", eco=True, texto="Oi, aqui é a Charlots"))
    assert r.status_code == 200

    conversa = await db.scalar(
        select(DmConversa).where(DmConversa.rede_social_id == rede.id)
    )
    assert conversa is not None
    # Humano respondeu pela caixa de entrada: o robô sai desta conversa.
    assert conversa.auto is False
    assert conversa.status == "humano"

    msg = await db.scalar(select(DmMensagem).where(DmMensagem.mid == "mid.eco"))
    assert msg is not None
    assert msg.direcao == "eco"


@pytest.mark.asyncio
async def test_apagar_esvazia_a_mensagem_original(client: AsyncClient, db: AsyncSession):
    """O apagamento chega com o MESMO `mid` da original.

    Regressão: a primeira versão fazia INSERT, morria no UNIQUE e tratava
    como reentrega — o "unsent" era um no-op. É item obrigatório da gravação
    do App Review, e guardar texto que a pessoa apagou é o oposto do que ela
    pediu.
    """
    await _conta(db)

    # 1) a mensagem original chega e é gravada com conteúdo
    assert (await _post(client, _evento(mid="mid.unsent"))).status_code == 200
    msg = await db.scalar(select(DmMensagem).where(DmMensagem.mid == "mid.unsent"))
    assert msg is not None and msg.texto  # tem texto antes

    # 2) o cliente apaga: MESMO mid
    r = await _post(client, _evento(mid="mid.unsent", texto=None, apagada=True))
    assert r.status_code == 200

    await db.refresh(msg)
    assert msg.tipo == "apagada"
    assert msg.apagada_em is not None
    assert msg.texto is None  # esvaziou, não só marcou
    assert msg.payload == {}

    # e continua sendo UMA linha só
    total = await db.scalar(
        select(func.count()).select_from(DmMensagem).where(DmMensagem.mid == "mid.unsent")
    )
    assert total == 1


@pytest.mark.asyncio
async def test_conta_desconhecida_responde_200_sem_gravar(
    client: AsyncClient, db: AsyncSession
):
    # Nenhuma conta cadastrada: não é erro. Devolver 4xx faria a Meta
    # desativar a assinatura depois de algumas entregas.
    r = await _post(client, _evento(mid="mid.orfa"))
    assert r.status_code == 200

    total = await db.scalar(select(func.count()).select_from(DmMensagem))
    assert total == 0
