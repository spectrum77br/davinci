# ruff: noqa: S105  (tokens de teste de um cliente falso, nada real)
"""Marcar a conversa como LIDA ao abrir (Eduardo, 14/10/2026).

"Ao passar também tem que marcar como visto, igual lá no Duoke quando clicar
na conversa." POST /api/atendimento/conversas/{id}/lida, com a trava da fase
de observação ligada (como em produção). Sem rede: a plataforma é um cliente
FALSO que só anota o que pediram — nenhum pedido sai para Shopee, TikTok ou
ML daqui (os formatos dos métodos novos dos clientes de verdade são
conferidos sobre um transporte falso, no fim).

O que se mede:

- quem MEXE (ATENDIMENTO_USUARIOS) numa loja em auto, copiloto ou humano →
  a plataforma é chamada com os ids certos (Shopee: a conversa e a última
  mensagem que não é da loja; TikTok: a conversa; ML: o pack e o vendedor)
  e `nao_lidas` zera;
- quem só lê (até admin fora da lista) → 403 e nada é chamado;
- loja em `observar`, envio desligado, plataforma sem "lida" (Amazon,
  pergunta do ML, Direct) → 409 com o motivo e nada é chamado;
- a plataforma falhou (erro, sem escopo, cliente que não monta) → 200 com
  `marcada=false` e o aviso, e `nao_lidas` NÃO zera;
- passou do tempo → a rota responde, mas a chamada NÃO é cancelada: segue
  blindada até o fim (o token renovado no meio chega ao banco — refresh token
  de uso único) e a trava só solta quando ela acaba;
- Shopee: o ponteiro é a mais recente que não é resposta da loja — inclusive
  a automática da própria loja, que a Shopee conta no não lido;
- idempotente: já lida não vai à plataforma; duas abas juntas vão uma vez;
- chegou mensagem no meio → marcou, mas o número fica com o sync;
- o detalhe diz `marcar_lida` com as MESMAS travas (a tela só chama com ele);
- a IA, os crons e os robôs nunca marcam: sem pessoa, o serviço recusa, e no
  código só a rota chama o serviço e só o serviço chama os métodos que marcam.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    Integration,
    IntegrationPlatform,
    User,
    UserRole,
)
from app.redis_client import redis
from app.routers import atendimento as rota
from app.security.cipher import decrypt_json, encrypt_json
from app.services.atendimento import clientes, contexto, gravar, lida
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient
from app.services.marketplaces.tiktok import TikTokClient

URL = "/api/atendimento"
AGORA = datetime.now(UTC)
DONO = "dono@davinci-test.com"
APP = Path(__file__).resolve().parents[1] / "app"
# O httpx de verdade (os testes trocam o `AsyncClient` por um com transporte falso).
_AsyncClientReal = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _fase_de_observacao(monkeypatch):
    """Como em produção: trava ligada, a lista com UM e-mail e o envio ligado
    (a Kia responde pelo DaVinci); nada de IA, leitura ou simulador."""
    monkeypatch.setattr(rota, "SO_ADMIN", True)
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_usuarios", DONO)
    for nome in (
        "atendimento_leitura_ativa",
        "atendimento_ia_ativa",
        "atendimento_auto_ativo",
        "atendimento_simulador",
        "atendimento_alerta_telegram",
    ):
        monkeypatch.setattr(s, nome, False)
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    return s


@pytest.fixture(autouse=True)
def _contexto_falso(monkeypatch):
    async def contexto_da_conversa(session, conversa):
        return {"pedido": None, "logistica": None, "chamados": [], "devolucoes": []}

    monkeypatch.setattr(contexto, "contexto_da_conversa", contexto_da_conversa)


# ─────────────── a plataforma falsa ───────────────


class Plataforma:
    """Os três clientes falsos de uma vez: anota cada pedido de "marcar"."""

    def __init__(self) -> None:
        self.chamadas: list[tuple] = []
        self.falha: BaseException | None = None
        self.tiktok_code: int = 0
        self.no_meio = None  # corrotina rodada DURANTE a chamada (mensagem chegando)
        self.montados: list[str] = []
        self.montar_falha: BaseException | None = None

    async def _talvez(self) -> None:
        if self.no_meio is not None:
            await self.no_meio()
        if self.falha is not None:
            raise self.falha

    def cliente(self, integration: Integration) -> Any:
        dono = self
        plataforma = str(integration.platform)
        dono.montados.append(plataforma)

        class Shopee:
            creds = {"shop_id": 1, "access_token": "t"}

            async def chat_read_conversation(self, conversation_id, last_read_message_id):
                dono.chamadas.append(("shopee", str(conversation_id), str(last_read_message_id)))
                await dono._talvez()
                return {}

        class TikTok:
            creds = {"access_token": "t"}

            async def cs_read_message(self, conversation_id):
                dono.chamadas.append(("tiktok", str(conversation_id)))
                await dono._talvez()
                return {"code": dono.tiktok_code, "message": "x", "data": {}}

        class ML:
            creds = {"access_token": "t", "user_id": 999}

            async def marcar_pack_como_lido(self, pack_id, seller_id, *, limite, max_paginas):
                dono.chamadas.append(("ml", str(pack_id), str(seller_id), limite, max_paginas))
                await dono._talvez()
                return 3

        return {"shopee": Shopee, "tiktok": TikTok, "ml": ML}[plataforma]()


@pytest.fixture
def plataforma(monkeypatch) -> Plataforma:
    p = Plataforma()

    async def fabrica(integration):
        if p.montar_falha is not None:
            raise p.montar_falha
        return p.cliente(integration)

    monkeypatch.setattr(clientes, "cliente_da_integracao", fabrica)
    return p


# ─────────────── fábrica ───────────────

_PLAT = {
    "shopee": IntegrationPlatform.SHOPEE,
    "tiktok": IntegrationPlatform.TIKTOK,
    "ml": IntegrationPlatform.ML,
    "amazon": IntegrationPlatform.AMAZON,
}


async def _loja(
    db, dono: User, plataforma: str = "shopee", canal: str = "chat", modo: str = "auto"
) -> tuple[Integration, AtendimentoCanal]:
    integ = Integration(
        user_id=dono.id,
        platform=_PLAT[plataforma],
        name=f"{plataforma}-{canal}",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    c = AtendimentoCanal(
        integration_id=integ.id, plataforma=plataforma, canal=canal, modo=modo, status="ok"
    )
    db.add(c)
    await db.commit()
    return integ, c


async def _conversa(
    db,
    integ: Integration,
    canal: AtendimentoCanal,
    externo_id: str = "2441045667828072817",
    *,
    nao_lidas: int = 3,
    mensagens: list[tuple[str, str, int]] | None = None,
) -> AtendimentoConversa:
    """`mensagens` = (externo_id, autor, minutos atrás)."""
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma=canal.plataforma,
        canal_nome=canal.canal,
        externo_id=externo_id,
        pedido_marketplace=None,
        comprador_nome="Comprador",
        nao_lidas=nao_lidas,
    )
    padrao = [("7000000000000000001", "cliente", 30), ("7000000000000000002", "cliente", 20)]
    for mid, autor, minutos in mensagens if mensagens is not None else padrao:
        await gravar.gravar_mensagem(
            db,
            conversa,
            externo_id=mid,
            autor=autor,
            texto=f"mensagem {mid}",
            enviada_em=AGORA - timedelta(minutes=minutos),
        )
    conversa.nao_lidas = nao_lidas
    await db.commit()
    await db.refresh(conversa)
    return conversa


@pytest.fixture
async def dono(make_user, auth_as) -> User:
    u = await make_user(email=DONO, role=UserRole.ADMIN)
    auth_as(u)
    return u


async def _nao_lidas(db, conversa: AtendimentoConversa | str) -> int:
    """O número NO BANCO (a coluna, não o objeto da sessão do teste)."""
    cid = conversa if isinstance(conversa, str) else str(conversa.id)
    return int(
        await db.scalar(select(AtendimentoConversa.nao_lidas).where(AtendimentoConversa.id == cid))
    )


async def _marcar(client, conversa: AtendimentoConversa | str):
    cid = conversa if isinstance(conversa, str) else str(conversa.id)
    return await client.post(f"{URL}/conversas/{cid}/lida")


# ─────────────── quem mexe, loja em que o DaVinci responde ───────────────


@pytest.mark.parametrize("modo", ["auto", "copiloto", "humano"])
async def test_shopee_marca_ate_a_ultima_de_fora_e_zera(db, dono, client, plataforma, modo):
    integ, canal = await _loja(db, dono, "shopee", "chat", modo)
    conversa = await _conversa(
        db,
        integ,
        canal,
        mensagens=[
            ("7000000000000000001", "cliente", 40),
            ("7000000000000000002", "cliente", 30),
            # A nossa resposta (e a do Duoke) não é "de fora": não é o ponteiro.
            ("7000000000000000003", "loja", 20),
            # Notificação/afiliado conta no "não lido" da Shopee: é a última de fora.
            ("7000000000000000004", "sistema", 10),
            ("7000000000000000005", "loja", 5),
        ],
    )
    r = await _marcar(client, conversa)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["marcada"] is True and corpo["motivo"] is None and corpo["aviso"] is None
    assert corpo["conversa"]["nao_lidas"] == 0
    assert plataforma.chamadas == [("shopee", "2441045667828072817", "7000000000000000004")]
    assert await _nao_lidas(db, conversa) == 0


async def test_shopee_a_automatica_da_propria_loja_e_o_ponteiro(db, dono, client, plataforma):
    """A resposta AUTOMÁTICA da loja (`auto_reply`, com o `from_shop_id` da
    própria loja) entra no não lido do vendedor na Shopee — medido em produção
    em 14/10/2026: conversas com não lidas e SÓ automáticas da loja (a f7b34b27
    da Kia: 5 automáticas, 2 não lidas). Parar na do comprador deixaria a
    automática não lida lá com o número zerado aqui."""
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal, mensagens=[])
    loja = {"from_shop_id": 1, "status": "auto_reply"}  # o shop_id do cliente falso
    for mid, autor, minutos, payload in (
        ("7000000000000000001", "cliente", 40, {"from_shop_id": 555, "status": "user_chat"}),
        ("7000000000000000002", "sistema", 39, loja),
        ("7000000000000000003", "loja", 30, {"from_shop_id": 1, "status": "normal"}),
        ("7000000000000000004", "sistema", 10, loja),
    ):
        await gravar.gravar_mensagem(
            db,
            conversa,
            externo_id=mid,
            autor=autor,
            texto=f"mensagem {mid}",
            enviada_em=AGORA - timedelta(minutes=minutos),
            payload=payload,
        )
    await db.commit()
    assert (await _marcar(client, conversa)).json()["marcada"] is True
    assert plataforma.chamadas == [("shopee", "2441045667828072817", "7000000000000000004")]
    assert await _nao_lidas(db, conversa) == 0

    # Só automáticas da loja, nenhuma do comprador gravada: a mais recente.
    plataforma.chamadas.clear()
    so_auto = await _conversa(
        db,
        integ,
        canal,
        "2441045667828072999",
        nao_lidas=2,
        mensagens=[("7100000000000000001", "sistema", 50), ("7100000000000000002", "sistema", 1)],
    )
    assert (await _marcar(client, so_auto)).json()["marcada"] is True
    assert plataforma.chamadas == [("shopee", "2441045667828072999", "7100000000000000002")]


async def test_shopee_pula_id_que_nao_e_da_shopee(db, dono, client, plataforma):
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(
        db,
        integ,
        canal,
        mensagens=[("7000000000000000002", "cliente", 30), ("sim:abc", "cliente", 5)],
    )
    assert (await _marcar(client, conversa)).json()["marcada"] is True
    assert plataforma.chamadas == [("shopee", "2441045667828072817", "7000000000000000002")]


async def test_shopee_sem_mensagem_de_fora_nao_chama_nem_zera(db, dono, client, plataforma):
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal, mensagens=[("7000000000000000003", "loja", 5)])
    r = await _marcar(client, conversa)
    assert r.status_code == 200
    assert r.json()["marcada"] is False and r.json()["motivo"] == lida.MOTIVO_SEM_MENSAGEM
    assert r.json()["aviso"]
    assert plataforma.chamadas == []
    assert await _nao_lidas(db, conversa) == 3


async def test_tiktok_marca_a_conversa_e_zera(db, dono, client, plataforma):
    integ, canal = await _loja(db, dono, "tiktok", "chat", "copiloto")
    conversa = await _conversa(db, integ, canal, "7494560109732334001")
    r = await _marcar(client, conversa)
    assert r.status_code == 200 and r.json()["marcada"] is True
    assert plataforma.chamadas == [("tiktok", "7494560109732334001")]
    assert await _nao_lidas(db, conversa) == 0


async def test_tiktok_sem_escopo_nao_zera(db, dono, client, plataforma):
    """Hoje as lojas da TikTok estão sem `seller.customer_service` (105005)."""
    plataforma.tiktok_code = 105005
    integ, canal = await _loja(db, dono, "tiktok", "chat", "humano")
    conversa = await _conversa(db, integ, canal, "7494560109732334001")
    r = await _marcar(client, conversa)
    assert r.status_code == 200
    assert r.json() | {"conversa": None} == {
        "marcada": False,
        "motivo": lida.MOTIVO_PLATAFORMA_FALHOU,
        "aviso": lida.AVISO_FALHOU,
        "conversa": None,
    }
    assert len(plataforma.chamadas) == 1
    assert await _nao_lidas(db, conversa) == 3


async def test_ml_pos_venda_le_o_pack_marcando_e_zera(db, dono, client, plataforma):
    integ, canal = await _loja(db, dono, "ml", "pos_venda", "humano")
    conversa = await _conversa(db, integ, canal, "2000012345678901")
    r = await _marcar(client, conversa)
    assert r.status_code == 200 and r.json()["marcada"] is True
    assert plataforma.chamadas == [("ml", "2000012345678901", "999", 20, 5)]
    assert await _nao_lidas(db, conversa) == 0


# ─────────────── quem NÃO marca ───────────────


async def test_quem_so_le_leva_403_e_nada_e_chamado(db, make_user, auth_as, client, plataforma):
    dono_loja = await make_user(email=DONO, role=UserRole.ADMIN)
    integ, canal = await _loja(db, dono_loja)
    conversa = await _conversa(db, integ, canal)
    for leitor in (
        await make_user(),  # equipe
        await make_user(role=UserRole.ADMIN),  # admin fora de ATENDIMENTO_USUARIOS
    ):
        auth_as(leitor)
        r = await _marcar(client, conversa)
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["code"] == "atendimento_so_leitura"
    assert plataforma.chamadas == [] and plataforma.montados == []
    assert await _nao_lidas(db, conversa) == 3


async def test_loja_em_observar_recusa_e_nada_muda(db, dono, client, plataforma):
    integ, canal = await _loja(db, dono, "shopee", "chat", "observar")
    conversa = await _conversa(db, integ, canal)
    r = await _marcar(client, conversa)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == lida.RECUSA_CANAL_EM_OBSERVACAO
    assert "Duoke" in r.json()["detail"]["detail"]
    assert plataforma.chamadas == [] and plataforma.montados == []
    assert await _nao_lidas(db, conversa) == 3


async def test_envio_desligado_recusa(db, dono, client, plataforma, _fase_de_observacao):
    _fase_de_observacao.atendimento_envio_ativo = False
    integ, canal = await _loja(db, dono, "shopee", "chat", "auto")
    conversa = await _conversa(db, integ, canal)
    r = await _marcar(client, conversa)
    assert r.status_code == 409 and r.json()["detail"]["code"] == lida.RECUSA_ENVIO_DESLIGADO
    assert plataforma.chamadas == []


@pytest.mark.parametrize(
    ("plat", "canal_nome", "externo"),
    [("ml", "pergunta", "q:123"), ("amazon", "email", "<m@amazon.com>")],
)
async def test_plataforma_sem_lida_recusa(db, dono, client, plataforma, plat, canal_nome, externo):
    integ, canal = await _loja(db, dono, plat, canal_nome, "humano")
    conversa = await _conversa(db, integ, canal, externo)
    r = await _marcar(client, conversa)
    assert r.status_code == 409 and r.json()["detail"]["code"] == lida.RECUSA_NAO_SE_APLICA
    assert plataforma.chamadas == []
    assert await _nao_lidas(db, conversa) == 3


async def test_direct_do_instagram_e_loja_desconectada(db, dono, client, plataforma):
    r = await _marcar(client, "ig:00000000-0000-0000-0000-000000000000")
    assert r.status_code == 409 and r.json()["detail"]["code"] == lida.RECUSA_NAO_SE_APLICA
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    await db.execute(
        update(Integration).where(Integration.id == integ.id).values(archived_at=AGORA)
    )
    await db.commit()
    r = await _marcar(client, conversa)
    assert r.status_code == 409 and r.json()["detail"]["code"] == lida.RECUSA_SEM_INTEGRACAO
    assert (await _marcar(client, "00000000-0000-0000-0000-000000000000")).status_code == 404
    assert plataforma.chamadas == []


# ─────────────── a plataforma falhou: não zera, a tela segue ───────────────


@pytest.mark.parametrize(
    "falha",
    [
        RuntimeError("shopee_chat_lida error_param: oi, meu pedido 123 não chegou"),
        httpx.ConnectTimeout("timeout"),
        TimeoutError(),
    ],
)
async def test_falha_da_plataforma_responde_ok_com_aviso_e_nao_zera(
    db, dono, client, plataforma, falha
):
    plataforma.falha = falha
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    r = await _marcar(client, conversa)
    assert r.status_code == 200, r.text
    assert r.json()["marcada"] is False
    assert r.json()["motivo"] == lida.MOTIVO_PLATAFORMA_FALHOU
    assert r.json()["aviso"] == lida.AVISO_FALHOU
    assert r.json()["conversa"]["nao_lidas"] == 3
    assert len(plataforma.chamadas) == 1
    assert await _nao_lidas(db, conversa) == 3
    # A trava foi solta: a próxima abertura tenta de novo.
    plataforma.falha = None
    assert (await _marcar(client, conversa)).json()["marcada"] is True
    assert await _nao_lidas(db, conversa) == 0


async def test_cliente_que_nao_monta_e_falha_da_plataforma(db, dono, client, plataforma):
    plataforma.montar_falha = RuntimeError("shopee refresh_token inválido")
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    r = await _marcar(client, conversa)
    assert r.status_code == 200 and r.json()["motivo"] == lida.MOTIVO_PLATAFORMA_FALHOU
    assert await _nao_lidas(db, conversa) == 3


async def _esperar_segundo_plano(segundos: float = 5.0) -> None:
    """Espera as chamadas que passaram do tempo (e a soltura da trava) acabarem."""
    fim = asyncio.get_running_loop().time() + segundos
    while lida._EM_SEGUNDO_PLANO and asyncio.get_running_loop().time() < fim:
        await asyncio.gather(*list(lida._EM_SEGUNDO_PLANO), return_exceptions=True)
    assert not lida._EM_SEGUNDO_PLANO


async def test_passou_do_tempo_renovando_o_token_o_novo_chega_ao_banco(
    db, dono, client, monkeypatch
):
    """Token vencido: o ShopeeClient DE VERDADE renova antes do
    read_conversation, e o refresh token é de USO ÚNICO. Estourou o
    `TEMPO_MAXIMO_S` no meio da renovação: a rota responde "não marcou" na
    hora, mas a chamada NÃO é cancelada — segue blindada, o token novo chega
    ao banco e a trava só solta quando ela acaba. `nao_lidas` não zera (o
    número fica com o sync). Sem rede: transporte falso."""
    integ, canal = await _loja(db, dono, "shopee", "chat", "auto")
    await db.execute(
        update(Integration)
        .where(Integration.id == integ.id)
        .values(
            credentials=encrypt_json(
                {
                    "shop_id": 123,
                    "partner_id": 1,
                    "partner_key": "k",
                    "access_token": "velho",
                    "refresh_token": "rt-velho",
                    "expires_at": int(time.time()) - 60,
                }
            )
        )
    )
    await db.commit()
    conversa = await _conversa(db, integ, canal)
    estado = {"girou": False, "lidas": []}

    async def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/access_token/get":
            estado["girou"] = True  # a Shopee aceitou: o rt-velho morreu aqui
            await asyncio.sleep(0.5)  # e a resposta demora a chegar
            return httpx.Response(
                200, json={"access_token": "novo", "refresh_token": "rt-novo", "expire_in": 14400}
            )
        estado["lidas"].append((request.url.path, request.url.params.get("access_token")))
        return httpx.Response(200, json={"error": "", "response": {}})

    def fabrica(*a, **k):
        k["transport"] = httpx.MockTransport(api)
        return _AsyncClientReal(*a, **k)

    monkeypatch.setattr(httpx, "AsyncClient", fabrica)
    monkeypatch.setattr(lida, "TEMPO_MAXIMO_S", 0.1)
    r = await _marcar(client, conversa)
    assert r.status_code == 200, r.text
    assert r.json()["marcada"] is False
    assert r.json()["motivo"] == lida.MOTIVO_PLATAFORMA_FALHOU
    assert estado["girou"] is True and estado["lidas"] == []
    # Ainda renovando: a trava continua (outra aba agora iria junto).
    assert (await _marcar(client, conversa)).json()["motivo"] == lida.MOTIVO_EM_ANDAMENTO

    await _esperar_segundo_plano()
    async with _db.SessionLocal() as s:
        creds = decrypt_json((await s.get(Integration, integ.id)).credentials)
    assert creds["refresh_token"] == "rt-novo" and creds["access_token"] == "novo"
    # A chamada terminou (com o token novo) e soltou a trava.
    assert estado["lidas"] == [("/api/v2/sellerchat/read_conversation", "novo")]
    assert await redis.get(lida._chave_trava(conversa.id)) is None
    assert await _nao_lidas(db, conversa) == 3


async def test_passou_do_tempo_com_a_plataforma_pendurada(
    db, dono, client, plataforma, monkeypatch
):
    """A plataforma pendurou: a rota responde em `TEMPO_MAXIMO_S`, não zera, e a
    próxima abertura só vai de novo depois que a primeira chamada acabou."""
    liberar = asyncio.Event()

    async def pendurada():
        await liberar.wait()

    plataforma.no_meio = pendurada
    monkeypatch.setattr(lida, "TEMPO_MAXIMO_S", 0.05)
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    r = await _marcar(client, conversa)
    assert r.json()["marcada"] is False and r.json()["motivo"] == lida.MOTIVO_PLATAFORMA_FALHOU
    assert (await _marcar(client, conversa)).json()["motivo"] == lida.MOTIVO_EM_ANDAMENTO
    assert len(plataforma.chamadas) == 1
    liberar.set()
    await _esperar_segundo_plano()
    assert await _nao_lidas(db, conversa) == 3
    plataforma.no_meio = None
    assert (await _marcar(client, conversa)).json()["marcada"] is True
    assert len(plataforma.chamadas) == 2
    assert await _nao_lidas(db, conversa) == 0


def test_o_log_leva_so_o_codigo_do_erro():
    """A mensagem da plataforma pode ecoar o texto do comprador: só o código vai ao log."""
    erro = RuntimeError("shopee_chat_lida error_param: oi, meu CPF é 123")
    assert lida._erro_curto(erro) == "error_param"
    assert lida._erro_curto(TimeoutError()) == "timeout"
    resposta = httpx.Response(403, request=httpx.Request("GET", "https://x"))
    http = httpx.HTTPStatusError("x", request=resposta.request, response=resposta)
    assert lida._erro_curto(http) == "http_403"
    assert lida._erro_curto(ValueError("dado do comprador")) == "ValueError"


# ─────────────── idempotência ───────────────


async def test_idempotente_ja_lida_nao_vai_a_plataforma(db, dono, client, plataforma):
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    assert (await _marcar(client, conversa)).json()["marcada"] is True
    for _ in range(3):
        r = await _marcar(client, conversa)
        assert r.status_code == 200
        assert r.json()["marcada"] is False and r.json()["motivo"] == lida.MOTIVO_JA_LIDA
    assert len(plataforma.chamadas) == 1


async def test_duas_abas_juntas_vao_uma_vez(db, dono, client, plataforma):
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    chave = lida._chave_trava(conversa.id)
    await redis.set(chave, "1", ex=30)  # a outra aba está marcando agora
    try:
        r = await _marcar(client, conversa)
        assert r.json()["marcada"] is False and r.json()["motivo"] == lida.MOTIVO_EM_ANDAMENTO
        assert plataforma.chamadas == []
    finally:
        await redis.delete(chave)


async def test_chegou_mensagem_no_meio_marca_mas_nao_zera(db, dono, client, plataforma):
    """O sync gravou mensagem nova enquanto a Shopee respondia: ela pode ter
    ficado depois do `last_read_message_id` — o número fica com o sync."""
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)

    async def chegou():
        async with _db.SessionLocal() as s:
            await s.execute(
                update(AtendimentoConversa)
                .where(AtendimentoConversa.id == conversa.id)
                .values(ultima_mensagem_em=datetime.now(UTC), nao_lidas=4)
            )
            await s.commit()

    plataforma.no_meio = chegou
    r = await _marcar(client, conversa)
    assert r.status_code == 200
    assert r.json()["marcada"] is True and r.json()["motivo"] == lida.MOTIVO_CHEGOU_MENSAGEM
    assert await _nao_lidas(db, conversa) == 4


# ─────────────── o detalhe diz à tela se é para chamar ───────────────


async def _flag(client, cid: str) -> bool:
    r = await client.get(f"{URL}/conversas/{cid}")
    assert r.status_code == 200, r.text
    return r.json()["marcar_lida"]


async def test_detalhe_diz_marcar_lida_com_as_mesmas_travas(
    db, make_user, auth_as, client, plataforma, _fase_de_observacao
):
    dono_loja = await make_user(email=DONO, role=UserRole.ADMIN)
    auth_as(dono_loja)
    integ, canal = await _loja(db, dono_loja, "shopee", "chat", "auto")
    conversa = await _conversa(db, integ, canal)
    cid, canal_id = str(conversa.id), canal.id
    assert await _flag(client, cid) is True
    # Abrir o detalhe NÃO marca nada (quem marca é o POST, depois do atraso).
    assert plataforma.chamadas == [] and await _nao_lidas(db, cid) == 3

    auth_as(await make_user())  # quem só lê
    assert await _flag(client, cid) is False
    auth_as(dono_loja)

    await db.execute(
        update(AtendimentoCanal).where(AtendimentoCanal.id == canal_id).values(modo="observar")
    )
    await db.commit()
    assert await _flag(client, cid) is False
    await db.execute(
        update(AtendimentoCanal).where(AtendimentoCanal.id == canal_id).values(modo="humano")
    )
    await db.commit()
    assert await _flag(client, cid) is True

    _fase_de_observacao.atendimento_envio_ativo = False
    assert await _flag(client, cid) is False
    _fase_de_observacao.atendimento_envio_ativo = True

    await db.execute(
        update(AtendimentoConversa).where(AtendimentoConversa.id == cid).values(nao_lidas=0)
    )
    await db.commit()
    assert await _flag(client, cid) is False

    integ_p, canal_p = await _loja(db, dono_loja, "ml", "pergunta", "humano")
    pergunta = await _conversa(db, integ_p, canal_p, "q:1")
    assert await _flag(client, str(pergunta.id)) is False
    assert plataforma.chamadas == []


# ─────────────── a IA, os crons e os robôs nunca marcam ───────────────


async def test_sem_pessoa_o_servico_recusa(db, make_user, plataforma):
    dono_loja = await make_user(email=DONO, role=UserRole.ADMIN)
    integ, canal = await _loja(db, dono_loja)
    conversa = await _conversa(db, integ, canal)
    with pytest.raises(lida.LidaRecusada) as e:
        await lida.marcar_como_lida(db, conversa, user=None)
    assert e.value.code == lida.RECUSA_SO_LEITURA
    leitor = await make_user()
    with pytest.raises(lida.LidaRecusada):
        await lida.marcar_como_lida(db, conversa, user=leitor)
    assert await lida.marcar_ao_abrir(db, conversa, None) is False
    assert plataforma.chamadas == []
    assert await _nao_lidas(db, conversa) == 3


_MARCAM = {
    "chat_read_conversation": "services/marketplaces/shopee.py",
    "cs_read_message": "services/marketplaces/tiktok.py",
    "marcar_pack_como_lido": "services/marketplaces/ml.py",
}


def _quem_cita(padrao: str) -> set[str]:
    return {
        str(p.relative_to(APP))
        for p in APP.rglob("*.py")
        if re.search(padrao, p.read_text(encoding="utf-8"))
    }


def test_no_codigo_so_a_rota_marca():
    """Só o `lida.py` chama os métodos que marcam (fora os clientes, que os
    definem), e só as duas rotas chamam o `lida.py`. A IA (`ia.py`), os crons
    (`worker.py`, `sync.py`, `importar.py`, `reclamacoes.py`, `automacoes.py`,
    `humano.py`), os robôs e os adaptadores ficam de fora."""
    for nome, cliente in _MARCAM.items():
        # Definido só no cliente; CHAMADO só pelo serviço (os comentários que
        # citam o nome, nos adaptadores, não contam).
        assert _quem_cita(rf"\bdef {nome}\(") == {cliente}, nome
        assert _quem_cita(rf"\.{nome}\(") == {"services/atendimento/lida.py"}, nome
    assert _quem_cita(r"\bmarcar_como_lida\(") == {
        "services/atendimento/lida.py",
        "routers/atendimento_lida.py",
    }
    assert _quem_cita(r"\bmarcar_ao_abrir\(") == {
        "services/atendimento/lida.py",
        "routers/atendimento.py",
    }
    # Quem importa o serviço: só as rotas.
    importam = _quem_cita(
        r"from app\.services\.atendimento import[^\n]*\blida\b|"
        r"from app\.services\.atendimento import \([^)]*\blida,|"
        r"atendimento\.lida\b"
    )
    assert importam == {"routers/atendimento.py", "routers/atendimento_lida.py"}, importam
    # E a leitura do ML continua sem marcar: `mark_as_read=true` só no método dedicado.
    assert _quem_cita(r'"mark_as_read": "true"') == {"services/marketplaces/ml.py"}
    fonte_ml = (APP / "services/marketplaces/ml.py").read_text(encoding="utf-8")
    assert fonte_ml.count('"mark_as_read": "true"') == 1
    dedicado = fonte_ml[fonte_ml.index("async def marcar_pack_como_lido") :]
    assert '"mark_as_read": "true"' in dedicado[: dedicado.index("\n    async def ")]


# ─────────────── os métodos novos dos clientes de verdade ───────────────


async def test_shopee_read_conversation_monta_o_pedido_certo(monkeypatch):
    pedidos: list[dict] = []

    async def _call(self, method, path, *, params=None, json=None, what="shopee"):
        pedidos.append({"method": method, "path": path, "json": json, "what": what})
        return {}

    monkeypatch.setattr(ShopeeClient, "_call", _call)
    cliente = ShopeeClient({"shop_id": 1, "access_token": "t", "expires_at": 9_999_999_999})
    await cliente.chat_read_conversation("2441045667828072817", 7000000000000000004)
    assert pedidos == [
        {
            "method": "POST",
            "path": "/api/v2/sellerchat/read_conversation",
            "json": {
                "conversation_id": 2441045667828072817,
                "last_read_message_id": "7000000000000000004",
            },
            "what": "shopee_chat_lida",
        }
    ]


async def test_tiktok_read_message_monta_o_pedido_certo(monkeypatch):
    pedidos: list[tuple] = []

    async def _post(self, path, body=None, extra_params=None, *, _retried=False):
        pedidos.append((path, body))
        return {"code": 0, "message": "Success", "data": {}}

    monkeypatch.setattr(TikTokClient, "_post", _post)
    cliente = TikTokClient({"app_key": "k", "app_secret": "s", "access_token": "t"})
    assert (await cliente.cs_read_message("777"))["code"] == 0
    assert pedidos == [("/customer_service/202309/conversations/777/messages/read", None)]


async def test_ml_marcar_pack_le_todas_as_paginas_com_mark_as_read_true(monkeypatch):
    feitos: list[httpx.Request] = []

    def api(request: httpx.Request) -> httpx.Response:
        feitos.append(request)
        offset = int(request.url.params.get("offset", "0"))
        lote = [{"id": f"m{offset + i}"} for i in range(min(20, 45 - offset))]
        return httpx.Response(
            200, json={"paging": {"total": 45, "offset": offset}, "messages": lote}
        )

    def fabrica(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(api)
        return _AsyncClientReal(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fabrica)
    cliente = MercadoLivreClient({"access_token": "t", "refresh_token": "r", "user_id": "999"})
    assert await cliente.marcar_pack_como_lido("2000012345678901", "999") == 45
    assert [p.url.path for p in feitos] == ["/messages/packs/2000012345678901/sellers/999"] * 3
    assert [p.url.params["offset"] for p in feitos] == ["0", "20", "40"]
    for p in feitos:
        assert p.method == "GET"
        assert p.url.params["mark_as_read"] == "true"
        assert p.url.params["tag"] == "post_sale"
    # O teto de páginas segura uma resposta estranha.
    feitos.clear()
    assert await cliente.marcar_pack_como_lido("1", "999", limite=20, max_paginas=1) == 20
    assert len(feitos) == 1
    # Não-2xx levanta (o serviço transforma em "não marcou").
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: _AsyncClientReal(
            *a, **{**k, "transport": httpx.MockTransport(lambda r: httpx.Response(403, json={}))}
        ),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await cliente.marcar_pack_como_lido("1", "999")
