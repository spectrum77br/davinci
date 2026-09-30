# ruff: noqa: S105  (tokens de teste de um cliente falso, nada real)
"""Adaptador da TikTok Shop (Customer Service 202309), com um cliente FALSO.

Hoje o app NÃO tem o escopo `seller.customer_service`: produção devolve
`401 · 105005` nas 8 lojas. O primeiro compromisso do adaptador é tratar isso
como `sem_escopo` — sem levantar e sem gravar nada. O resto (leitura e
envio) segue o formato DOCUMENTADO, nunca medido, e é testado aqui para o dia
em que o escopo sair.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    IntegrationPlatform,
    User,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import enriquecer, tiktok
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    CONVERSA_ABERTA,
    CONVERSA_BLOQUEADA,
    ORIGEM_EXTERNO,
    PLATAFORMAS,
    STATUS_CANAL,
)
from app.services.marketplaces.tiktok import TikTokClient

AGORA = datetime.now(UTC).replace(microsecond=0)

# O que produção devolve hoje (o `_get` põe o HTTP em `code` e o corpo em `message`).
SEM_ESCOPO_401 = {
    "code": 401,
    "message": '{"code":105005,"message":"Access denied.","request_id":"x"}',
}


def _s(quando: datetime) -> int:
    return int(quando.timestamp())


# ─────────────── cliente falso ───────────────

_seq = iter(range(10**6))


def tmsg(
    quando: datetime,
    *,
    papel: str = "BUYER",
    tipo: str = "TEXT",
    content: dict | None = None,
    texto: str = "oi",
    bruto: str | None = None,
    visivel: bool = True,
    mid: str | None = None,
) -> dict:
    """Mensagem no formato documentado: `content` é JSON EM TEXTO."""
    n = next(_seq)
    return {
        "id": mid or f"74945601{n:011d}",
        "type": tipo,
        "content": bruto if bruto is not None else json.dumps(
            content if content is not None else {"content": texto}
        ),
        "create_time": _s(quando),
        "is_visible": visivel,
        "sender": {"im_user_id": f"im-{papel}", "role": papel, "nickname": "x", "avatar": ""},
        "index": str(7494560109732334000 + n),
    }


class TikTokFalso:
    def __init__(self) -> None:
        self.conversas: dict[str, dict] = {}
        self.mensagens: dict[str, list[dict]] = {}
        self.resposta_lista: dict | None = None
        self.resposta_mensagens: dict | None = None
        self.chamadas_lista: list[dict] = []
        self.chamadas_mensagens: list[tuple[str, str | None]] = []
        self.resposta_envio: Any = {"code": 0, "data": {"message_id": "7494560109732334261"}}
        self.enviados: list[tuple[str, str]] = []

    def conversa(
        self,
        cid: str,
        *msgs: dict,
        pode_enviar: bool = True,
        nome: str = "Ana",
        nao_lidas: int = 1,
        avatar: str = "",
    ) -> dict:
        existentes = self.mensagens.setdefault(cid, [])
        existentes.extend(msgs)
        ultima = max(existentes, key=lambda m: m["create_time"])
        conv = {
            "id": cid,
            "participant_count": 2,
            "can_send_message": pode_enviar,
            "unread_count": nao_lidas,
            "create_time": min(m["create_time"] for m in existentes),
            "participants": [
                {"im_user_id": f"im-{cid}", "user_id": f"u-{cid}", "role": "BUYER",
                 "nickname": nome, "avatar": avatar},
                {"im_user_id": "im-loja", "user_id": "loja", "role": "SHOP",
                 "nickname": "Loja", "avatar": ""},
            ],
            "latest_message": ultima,
            "cur_session_id": "1",
        }
        self.conversas[cid] = conv
        return conv

    async def cs_conversations(self, page_size: int = 20, page_token: str | None = None) -> dict:
        self.chamadas_lista.append({"page_size": page_size, "page_token": page_token})
        if self.resposta_lista is not None:
            return self.resposta_lista
        lista = sorted(
            self.conversas.values(), key=lambda c: c["latest_message"]["create_time"], reverse=True
        )
        inicio = int(page_token or 0)
        fim = inicio + page_size
        return {
            "code": 0,
            "message": "Success",
            "request_id": "r",
            "data": {
                "next_page_token": str(fim) if fim < len(lista) else "",
                "conversations": lista[inicio:fim],
            },
        }

    async def cs_messages(
        self, conversation_id: str, page_size: int = 10, page_token: str | None = None
    ) -> dict:
        self.chamadas_mensagens.append((conversation_id, page_token))
        if self.resposta_mensagens is not None:
            return self.resposta_mensagens
        msgs = sorted(
            self.mensagens.get(conversation_id, []), key=lambda m: m["create_time"], reverse=True
        )
        inicio = int(page_token or 0)
        fim = inicio + page_size
        return {
            "code": 0,
            "message": "Success",
            "data": {
                "next_page_token": str(fim) if fim < len(msgs) else "",
                "unsupported_msg_tips": "Veja no Seller Center.",
                "messages": msgs[inicio:fim],
            },
        }

    async def cs_send_text(self, conversation_id: str, texto: str) -> dict:
        self.enviados.append((conversation_id, texto))
        if isinstance(self.resposta_envio, BaseException):
            raise self.resposta_envio
        return self.resposta_envio


# ─────────────── fixtures ───────────────


async def _canal(db: AsyncSession, user: User, cursor: dict | None = None):
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.TIKTOK,
        name="TikTok Jlas",
        credentials=encrypt_json({"access_token": "t", "app_key": "k", "app_secret": "s"}),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="tiktok", canal="chat", cursor=cursor or {}
    )
    db.add(canal)
    await db.commit()
    return integ, canal


async def _rodar(db: AsyncSession, canal, integ, falso: TikTokFalso):
    r = await tiktok.sincronizar(db, canal, integ, falso)
    await db.commit()
    return r


async def _conversas(db: AsyncSession) -> dict[str, AtendimentoConversa]:
    linhas = (await db.execute(select(AtendimentoConversa))).scalars().all()
    for c in linhas:
        await db.refresh(c)
    return {c.externo_id: c for c in linhas}


async def _mensagens(db: AsyncSession, conversa_id) -> list[AtendimentoMensagem]:
    linhas = (
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa_id)
                .order_by(AtendimentoMensagem.enviada_em)
            )
        )
        .scalars()
        .all()
    )
    for m in linhas:
        await db.refresh(m)
    return list(linhas)


async def _total_mensagens(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(AtendimentoMensagem)))


@pytest.fixture
def teto(monkeypatch):
    def _f(n: int) -> None:
        monkeypatch.setattr(get_settings(), "atendimento_sync_max_conversas", n)

    _f(40)
    return _f


def test_constantes_do_adaptador_batem_com_o_vocabulario():
    assert tiktok.PLATAFORMA in PLATAFORMAS
    for s in (tiktok.STATUS_OK, tiktok.STATUS_SEM_ESCOPO, tiktok.STATUS_ERRO):
        assert s in STATUS_CANAL


# ─────────────── sem escopo (o normal hoje) ───────────────


@pytest.mark.parametrize(
    "resposta",
    [
        SEM_ESCOPO_401,
        {"code": 105005, "message": "Access denied.", "data": {}},
        {"code": 403, "message": "forbidden"},
    ],
)
async def test_sem_escopo_nao_levanta_nem_grava(db, make_user, teto, resposta):
    integ, canal = await _canal(db, await make_user(), cursor={"ultimo_ts": 5})
    f = TikTokFalso()
    f.conversa("C1", tmsg(AGORA - timedelta(hours=1)))
    f.resposta_lista = resposta

    r = await _rodar(db, canal, integ, f)

    assert r.status == tiktok.STATUS_SEM_ESCOPO
    assert "seller.customer_service" in r.erro
    assert (r.conversas_novas, r.mensagens_novas) == (0, 0)
    assert await _conversas(db) == {}
    assert canal.cursor == {"ultimo_ts": 5}
    assert f.chamadas_mensagens == []


async def test_outro_erro_da_tiktok_vira_erro(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = TikTokFalso()
    f.resposta_lista = {"code": 45101004, "message": "quota"}
    r = await _rodar(db, canal, integ, f)
    assert r.status == tiktok.STATUS_ERRO and r.erro == "tiktok code=45101004"


async def test_erro_de_rede_vira_erro(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = TikTokFalso()

    async def _cai(**_):
        raise httpx.ConnectTimeout("lento")

    f.cs_conversations = _cai
    r = await _rodar(db, canal, integ, f)
    assert r.status == tiktok.STATUS_ERRO and r.erro == "tiktok ConnectTimeout"


# ─────────────── leitura (formato documentado) ───────────────


async def test_leitura_do_formato_documentado(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = TikTokFalso()
    t = AGORA - timedelta(hours=2)
    f.conversa(
        "C1",
        tmsg(t, papel="SHOP", tipo="BUYER_ENTER_FROM_ORDER", content={"order_id": "578000111"}),
        tmsg(t + timedelta(minutes=1), texto="cadê meu pedido?"),
        tmsg(t + timedelta(minutes=2), tipo="PRODUCT_CARD", content={"product_id": "1729"}),
        tmsg(t + timedelta(minutes=3), tipo="IMAGE",
             content={"url": "https://img/1", "width": "10", "height": "10"}),
        tmsg(t + timedelta(minutes=4), papel="SYSTEM", tipo="NOTIFICATION",
             content={"content": "O pedido foi enviado"}),
        tmsg(t + timedelta(minutes=5), papel="ROBOT", texto="Resposta automática"),
        tmsg(t + timedelta(minutes=6), papel="SYSTEM", texto="Avalie", visivel=False),
        tmsg(t + timedelta(minutes=7), bruto="isto não é json"),
        tmsg(t + timedelta(minutes=8), tipo="EMOTICONS", content={"emoticon_id": "9"}),
        nao_lidas=3,
    )

    r = await _rodar(db, canal, integ, f)

    assert (r.status, r.conversas_novas, r.mensagens_novas) == (tiktok.STATUS_OK, 1, 8)
    conv = (await _conversas(db))["C1"]
    assert (conv.comprador_id, conv.comprador_nome, conv.nao_lidas) == ("u-C1", "Ana", 3)
    assert conv.conta == "TikTok Jlas"
    assert conv.pedido_marketplace == "578000111" and conv.anuncio_id == "1729"
    assert conv.dados == {"pode_enviar": True}
    ms = await _mensagens(db, conv.id)
    assert [(m.tipo, m.autor) for m in ms] == [
        ("pedido", AUTOR_SISTEMA),
        ("texto", AUTOR_CLIENTE),
        ("produto", AUTOR_CLIENTE),
        ("imagem", AUTOR_CLIENTE),
        ("outro", AUTOR_SISTEMA),
        ("texto", AUTOR_SISTEMA),
        ("texto", AUTOR_CLIENTE),  # content quebrado: sem texto, mas não derruba nada
        ("outro", AUTOR_CLIENTE),
    ]
    # Cartões no formato das outras lojas (spec 2.2), só com o id: a tela lê
    # tudo igual e desenha o que houver.
    assert ms[0].anexos == [enriquecer.cartao_pedido_vazio("578000111")]
    assert ms[2].anexos == [enriquecer.cartao_produto_vazio("1729")]
    assert conv.comprador_avatar is None  # avatar "" não vira foto
    assert ms[1].texto == "cadê meu pedido?"
    assert ms[1].enviada_em == t + timedelta(minutes=1)
    assert ms[3].anexos == [{"tipo": "imagem", "url": "https://img/1"}]
    assert ms[4].texto == "O pedido foi enviado"
    assert ms[6].texto is None
    assert ms[7].texto == "[Figurinha]"  # rótulo em português, não "[EMOTICONS]"
    # O robô da TikTok não conta como resposta: o cliente segue esperando.
    assert conv.aguardando_resposta is True
    assert conv.prazo_resposta_em == t + timedelta(minutes=8) + timedelta(hours=24)

    # A loja responde pelo Seller Center: entra como `externo` e sai da fila.
    f.conversa("C1", tmsg(t + timedelta(minutes=9), papel="SHOP", texto="Já foi enviado!"))
    r2 = await _rodar(db, canal, integ, f)
    assert (r2.conversas_atualizadas, r2.mensagens_novas) == (1, 1)
    conv = (await _conversas(db))["C1"]
    resposta = (await _mensagens(db, conv.id))[-1]
    assert (resposta.autor, resposta.origem) == (AUTOR_LOJA, ORIGEM_EXTERNO)
    assert conv.aguardando_resposta is False


async def test_primeira_rodada_so_7_dias_e_rodar_duas_vezes_nao_duplica(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = TikTokFalso()
    f.conversa("NOVA", tmsg(AGORA - timedelta(hours=1)), tmsg(AGORA - timedelta(minutes=50)))
    f.conversa("VELHA", tmsg(AGORA - timedelta(days=10)))

    r1 = await _rodar(db, canal, integ, f)
    assert (r1.conversas_novas, r1.mensagens_novas) == (1, 2)
    assert set(await _conversas(db)) == {"NOVA"}
    assert canal.cursor["ultimo_ts"] == _s(AGORA - timedelta(minutes=50))
    assert canal.cursor["lacunas"] == []

    r2 = await _rodar(db, canal, integ, f)
    assert (r2.conversas_novas, r2.conversas_atualizadas, r2.mensagens_novas) == (0, 0, 0)

    antes = len(f.chamadas_mensagens)
    canal.cursor = {}
    r3 = await _rodar(db, canal, integ, f)
    assert (r3.conversas_atualizadas, r3.mensagens_novas) == (1, 0)
    assert len(f.chamadas_mensagens) == antes  # a última já estava gravada
    assert await _total_mensagens(db) == 2


async def test_mensagens_param_na_primeira_ja_gravada(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = TikTokFalso()
    inicio = AGORA - timedelta(days=1)
    f.conversa("L", *[tmsg(inicio + timedelta(minutes=i)) for i in range(35)])
    r1 = await _rodar(db, canal, integ, f)
    assert r1.mensagens_novas == 30  # 3 páginas de 10
    f.conversa("L", tmsg(inicio + timedelta(minutes=40)))
    antes = len(f.chamadas_mensagens)
    r2 = await _rodar(db, canal, integ, f)
    assert r2.mensagens_novas == 1
    assert len(f.chamadas_mensagens) - antes == 1


async def test_teto_por_rodada_e_retomada(db, make_user, teto):
    teto(1)
    integ, canal = await _canal(db, await make_user())
    f = TikTokFalso()
    f.conversa("A", tmsg(AGORA - timedelta(hours=1)))
    f.conversa("B", tmsg(AGORA - timedelta(hours=2)))

    await _rodar(db, canal, integ, f)
    assert set(await _conversas(db)) == {"A"}
    # Parou no teto: o resto da lista fica como lacuna; o maior horário já visto anda.
    [lacuna] = canal.cursor["lacunas"]
    assert lacuna["retomar"] == "1"
    assert canal.cursor["ultimo_ts"] == _s(AGORA - timedelta(hours=1))

    await _rodar(db, canal, integ, f)
    assert set(await _conversas(db)) == {"A", "B"}
    # O topo primeiro (nada novo), depois a lacuna.
    assert f.chamadas_lista[-2:] == [
        {"page_size": 1, "page_token": None},
        {"page_size": 1, "page_token": "1"},
    ]
    assert canal.cursor["lacunas"] == []
    assert canal.cursor["ultimo_ts"] == _s(AGORA - timedelta(hours=1))


async def test_caminhada_longa_nao_segura_a_mensagem_nova_do_topo(db, make_user, teto):
    """API-12: a caminhada retomada era lida ANTES do topo — a mensagem nova
    esperava a caminhada inteira terminar para entrar na fila."""
    teto(3)
    integ, canal = await _canal(db, await make_user())
    f = TikTokFalso()
    for i in range(12):
        f.conversa(f"V{i:02d}", tmsg(AGORA - timedelta(hours=2 + i)))
    await _rodar(db, canal, integ, f)  # lê 3, deixa a lacuna com 9

    f.conversa("NOVA", tmsg(AGORA - timedelta(minutes=1), texto="cadê?"))
    r = await _rodar(db, canal, integ, f)

    assert "NOVA" in await _conversas(db)
    # A nova + o que sobrou do teto para a caminhada antiga. O `page_token` do
    # falso é posição na lista: com a nova no topo, a retomada relê uma (sem
    # pular nenhuma — é o que importa).
    assert r.conversas_novas >= 2
    assert (await _conversas(db))["NOVA"].aguardando_resposta is True

    for _ in range(4):
        await _rodar(db, canal, integ, f)
    assert len(await _conversas(db)) == 13
    assert canal.cursor["lacunas"] == []


async def test_janela_fechada_bloqueia_e_reabre(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = TikTokFalso()
    f.conversa("J", tmsg(AGORA - timedelta(hours=2)), pode_enviar=False)
    await _rodar(db, canal, integ, f)
    conv = (await _conversas(db))["J"]
    assert conv.situacao == CONVERSA_BLOQUEADA and conv.bloqueio_motivo == tiktok.MOTIVO_JANELA
    assert conv.aguardando_resposta is True  # bloqueada continua pedindo gente

    f.conversa("J", tmsg(AGORA - timedelta(hours=1)), pode_enviar=True)
    await _rodar(db, canal, integ, f)
    conv = (await _conversas(db))["J"]
    assert conv.situacao == CONVERSA_ABERTA and conv.bloqueio_motivo is None
    assert conv.dados == {"pode_enviar": True}


async def test_erro_nas_mensagens_nao_pula_conversa(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = TikTokFalso()
    f.conversa("A", tmsg(AGORA - timedelta(hours=1)))
    f.resposta_mensagens = {"code": 45101001, "message": "Internal error"}
    r1 = await _rodar(db, canal, integ, f)
    assert r1.status == tiktok.STATUS_ERRO
    assert canal.cursor["ultimo_ts"] is None and canal.cursor["falhas"] == {"A": 1}

    f.resposta_mensagens = None
    r2 = await _rodar(db, canal, integ, f)
    assert r2.status == tiktok.STATUS_OK and r2.mensagens_novas == 1
    assert canal.cursor["falhas"] == {}


# ─────────────── envio ───────────────


async def _conversa_para_envio(db, make_user):
    integ, canal = await _canal(db, await make_user())
    conv = AtendimentoConversa(
        canal_id=canal.id,
        integration_id=integ.id,
        plataforma="tiktok",
        canal="chat",
        externo_id="7494560109732334262",
    )
    db.add(conv)
    await db.commit()
    return integ, conv


async def test_envio_ok(db, make_user):
    integ, conv = await _conversa_para_envio(db, make_user)
    f = TikTokFalso()
    r = await tiktok.enviar_texto(db, conv, integ, f, "Seu pedido já saiu.")
    assert r.ok and r.externo_id == "7494560109732334261" and not r.ambiguo
    assert f.enviados == [("7494560109732334262", "Seu pedido já saiu.")]


@pytest.mark.parametrize(
    ("resposta", "ambiguo", "erro", "bloqueio"),
    [
        (httpx.ReadTimeout("t"), True, "tiktok ReadTimeout", None),
        (ValueError("json quebrado"), True, "tiktok ValueError", None),
        ({"code": 45101001, "message": "Internal error"}, True, "tiktok code=45101001", None),
        ({"code": 502, "message": "bad gateway"}, True, "tiktok code=502", None),
        ({"message": "sem code"}, True, "tiktok code=None", None),
        ({"code": 45101006, "message": "sensitive"}, False, "tiktok code=45101006", None),
        (SEM_ESCOPO_401, False, "tiktok sem_escopo", None),
        (
            {"code": 45109001, "message": "rules"},
            False,
            "tiktok code=45109001",
            tiktok.MOTIVO_JANELA,
        ),
    ],
)
async def test_envio_com_erro_nunca_levanta(db, make_user, resposta, ambiguo, erro, bloqueio):
    integ, conv = await _conversa_para_envio(db, make_user)
    f = TikTokFalso()
    f.resposta_envio = resposta
    r = await tiktok.enviar_texto(db, conv, integ, f, "texto da loja")
    assert (r.ok, r.ambiguo, r.erro, r.bloqueio) == (False, ambiguo, erro, bloqueio)


async def test_envio_vazio_nem_chama(db, make_user):
    integ, conv = await _conversa_para_envio(db, make_user)
    f = TikTokFalso()
    r = await tiktok.enviar_texto(db, conv, integ, f, "  ")
    assert (r.ok, r.erro) == (False, "tiktok texto_vazio") and f.enviados == []


# ─────────────── métodos novos do TikTokClient ───────────────


@pytest.fixture
def cliente_gravando(monkeypatch):
    chamadas: list[dict] = []

    async def _get(self, path, extra_params=None, *, _retried=False):
        chamadas.append({"verbo": "GET", "path": path, "params": extra_params})
        return {"code": 0, "data": {}}

    async def _post(self, path, body=None, extra_params=None, *, _retried=False):
        chamadas.append({"verbo": "POST", "path": path, "body": body, "params": extra_params})
        return {"code": 0, "data": {"message_id": "1"}}

    monkeypatch.setattr(TikTokClient, "_get", _get)
    monkeypatch.setattr(TikTokClient, "_post", _post)
    return TikTokClient({"app_key": "k", "app_secret": "s", "access_token": "t"}), chamadas


async def test_cliente_cs_conversations_e_messages(cliente_gravando):
    cliente, chamadas = cliente_gravando
    await cliente.cs_conversations()
    await cliente.cs_conversations(page_size=50, page_token="abc")  # noqa: S106
    await cliente.cs_messages("123", page_size=50, page_token="tok")  # noqa: S106
    assert chamadas[0] == {
        "verbo": "GET",
        "path": "/customer_service/202309/conversations",
        "params": {"page_size": "20", "locale": "pt-BR"},
    }
    assert chamadas[1]["params"] == {"page_size": "20", "locale": "pt-BR", "page_token": "abc"}
    assert chamadas[2]["path"] == "/customer_service/202309/conversations/123/messages"
    assert chamadas[2]["params"] == {
        "page_size": "10",
        "locale": "pt-BR",
        "sort_order": "DESC",
        "sort_field": "create_time",
        "page_token": "tok",
    }


async def test_cliente_cs_send_text_manda_content_serializado(cliente_gravando):
    cliente, chamadas = cliente_gravando
    resp = await cliente.cs_send_text("123", 'Olá, "já" saiu ✅')
    assert resp["data"]["message_id"] == "1"
    (c,) = chamadas
    assert c["path"] == "/customer_service/202309/conversations/123/messages"
    assert c["body"]["type"] == "TEXT"
    assert isinstance(c["body"]["content"], str)
    assert json.loads(c["body"]["content"]) == {"content": 'Olá, "já" saiu ✅'}


def test_cliente_nao_tem_como_marcar_como_lido():
    nomes = [n.lower() for n in dir(TikTokClient)]
    assert not [n for n in nomes if n.startswith("cs_") and "read" in n]


_AsyncClientReal = httpx.AsyncClient


async def test_nenhum_pedido_de_marcar_como_lido_sai_do_cliente_de_verdade(
    db, make_user, teto, monkeypatch
):
    """COMPL-7: o teste acima só olha NOMES de método. Aqui a rodada e o envio
    rodam com o `TikTokClient` de VERDADE sobre um transporte falso, e cada
    pedido é conferido contra a lista FECHADA do que pode sair — o `Read
    Message` (…/messages/read) ou qualquer outro endpoint reprova."""
    feitos: list[httpx.Request] = []
    t = AGORA - timedelta(minutes=5)
    falso = TikTokFalso()
    conv = falso.conversa("777", tmsg(t, texto="oi"))
    mensagens = falso.mensagens["777"]

    def api(request: httpx.Request) -> httpx.Response:
        feitos.append(request)
        caminho = request.url.path
        if caminho == "/customer_service/202309/conversations":
            data = {"conversations": [conv], "next_page_token": ""}
        elif caminho == "/customer_service/202309/conversations/777/messages":
            if request.method == "POST":
                data = {"message_id": "7494560109732334999"}
            else:
                data = {"messages": mensagens, "next_page_token": ""}
        else:
            return httpx.Response(404, json={"code": 404, "message": caminho})
        return httpx.Response(200, json={"code": 0, "message": "Success", "data": data})

    def fabrica(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(api)
        return _AsyncClientReal(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fabrica)
    cliente = TikTokClient({"app_key": "k", "app_secret": "s", "access_token": "t"})
    integ, canal = await _canal(db, await make_user())

    r = await _rodar(db, canal, integ, cliente)
    assert (r.status, r.conversas_novas, r.mensagens_novas) == (tiktok.STATUS_OK, 1, 1)
    leitura = list(feitos)
    envio = await tiktok.enviar_texto(
        db, (await _conversas(db))["777"], integ, cliente, "Chega amanhã."
    )
    assert envio.ok

    permitidos = {
        ("GET", "/customer_service/202309/conversations"),
        ("GET", "/customer_service/202309/conversations/777/messages"),
        ("POST", "/customer_service/202309/conversations/777/messages"),
    }
    assert {(p.method, p.url.path) for p in feitos} <= permitidos
    assert not [p for p in feitos if p.url.path.endswith("/read")]
    assert {p.method for p in leitura} == {"GET"}  # ler nunca escreve


async def test_foto_do_comprador_vem_do_participante(db, make_user, teto):
    """A foto é a URL que o chat entrega (`participants[].avatar`), sem nova chamada."""
    integ, canal = await _canal(db, await make_user())
    f = TikTokFalso()
    t = AGORA - timedelta(hours=1)
    foto = "https://p16-oec-ttp.tiktokcdn-us.com/avatar.image?"
    f.conversa("C9", tmsg(t, texto="oi"), avatar=foto)
    await _rodar(db, canal, integ, f)
    assert (await _conversas(db))["C9"].comprador_avatar == foto

    # Avatar inválido (ou vazio) depois não apaga a foto que já estava.
    f.conversa("C9", tmsg(t + timedelta(minutes=1), texto="?"), avatar="javascript:alert(1)")
    await _rodar(db, canal, integ, f)
    assert (await _conversas(db))["C9"].comprador_avatar == foto
