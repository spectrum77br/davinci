# ruff: noqa: S105  (tokens de teste de um cliente falso, nada real)
"""Importação do histórico (P6), com clientes FALSOS — nenhuma chamada sai do Mac.

A Shopee falsa reproduz o que foi MEDIDO em produção em 28/09/2026
(`scratchpad/formato_cliente.txt`): `direction=older` sem horário devolve as
MAIS NOVAS em ordem decrescente e, com `next_timestamp_nano=X`, as anteriores
a X; `direction=latest` sem horário devolve as MAIS ANTIGAS (2023) em ordem
crescente — se alguém voltar a usar `latest` no topo, estes testes quebram;
`page_size` acima de 25 devolve lista vazia. O ML falso fala o formato das
perguntas (`api_version=4`), do `/orders/search` e do pack.

O que se mede: a trava da leitura; só a janela pedida; a conversa velha
esperando entra FECHADA (fora da fila, do alerta e da IA) e a respondida por
fora entra `respondida` com a resposta `externo`; nada de IA; nunca uma
chamada de "marcar como lido"; retomada de onde parou; `seco` não grava
nada; o teto de chamadas por minuto; o índice e a marca de cobertura.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoAvaliacaoLoja,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    AtendimentoRascunho,
    Integration,
    IntegrationPlatform,
    User,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import importar, indice
from app.services.atendimento.constantes import (
    CONVERSA_FECHADA,
    CONVERSA_RESPONDIDA,
    ORIGEM_CLIENTE,
    ORIGEM_EXTERNO,
)

AGORA = datetime.now(UTC).replace(microsecond=0)
SHOP = 111
SELLER = 999000111

# O que a importação pode chamar em cada loja — tudo LEITURA.
LEITURAS_SHOPEE = {
    "chat_conversation_list",
    "chat_one_conversation",
    "chat_mensagens_pagina",
    "get_order_list",
    "get_order_detail_indice",
    "get_comments",
}
LEITURAS_ML = {"perguntas_recebidas", "itens", "search_orders", "mensagens_do_pack"}


class RedisFalso:
    def __init__(self) -> None:
        self.dados: dict[str, Any] = {}

    async def get(self, chave):
        return self.dados.get(chave)

    async def set(self, chave, valor, *, nx=False, ex=None):
        if nx and chave in self.dados:
            return None
        self.dados[chave] = valor
        return True

    async def eval(self, script, _n, chave, token, *_args):
        """Os dois scripts da trava: soltar (`del`) e renovar (`expire`) se for a dona."""
        if self.dados.get(chave) != token:
            return 0
        if "'del'" in script:
            del self.dados[chave]
        return 1


@pytest.fixture
def redis_falso(monkeypatch) -> RedisFalso:
    r = RedisFalso()
    monkeypatch.setattr(importar, "redis", r)
    monkeypatch.setattr(indice, "redis", r)
    return r


@pytest.fixture
def leitura(monkeypatch):
    from app import worker

    def _f(ligada: bool) -> None:
        monkeypatch.setattr(get_settings(), "atendimento_leitura_ativa", ligada)
        # O worker lê o `_settings` do import; na suíte inteira ele pode não
        # ser mais o `get_settings()` (cache_clear na coleta de outros testes).
        monkeypatch.setattr(worker._settings, "atendimento_leitura_ativa", ligada)

    _f(True)
    monkeypatch.setattr(importar, "_agora", lambda: AGORA)
    return _f


def _sem_espera() -> importar.Limitador:
    return importar.Limitador(100_000)


def _s(quando: datetime) -> int:
    return int(quando.timestamp())


# ─────────────── Shopee falsa ───────────────

_seq = iter(range(10**6))


def msg_shopee(cid: str, quando: datetime, *, da_loja: bool = False, texto: str = "oi") -> dict:
    return {
        "message_id": f"70{next(_seq):017d}",
        "from_id": 222 if da_loja else 555,
        "to_id": 555 if da_loja else 222,
        "from_shop_id": SHOP if da_loja else 987654,
        "to_shop_id": 987654 if da_loja else SHOP,
        "message_type": "text",
        "content": {"text": texto},
        "conversation_id": cid,
        "created_timestamp": _s(quando),
        "region": "BR",
        "status": "normal",
        "message_option": 0,
        "source": "new_webchat",
        "source_content": {},
        "quoted_msg": None,
    }


class ShopeeFalsa:
    def __init__(self) -> None:
        self.shop_id = SHOP
        self.conversas: dict[str, dict] = {}
        self.mensagens: dict[str, list[dict]] = {}
        self.pedidos: list[dict] = []
        self.comentarios: list[dict] = []
        self.metodos: list[str] = []
        self.chamadas_lista: list[dict] = []
        self.falhar_lista_na: int | None = None  # a N-ésima chamada da lista levanta
        # cid → quantas vezes ainda falha o `get_message` dela (None = sempre).
        self.falhar_mensagens: dict[str, int | None] = {}

    def conversa(self, cid: str, *msgs: dict) -> None:
        self.mensagens.setdefault(cid, []).extend(msgs)
        ultima = max(self.mensagens[cid], key=lambda m: m["created_timestamp"])
        self.conversas[cid] = {
            "conversation_id": cid,
            "to_id": 555,
            "to_name": f"comprador_{cid}",
            "to_avatar": "",
            "shop_id": SHOP,
            "unread_count": 0,
            "pinned": False,
            "latest_message_id": ultima["message_id"],
            "latest_message_type": "text",
            "latest_message_content": ultima["content"],
            "latest_message_from_id": ultima["from_id"],
            # Nanossegundos, 19 dígitos (medido em 28/09).
            "last_message_timestamp": ultima["created_timestamp"] * 10**9,
        }

    async def chat_conversation_list(
        self, *, direction="latest", tipo="all", page_size=25, next_timestamp_nano=None
    ):
        self.metodos.append("chat_conversation_list")
        self.chamadas_lista.append(
            {"direction": direction, "page_size": page_size, "next": next_timestamp_nano}
        )
        if self.falhar_lista_na is not None and len(self.chamadas_lista) == self.falhar_lista_na:
            raise RuntimeError("shopee_chat_conversas error_server: busy")
        if page_size > 25:
            return {"conversations": [], "page_result": {"page_size": 0, "more": False}}

        def ts(c: dict) -> int:
            return c["last_message_timestamp"]

        todas = list(self.conversas.values())
        if direction == "older":
            lista = sorted(todas, key=ts, reverse=True)
            if next_timestamp_nano is not None:
                lista = [c for c in lista if ts(c) < int(next_timestamp_nano)]
        else:  # "latest": sem horário, as MAIS ANTIGAS primeiro (o que a Shopee faz)
            lista = sorted(todas, key=ts)
            if next_timestamp_nano is not None:
                lista = [c for c in lista if ts(c) > int(next_timestamp_nano)]
        pagina = lista[:page_size]
        ultimo = pagina[-1] if pagina else None
        return {
            "page_result": {
                "page_size": len(pagina),
                "next_cursor": {
                    "next_message_time_nano": str(ts(ultimo)) if ultimo else "0",
                    "conversation_id": ultimo["conversation_id"] if ultimo else "0",
                },
                "more": len(lista) > page_size,
            },
            "conversations": pagina,
        }

    async def chat_one_conversation(self, conversation_id):
        self.metodos.append("chat_one_conversation")
        return dict(self.conversas.get(conversation_id) or {})

    async def chat_mensagens_pagina(self, conversation_id, *, offset=None, page_size=25):
        self.metodos.append("chat_mensagens_pagina")
        if conversation_id in self.falhar_mensagens:
            resta = self.falhar_mensagens[conversation_id]
            if resta is None or resta > 0:
                if resta is not None:
                    self.falhar_mensagens[conversation_id] = resta - 1
                raise RuntimeError("shopee_chat_mensagens error_server: busy")
        msgs = sorted(
            self.mensagens.get(conversation_id, []),
            key=lambda m: m["created_timestamp"],
            reverse=True,
        )
        inicio = int(offset or 0)
        fim = inicio + page_size
        return {
            "messages": msgs[inicio:fim],
            "page_result": {"next_offset": str(fim) if fim < len(msgs) else "", "page_size": 25},
        }

    async def get_order_list(
        self, *, time_from, time_to, time_range_field="create_time", page_size=100, cursor=""
    ):
        self.metodos.append("get_order_list")
        assert time_range_field == "create_time"
        assert time_to - time_from < 15 * 24 * 3600
        na_janela = [p for p in self.pedidos if time_from <= p["create_time"] <= time_to]
        inicio = int(cursor or 0)
        pagina = na_janela[inicio : inicio + page_size]
        fim = inicio + len(pagina)
        return {
            "order_list": [{"order_sn": p["order_sn"], "order_status": "X"} for p in pagina],
            "more": fim < len(na_janela),
            "next_cursor": str(fim),
        }

    async def get_order_detail_indice(self, order_sns):
        self.metodos.append("get_order_detail_indice")
        assert len(order_sns) <= 50
        return [p for p in self.pedidos if p["order_sn"] in order_sns]

    async def get_comments(self, *, cursor="", page_size=50, item_id=None, comment_id=None):
        self.metodos.append("get_comments")
        inicio = int(cursor or 0)
        pagina = self.comentarios[inicio : inicio + page_size]
        fim = inicio + len(pagina)
        mais = fim < len(self.comentarios)
        return {"item_comment_list": pagina, "more": mais, "next_cursor": str(fim) if mais else ""}


def pedido_shopee(sn: str, dias: int, comprador: int = 555) -> dict:
    return {
        "order_sn": sn,
        "buyer_user_id": comprador,
        "order_status": "COMPLETED",
        "total_amount": 120.0,
        "create_time": _s(AGORA - timedelta(days=dias)),
        "item_list": [{"item_name": "Mala", "model_quantity_purchased": 1}],
    }


def comentario(cid: int, dias: int) -> dict:
    return {
        "comment_id": cid,
        "comment": "bom",
        "buyer_username": "comprador_x",
        "order_sn": "S1",
        "item_id": 1,
        "create_time": _s(AGORA - timedelta(days=dias)),
        "rating_star": 4,
        "comment_reply": {},
    }


# ─────────────── ML falso ───────────────


def _data_ml(quando: datetime) -> str:
    return quando.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000-00:00")


def pergunta_ml(qid: int, dias: int, comprador: int = 4242) -> dict:
    quando = AGORA - timedelta(days=dias)
    return {
        "date_created": _data_ml(quando),
        "item_id": f"MLB{qid}",
        "seller_id": SELLER,
        "status": "ANSWERED",
        "text": "tem na cor azul?",
        "tags": [],
        "id": qid,
        "answer": {
            "text": "Temos sim!",
            "status": "ACTIVE",
            "date_created": _data_ml(quando + timedelta(hours=1)),
        },
        "from": {"id": comprador},
        "hold": False,
        "deleted_from_listing": False,
    }


def msg_ml(mid: str, pack: str, quando: datetime, *, da_loja: bool, comprador: int) -> dict:
    de, para = (SELLER, comprador) if da_loja else (comprador, SELLER)
    return {
        "id": mid,
        "site_id": "MLB",
        "from": {"user_id": de},
        "to": {"user_id": para},
        "status": "available",
        "text": "chegou?" if not da_loja else "Já foi enviado!",
        "message_date": {"created": _data_ml(quando), "received": _data_ml(quando)},
        "message_moderation": {"status": "clean", "is_automatic": False},
        "message_attachments": None,
        "message_resources": [
            {"id": pack, "name": "packs"},
            {"id": str(SELLER), "name": "sellers"},
        ],
        "data": {"order_id": None},
    }


class MLFalso:
    def __init__(self) -> None:
        self.creds = {"user_id": SELLER}
        self.perguntas: list[dict] = []  # mais novas primeiro, como a API
        self.pedidos: list[dict] = []
        self.packs: dict[str, list[dict]] = {}
        self.metodos: list[str] = []
        self.chamadas: list[tuple[str, Any]] = []
        self.falhar_pack: dict[str, int] = {}  # pack → quantas vezes ainda falha

    async def perguntas_recebidas(self, *, status="UNANSWERED", offset=0, limit=50):
        self.metodos.append("perguntas_recebidas")
        assert status == "ANSWERED"
        return {
            "total": len(self.perguntas),
            "limit": limit,
            "questions": self.perguntas[offset : offset + limit],
        }

    async def itens(self, ids):
        self.metodos.append("itens")
        return [{"id": i, "title": f"Anúncio {i}"} for i in ids]

    async def search_orders(self, *, seller_id, date_from, date_to, limit=50, offset=0):
        self.metodos.append("search_orders")
        self.chamadas.append(("search_orders", (seller_id, date_from, date_to)))
        return {
            "results": self.pedidos[offset : offset + limit],
            "paging": {"total": len(self.pedidos), "offset": offset, "limit": limit},
        }

    async def mensagens_do_pack(self, pack_id, seller_id, *, offset=0, limit=20):
        self.metodos.append("mensagens_do_pack")
        self.chamadas.append(("mensagens_do_pack", str(pack_id)))
        if self.falhar_pack.get(str(pack_id), 0) > 0:
            self.falhar_pack[str(pack_id)] -= 1
            raise RuntimeError("HTTP 500")
        msgs = self.packs.get(str(pack_id), [])
        return {
            "paging": {"limit": limit, "offset": offset, "total": len(msgs)},
            "conversation_status": {"status": "active", "substatus": None, "claim_ids": []},
            "messages": msgs[offset : offset + limit],
            "seller_max_message_length": 350,
        }


# ─────────────── cenário ───────────────


async def _loja(db: AsyncSession, user: User, plataforma: IntegrationPlatform, nome: str):
    integ = Integration(
        user_id=user.id,
        platform=plataforma,
        name=nome,
        credentials=encrypt_json(
            {"access_token": "t", "refresh_token": "r", "shop_id": SHOP, "user_id": SELLER}
        ),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


def _fabrica(por_loja: dict):
    async def _f(integration):
        return por_loja[integration.id]

    return _f


async def _conversas(db: AsyncSession) -> dict[str, AtendimentoConversa]:
    # `populate_existing` relê do banco sem expirar o resto da sessão (a loja
    # do teste continua utilizável).
    linhas = (
        (await db.execute(select(AtendimentoConversa).execution_options(populate_existing=True)))
        .scalars()
        .all()
    )
    return {c.externo_id: c for c in linhas}


async def _mensagens(db: AsyncSession, conversa_id) -> list[AtendimentoMensagem]:
    return list(
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


async def _ja_lida(db: AsyncSession, integ: Integration, falsa: ShopeeFalsa, cid: str) -> None:
    """Uma conversa que a leitura periódica já tem, até a última mensagem."""
    conv = falsa.conversas[cid]
    conversa = AtendimentoConversa(
        integration_id=integ.id,
        plataforma="shopee",
        canal="chat",
        externo_id=cid,
        comprador_id="555",
        ultima_mensagem_em=AGORA - timedelta(days=2),
    )
    db.add(conversa)
    await db.flush()
    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id,
            externo_id=conv["latest_message_id"],
            autor="cliente",
            origem="cliente",
            texto="oi",
            enviada_em=AGORA - timedelta(days=2),
        )
    )
    await db.commit()


def _cenario_shopee() -> ShopeeFalsa:
    falsa = ShopeeFalsa()
    falsa.conversa("recente", msg_shopee("recente", AGORA - timedelta(days=2)))
    falsa.conversa(
        "respondida",
        msg_shopee("respondida", AGORA - timedelta(days=30)),
        msg_shopee("respondida", AGORA - timedelta(days=30, minutes=-5), da_loja=True),
    )
    falsa.conversa("esperando", msg_shopee("esperando", AGORA - timedelta(days=60)))
    falsa.conversa("antiga", msg_shopee("antiga", AGORA - timedelta(days=100)))
    for i in range(30):  # enche mais de uma página (25)
        cid = f"f{i:02d}"
        falsa.conversa(cid, msg_shopee(cid, AGORA - timedelta(days=10, hours=i)))
    falsa.pedidos = [pedido_shopee(f"S{i}", dias=i * 5) for i in range(1, 25)]  # 5..120 dias
    falsa.comentarios = [comentario(900 - i, dias=i * 10) for i in range(15)]  # 0..140 dias
    return falsa


# ─────────────── travas ───────────────


async def test_recusa_sem_leitura_dias_fora_e_loja_inexistente(db, make_user, leitura, redis_falso):
    leitura(False)
    with pytest.raises(importar.ImportacaoRecusada) as erro:
        await importar.importar_historico(dias=90, limitador=_sem_espera())
    assert erro.value.code == "leitura_desligada"

    leitura(True)
    for dias in (0, 400):
        with pytest.raises(importar.ImportacaoRecusada) as erro:
            await importar.importar_historico(dias=dias, limitador=_sem_espera())
        assert erro.value.code == "dias_invalido"

    user = await make_user()
    await _loja(db, user, IntegrationPlatform.SHOPEE, "kfa")
    with pytest.raises(importar.ImportacaoRecusada) as erro:
        await importar.importar_historico(
            loja="nao-existe", seco=True, limitador=_sem_espera(), fabrica_cliente=_fabrica({})
        )
    assert erro.value.code == "loja_nao_encontrada"


async def test_forcar_passa_pela_trava_da_leitura(db, make_user, leitura, redis_falso):
    leitura(False)
    user = await make_user()
    integ = await _loja(db, user, IntegrationPlatform.SHOPEE, "kfa")
    falsa = ShopeeFalsa()
    falsa.conversa("c1", msg_shopee("c1", AGORA - timedelta(days=20)))
    resumo = await importar.importar_historico(
        forcar=True, limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )
    assert resumo["lojas"][0]["etapas"]["chat"]["conversas_novas"] == 1


# ─────────────── Shopee ───────────────


async def test_shopee_do_mais_novo_para_tras_so_a_janela_sem_ia_e_sem_marcar_lido(
    db, make_user, leitura, redis_falso
):
    assert importar.DIRECAO_LISTA == "older"
    user = await make_user()
    integ = await _loja(db, user, IntegrationPlatform.SHOPEE, "kfa")
    falsa = _cenario_shopee()
    await _ja_lida(db, integ, falsa, "recente")
    dormidas: list[float] = []
    relogio = [0.0]

    async def dormir(s: float) -> None:
        dormidas.append(s)
        relogio[0] += s

    limitador = importar.Limitador(40, relogio=lambda: relogio[0], dormir=dormir)

    resumo = await importar.importar_historico(
        dias=90, limitador=limitador, fabrica_cliente=_fabrica({integ.id: falsa})
    )

    # Do topo (sem horário) com `older`, páginas de até 25, sempre `older`.
    assert falsa.chamadas_lista[0] == {"direction": "older", "page_size": 25, "next": None}
    assert {c["direction"] for c in falsa.chamadas_lista} == {"older"}
    assert all(c["page_size"] <= 25 for c in falsa.chamadas_lista)
    assert set(falsa.metodos) <= LEITURAS_SHOPEE  # nada de marcar como lido

    conversas = await _conversas(db)
    assert "antiga" not in conversas  # fora dos 90 dias
    assert len(conversas) == 33  # a recente (já lida) + respondida + esperando + 30

    esperando = conversas["esperando"]
    assert esperando.situacao == CONVERSA_FECHADA
    assert esperando.aguardando_resposta is False
    assert esperando.prazo_resposta_em is None
    assert esperando.dados["importacao"]["fechada"] is True

    respondida = conversas["respondida"]
    assert respondida.situacao == CONVERSA_RESPONDIDA
    assert [(m.autor, m.origem) for m in await _mensagens(db, respondida.id)] == [
        ("cliente", ORIGEM_CLIENTE),
        ("loja", ORIGEM_EXTERNO),
    ]
    assert respondida.dados["importacao"]["fechada"] is False

    # As de 10 dias esperando também são histórico (> 7 dias): fechadas.
    assert conversas["f00"].situacao == CONVERSA_FECHADA
    # A que a leitura periódica já tinha não foi relida nem mexida.
    assert "importacao" not in (conversas["recente"].dados or {})
    assert await db.scalar(select(func.count()).select_from(AtendimentoRascunho)) == 0

    chat = resumo["lojas"][0]["etapas"]["chat"]
    assert chat["ja_em_dia"] == 1
    assert chat["conversas_novas"] == 32
    assert chat["fechadas"] == 31

    # Índice: pedidos da janela (5..90 dias) e avaliações até o começo dela.
    pedidos = (await db.execute(select(AtendimentoPedidoComprador))).scalars().all()
    assert sorted(p.pedido for p in pedidos) == sorted(f"S{i}" for i in range(1, 19))
    assert {p.comprador_id for p in pedidos} == {"555"}
    avaliacoes = (await db.execute(select(AtendimentoAvaliacaoLoja))).scalars().all()
    assert len(avaliacoes) >= 10  # até 90 dias (a página que cruza a janela entra inteira)
    cobertura = await indice.cobertura(integ.id)
    assert cobertura == AGORA - timedelta(days=90)

    # O teto por minuto segurou o ritmo — e toda chamada passou por ele.
    assert dormidas and all(0 < d <= 60 for d in dormidas)
    assert resumo["chamadas"] == limitador.total == len(falsa.metodos)


async def test_shopee_retoma_de_onde_parou(db, make_user, leitura, redis_falso):
    user = await make_user()
    integ = await _loja(db, user, IntegrationPlatform.SHOPEE, "kfa")
    falsa = ShopeeFalsa()
    for i in range(60):  # 3 páginas
        cid = f"c{i:02d}"
        falsa.conversa(cid, msg_shopee(cid, AGORA - timedelta(days=8, hours=i)))
    falsa.falhar_lista_na = 2

    primeira = await importar.importar_historico(
        limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )
    chat = primeira["lojas"][0]["etapas"]["chat"]
    assert chat["erro"].startswith("RuntimeError")
    assert len(await _conversas(db)) == 25
    chave = importar._CHAVE_ESTADO.format(integ.id, "chat", 90)
    assert chave in redis_falso.dados

    falsa.falhar_lista_na = None
    falsa.chamadas_lista.clear()
    await importar.importar_historico(
        limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )
    # Continuou do cursor da 1ª página — não voltou ao topo.
    assert falsa.chamadas_lista[0]["next"] == str(falsa.conversas["c24"]["last_message_timestamp"])
    assert len(await _conversas(db)) == 60

    falsa.chamadas_lista.clear()
    terceira = await importar.importar_historico(
        limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )
    assert terceira["lojas"][0]["etapas"]["chat"] == {"ja_concluida": 1}
    assert falsa.chamadas_lista == []


async def test_teto_de_paginas_deixa_para_a_proxima_execucao(
    db, make_user, leitura, redis_falso, monkeypatch
):
    monkeypatch.setattr(importar, "MAX_PAGINAS_LISTA", 1)
    user = await make_user()
    integ = await _loja(db, user, IntegrationPlatform.SHOPEE, "kfa")
    falsa = ShopeeFalsa()
    for i in range(40):
        cid = f"c{i:02d}"
        falsa.conversa(cid, msg_shopee(cid, AGORA - timedelta(days=8, hours=i)))

    primeira = await importar.importar_historico(
        limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )
    assert primeira["lojas"][0]["etapas"]["chat"]["teto"] == 1
    assert len(await _conversas(db)) == 25

    segunda = await importar.importar_historico(
        limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )
    assert "teto" not in segunda["lojas"][0]["etapas"]["chat"]
    assert len(await _conversas(db)) == 40


async def test_seco_so_conta_e_nao_grava_nada(db, make_user, leitura, redis_falso):
    user = await make_user()
    integ = await _loja(db, user, IntegrationPlatform.SHOPEE, "kfa")
    falsa = _cenario_shopee()
    await _ja_lida(db, integ, falsa, "recente")

    resumo = await importar.importar_historico(
        seco=True, limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )

    etapas = resumo["lojas"][0]["etapas"]
    assert etapas["chat"] == {"conversas": 33, "ja_em_dia": 1, "a_importar": 32}
    assert etapas["pedidos"]["pedidos"] == 18
    assert etapas["avaliacoes"]["avaliacoes"] == 10
    assert "chat_mensagens_pagina" not in falsa.metodos
    assert "get_order_detail_indice" not in falsa.metodos
    assert len(await _conversas(db)) == 1  # só a que já existia
    assert await db.scalar(select(func.count()).select_from(AtendimentoPedidoComprador)) == 0
    assert redis_falso.dados == {}  # nem estado, nem cobertura


async def test_filtro_de_loja(db, make_user, leitura, redis_falso):
    user = await make_user()
    kfa = await _loja(db, user, IntegrationPlatform.SHOPEE, "kfa")
    kia = await _loja(db, user, IntegrationPlatform.SHOPEE, "kia")
    falsa_kfa, falsa_kia = ShopeeFalsa(), ShopeeFalsa()
    resumo = await importar.importar_historico(
        loja="KIA",
        seco=True,
        limitador=_sem_espera(),
        fabrica_cliente=_fabrica({kfa.id: falsa_kfa, kia.id: falsa_kia}),
    )
    assert [x["integration_id"] for x in resumo["lojas"]] == [str(kia.id)]
    assert falsa_kfa.metodos == []


# ─────────────── Mercado Livre ───────────────


async def test_ml_perguntas_respondidas_e_pos_venda_sem_marcar_lido(
    db, make_user, leitura, redis_falso
):
    user = await make_user()
    integ = await _loja(db, user, IntegrationPlatform.ML, "mega")
    falso = MLFalso()
    falso.perguntas = [pergunta_ml(1, 10), pergunta_ml(2, 50), pergunta_ml(3, 120)]
    comprador = 777
    falso.pedidos = [
        {"id": 5001, "pack_id": 2000001, "date_created": _data_ml(AGORA - timedelta(days=20))},
        {"id": 5002, "pack_id": 2000001, "date_created": _data_ml(AGORA - timedelta(days=20))},
        {"id": 5003, "pack_id": None, "date_created": _data_ml(AGORA - timedelta(days=15))},
        {"id": 5004, "pack_id": 3000003, "date_created": _data_ml(AGORA - timedelta(days=40))},
        {"id": 5005, "pack_id": 4000004, "date_created": _data_ml(AGORA - timedelta(days=3))},
    ]
    falso.packs = {
        "2000001": [
            msg_ml("m1", "2000001", AGORA - timedelta(days=19), da_loja=False, comprador=comprador),
            msg_ml("m2", "2000001", AGORA - timedelta(days=18), da_loja=True, comprador=comprador),
        ],
        # 5003 (sem carrinho): pack sem mensagem — não vira conversa.
        "3000003": [
            msg_ml("m3", "3000003", AGORA - timedelta(days=39), da_loja=False, comprador=comprador),
        ],
    }
    # O pack 4000004 a leitura periódica já tem.
    db.add(
        AtendimentoConversa(
            integration_id=integ.id,
            plataforma="ml",
            canal="pos_venda",
            externo_id="4000004",
            comprador_id="1",
        )
    )
    await db.commit()

    resumo = await importar.importar_historico(
        dias=90, limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falso})
    )

    assert set(falso.metodos) <= LEITURAS_ML
    conversas = await _conversas(db)

    # Perguntas: só as da janela, respondidas, com a resposta dada fora.
    assert "q:3" not in conversas
    for qid in ("q:1", "q:2"):
        c = conversas[qid]
        assert c.situacao == CONVERSA_RESPONDIDA
        assert c.nao_lidas == 0
        assert c.anuncio_titulo == f"Anúncio MLB{qid[2:]}"
        assert c.comprador_id == "4242"
        origens = [(m.externo_id[:2], m.origem) for m in await _mensagens(db, c.id)]
        assert origens == [("q:", ORIGEM_CLIENTE), ("a:", ORIGEM_EXTERNO)]

    # Pós-venda: o carrinho uma vez, o pack sem mensagem fora, o já lido intocado.
    packs = [a for n, a in falso.chamadas if n == "mensagens_do_pack"]
    assert packs == ["2000001", "5003", "3000003"]
    assert "5003" not in conversas
    carrinho = conversas["2000001"]
    assert carrinho.situacao == CONVERSA_RESPONDIDA
    assert carrinho.comprador_id == str(comprador)
    assert [m.origem for m in await _mensagens(db, carrinho.id)] == [
        ORIGEM_CLIENTE,
        ORIGEM_EXTERNO,
    ]
    velho = conversas["3000003"]
    assert velho.situacao == CONVERSA_FECHADA  # esperou 39 dias: histórico
    assert velho.aguardando_resposta is False
    busca = next(a for n, a in falso.chamadas if n == "search_orders")
    assert busca[1] == _data_ml(AGORA - timedelta(days=90))

    etapas = resumo["lojas"][0]["etapas"]
    assert etapas["perguntas"]["conversas_novas"] == 2
    assert etapas["pos_venda"]["conversas_novas"] == 2
    assert etapas["pos_venda"]["ja_existem"] == 1
    assert await db.scalar(select(func.count()).select_from(AtendimentoRascunho)) == 0


# ─────────────── teto de chamadas, worker e comando ───────────────


async def test_limitador_janela_deslizante():
    relogio = [0.0]
    dormidas: list[float] = []

    async def dormir(s: float) -> None:
        dormidas.append(s)
        relogio[0] += s

    limitador = importar.Limitador(3, relogio=lambda: relogio[0], dormir=dormir)
    for _ in range(7):
        await limitador.esperar()
    assert dormidas == [60.0, 60.0]
    assert limitador.total == 7


async def test_worker_recusa_sem_leitura_e_indexar_desligado(leitura):
    from app import worker

    leitura(False)
    assert await worker.atendimento_importar_historico({}, dias=90) == {
        "recusada": "leitura_desligada"
    }
    assert await worker.atendimento_indexar_pedidos({}) is None


def test_comando_recusa_sem_leitura(monkeypatch, capsys):
    from scripts import atendimento_importar_historico as comando

    monkeypatch.setattr(get_settings(), "atendimento_leitura_ativa", False)
    args = comando._argumentos(["--dias", "30", "--loja", "kfa", "--seco", "--por-minuto", "20"])
    assert (args.dias, args.loja, args.seco, args.forcar, args.por_minuto) == (
        30,
        "kfa",
        True,
        False,
        20,
    )
    assert comando.main(["--dias", "30"]) == 2
    assert "leitura_desligada" in capsys.readouterr().err


# ─────────────── revisão de 28/09 ───────────────


async def test_uma_importacao_por_vez(db, make_user, leitura, redis_falso):
    """SEG-07: comando e job juntos dobrariam o teto de chamadas na mesma cota."""
    user = await make_user()
    integ = await _loja(db, user, IntegrationPlatform.SHOPEE, "kfa")
    falsa = _cenario_shopee()
    redis_falso.dados[importar.CHAVE_TRAVA] = "outra-importacao"

    with pytest.raises(importar.ImportacaoRecusada) as erro:
        await importar.importar_historico(
            limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
        )
    assert erro.value.code == "importacao_em_andamento"
    assert falsa.metodos == []
    assert redis_falso.dados[importar.CHAVE_TRAVA] == "outra-importacao"  # não é nossa

    del redis_falso.dados[importar.CHAVE_TRAVA]
    await importar.importar_historico(
        seco=True, limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )
    assert importar.CHAVE_TRAVA not in redis_falso.dados  # acabou: soltou a vez


async def test_worker_devolve_a_recusa_da_importacao_em_andamento(leitura, redis_falso):
    from app import worker

    redis_falso.dados[importar.CHAVE_TRAVA] = "outra-importacao"
    assert await worker.atendimento_importar_historico({}, dias=90) == {
        "recusada": "importacao_em_andamento"
    }


async def test_canal_desligado_nao_e_importado(db, make_user, leitura, redis_falso):
    """SEG-06: a loja com o canal `desligado` (o interruptor do 429) não entra —
    nem o cliente dela é aberto. No ML, só a etapa do canal desligado sai."""
    from app.models import AtendimentoCanal

    user = await make_user()
    shopee = await _loja(db, user, IntegrationPlatform.SHOPEE, "kfa")
    ml = await _loja(db, user, IntegrationPlatform.ML, "mega")
    db.add_all(
        [
            AtendimentoCanal(
                integration_id=shopee.id, plataforma="shopee", canal="chat", status="desligado"
            ),
            AtendimentoCanal(
                integration_id=ml.id, plataforma="ml", canal="pergunta", status="desligado"
            ),
            AtendimentoCanal(integration_id=ml.id, plataforma="ml", canal="pos_venda"),
        ]
    )
    await db.commit()
    falsa, falso = _cenario_shopee(), MLFalso()
    falso.perguntas = [pergunta_ml(1, 10)]
    abertas: list = []

    async def fabrica(integration):
        abertas.append(integration.id)
        return {shopee.id: falsa, ml.id: falso}[integration.id]

    resumo = await importar.importar_historico(limitador=_sem_espera(), fabrica_cliente=fabrica)

    assert falsa.metodos == [] and shopee.id not in abertas
    por_loja = {x["integration_id"]: x for x in resumo["lojas"]}
    assert por_loja[str(shopee.id)]["etapas"] == {
        nome: {"canal_desligado": 1} for nome in ("chat", "pedidos", "avaliacoes")
    }
    assert por_loja[str(ml.id)]["etapas"]["perguntas"] == {"canal_desligado": 1}
    assert "perguntas_recebidas" not in falso.metodos
    assert "search_orders" in falso.metodos


async def test_avaliacoes_no_teto_continuam_na_proxima_execucao(
    db, make_user, leitura, redis_falso, monkeypatch
):
    """LOGICA-03: a loja com mais avaliações que o teto não fica com as antigas de
    fora — a etapa guarda o cursor e só se diz concluída no começo da janela."""
    monkeypatch.setattr(importar, "MAX_PAGINAS_AVALIACOES", 2)
    user = await make_user()
    integ = await _loja(db, user, IntegrationPlatform.SHOPEE, "kfa")
    falsa = ShopeeFalsa()
    falsa.comentarios = [comentario(5000 - i, dias=i // 5) for i in range(250)]  # 0..49 dias

    async def contar() -> int:
        return await db.scalar(select(func.count()).select_from(AtendimentoAvaliacaoLoja))

    r1 = await importar.importar_historico(
        limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )
    assert r1["lojas"][0]["etapas"]["avaliacoes"]["teto"] == 1
    assert await contar() == 100
    await importar.importar_historico(
        limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )
    assert await contar() == 200
    r3 = await importar.importar_historico(
        limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )
    assert "teto" not in r3["lojas"][0]["etapas"]["avaliacoes"]
    assert await contar() == 250
    r4 = await importar.importar_historico(
        limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )
    assert r4["lojas"][0]["etapas"]["avaliacoes"] == {"ja_concluida": 1}


async def test_conversa_que_falhou_uma_vez_e_tentada_de_novo(
    db, make_user, leitura, redis_falso
):
    """LOGICA-04: o erro passageiro numa conversa não a perde para sempre."""
    user = await make_user()
    integ = await _loja(db, user, IntegrationPlatform.SHOPEE, "kfa")
    falsa = ShopeeFalsa()
    for i in range(10):
        cid = f"c{i:02d}"
        falsa.conversa(cid, msg_shopee(cid, AGORA - timedelta(days=20, hours=i)))
    falsa.falhar_mensagens["c05"] = 1

    resumo = await importar.importar_historico(
        limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )

    chat = resumo["lojas"][0]["etapas"]["chat"]
    assert chat["itens_com_erro"] == 1 and chat["recuperados"] == 1
    assert "c05" in await _conversas(db)
    assert "chat_one_conversation" in falsa.metodos
    assert set(falsa.metodos) <= LEITURAS_SHOPEE


async def test_conversa_que_sempre_falha_segura_a_etapa_ate_desistir(
    db, make_user, leitura, redis_falso
):
    user = await make_user()
    integ = await _loja(db, user, IntegrationPlatform.SHOPEE, "kfa")
    falsa = ShopeeFalsa()
    for i in range(10):
        cid = f"c{i:02d}"
        falsa.conversa(cid, msg_shopee(cid, AGORA - timedelta(days=20, hours=i)))
    falsa.falhar_mensagens["c05"] = None  # sempre

    r1 = await importar.importar_historico(
        limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )
    assert r1["lojas"][0]["etapas"]["chat"]["pendentes"] == 1  # não se diz concluída
    falsa.chamadas_lista.clear()

    r2 = await importar.importar_historico(
        limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )
    chat = r2["lojas"][0]["etapas"]["chat"]
    assert chat["desistidos"] == 1 and "pendentes" not in chat
    assert falsa.chamadas_lista == []  # a lista já estava lida: só a pendente
    r3 = await importar.importar_historico(
        limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falsa})
    )
    assert r3["lojas"][0]["etapas"]["chat"] == {"ja_concluida": 1}


async def test_pack_do_ml_que_falhou_uma_vez_e_tentado_de_novo(
    db, make_user, leitura, redis_falso
):
    user = await make_user()
    integ = await _loja(db, user, IntegrationPlatform.ML, "mega")
    falso = MLFalso()
    comprador = 777
    falso.pedidos = [
        {"id": 5001, "pack_id": 2000001, "date_created": _data_ml(AGORA - timedelta(days=20))},
    ]
    falso.packs = {
        "2000001": [
            msg_ml("m1", "2000001", AGORA - timedelta(days=19), da_loja=False, comprador=comprador),
        ],
    }
    falso.falhar_pack["2000001"] = 1

    resumo = await importar.importar_historico(
        limitador=_sem_espera(), fabrica_cliente=_fabrica({integ.id: falso})
    )

    pos_venda = resumo["lojas"][0]["etapas"]["pos_venda"]
    assert pos_venda["recuperados"] == 1
    assert "2000001" in await _conversas(db)
