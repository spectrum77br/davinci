# ruff: noqa: S105  (tokens de teste de clientes falsos, nada real)
"""Avaliações de venda no atendimento (RF8, 02/10/2026): Shopee e Mercado Livre.

Sem rede. Os formatos são os MEDIDOS em produção em 02/10/2026 (sondagem só
GET, imprimindo só chaves e contagens):

- Shopee `get_comment`: `{item_comment_list, more, next_cursor}`; cada
  avaliação com 14 chaves (`comment_id`, `comment`, `buyer_username`,
  `order_sn`, `item_id`, `model_id`, `model_id_list`, `create_time`,
  `rating_star`, `editable`, `hidden`, `media` — `{}` ou
  `{"video_url_list": [https]}` — e `comment_reply` `{reply, hidden,
  create_time}` só quando há resposta); decrescente por `create_time`; o
  filtro `comment_id` devolve 1.
- ML `/reviews/item/{id}`: `{paging: {total, limit, offset, total_pageable},
  helpful_reviews, quali_attributes, rating_average, stars, rating_levels,
  cross_site_enabled, user_product_id, reviews}`; a opinião com `id`,
  `reviewable_object {id, type: product}`, `date_created` (ISO Z), `status`
  `published`, `title`, `content`, `rate`, `media [{id, status, type, alt,
  variations, url, thumbnail, preview_url, duration_ms}]`, `order_id` (int),
  `is_public_review`... — sem comprador nem resposta. A lista é do PRODUTO:
  anúncios irmãos (até de outra conta nossa) devolvem as mesmas opiniões.

O que se mede: a importação dos 30 dias e a leitura incremental; a
pendência (sem resposta, depois da carência, conferida pelo id); a conversa
`avaliacao` (Shopee respondível, ML bloqueada) e a etiqueta AVALIAÇÃO no
pedido, que volta ao status anterior quando a resposta chega ou é tratada;
o ML pela conta dona do pedido, um produto por rodada; o cron só com os
dois interruptores; as rotas (o 409 `envio_desligado` antes de tudo, a
resposta pública pela API com HTTP falso, o "tratada"); a lista com as
estrelas e o filtro; nada de texto de comprador no contexto da IA.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker
from app.config import Settings, get_settings
from app.main import app
from app.models import (
    AtendimentoAvaliacaoLoja,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoEtiquetaHistorico,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    AtendimentoReclamacao,
    Integration,
    IntegrationPlatform,
    UserRole,
)
from app.models.marketplace_financial import MarketplaceOrderFinancial
from app.routers import atendimento as rota_atendimento
from app.routers import atendimento_avaliacoes as rota_avaliacoes
from app.security.cipher import decrypt_json, encrypt_json
from app.services.atendimento import (
    avaliacoes,
    clientes,
    contexto,
    enviar,
    etiqueta,
    gravar,
    ia,
    indexar,
    indice,
)
from app.services.atendimento import shopee as shopee_adapter
from app.services.atendimento.constantes import (
    CANAL_AVALIACAO,
    CONVERSA_BLOQUEADA,
    ETIQUETA_AVALIACAO,
    ORIGEM_IA,
    PRIORIDADE_ETIQUETAS,
)
from app.services.atendimento.etiqueta import FatosEtiqueta, calcular
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
SN = "250930ABCDEF12"  # order_sn da Shopee (14 caracteres, como medido)
SELLER_A = 204897261
SELLER_B = 555000111
URL_VIDEO = "https://down-br.vod.susercontent.com/api/v4/11110105/mms/video.mp4"
URL_FOTO_ML = "https://http2.mlstatic.com/D_NQ_NP_2X_foto-F.jpg"
_AsyncClientReal = httpx.AsyncClient


# ─────────────── a infraestrutura dos testes ───────────────


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

    async def eval(self, _script, _n, chave, token):
        if self.dados.get(chave) == token:
            del self.dados[chave]
            return 1
        return 0


@pytest.fixture
def redis_falso(monkeypatch) -> RedisFalso:
    r = RedisFalso()
    monkeypatch.setattr(avaliacoes, "redis", r)
    return r


@pytest.fixture(autouse=True)
def _relogio(monkeypatch):
    monkeypatch.setattr(avaliacoes, "_agora", lambda: T0)
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    monkeypatch.setattr(s, "atendimento_simulador", False)
    monkeypatch.setattr(s, "atendimento_envio_ativo", False)


@pytest.fixture(autouse=True)
async def _limpa_espelho(db: AsyncSession):
    """O espelho financeiro não está na limpeza do conftest."""
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    await db.commit()
    yield
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    await db.commit()


@pytest.fixture
def rotas():
    """O router das avaliações já está no app (o `main.py` o inclui)."""
    assert any(
        getattr(r, "path", "") == "/api/atendimento/conversas/{conversa_id}/avaliacoes"
        for r in app.routes
    )
    return app


class ShopeeFalso:
    """O `get_comment` de UMA loja, no formato medido; e o registro das chamadas."""

    def __init__(self, shop_id: int = 111) -> None:
        self.shop_id = shop_id
        self.comentarios: list[dict] = []  # do mais novo para o mais velho
        self.chamadas: list[tuple[str, Any]] = []

    async def get_comments(self, *, cursor="", page_size=50, item_id=None, comment_id=None):
        if comment_id not in (None, ""):
            self.chamadas.append(("por_id", str(comment_id)))
            lista = [c for c in self.comentarios if str(c["comment_id"]) == str(comment_id)]
            return {"item_comment_list": lista, "more": False, "next_cursor": ""}
        self.chamadas.append(("pagina", (cursor, page_size)))
        ini = int(cursor or 0)
        fim = ini + int(page_size)
        return {
            "item_comment_list": self.comentarios[ini:fim],
            "more": fim < len(self.comentarios),
            "next_cursor": str(fim) if fim < len(self.comentarios) else "",
        }

    def de(self, tipo: str) -> list:
        return [c for t, c in self.chamadas if t == tipo]


def comentario(
    cid: int,
    horas_atras: float,
    estrelas: int = 5,
    *,
    sn: str = SN,
    texto: str = "",
    resposta: str | None = None,
    resposta_horas_atras: float | None = None,
    media: dict | None = None,
    editable: str = "EDITABLE",
) -> dict:
    """Uma avaliação no formato do `get_comment` (14 chaves, medido em 02/10/2026)."""
    c: dict[str, Any] = {
        "comment_id": cid,
        "comment": texto,
        "buyer_username": "comprador_x",
        "order_sn": sn,
        "item_id": 2200001,
        "model_id": 3300001,
        "create_time": int((T0 - timedelta(hours=horas_atras)).timestamp()),
        "rating_star": estrelas,
        "editable": editable,
        "hidden": False,
        "media": media if media is not None else {},
        "model_id_list": [3300001],
    }
    if resposta is not None:
        quando = T0 - timedelta(hours=resposta_horas_atras if resposta_horas_atras else 0)
        c["comment_reply"] = {
            "reply": resposta,
            "hidden": False,
            "create_time": int(quando.timestamp()),
        }
    return c


async def _loja(db: AsyncSession, make_user, *, plataforma="shopee", nome="atv", creds=None):
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform(plataforma),
        name=nome,
        credentials=encrypt_json(creds or {"access_token": "t", "shop_id": 111}),
    )
    db.add(integ)
    await db.commit()
    return SimpleNamespace(id=integ.id, user=user)


def _fabrica(cliente):
    async def _f(_integration):
        return cliente

    return _f


async def _rodada_shopee(integ, cliente, **kw) -> dict:
    return await avaliacoes.sincronizar_loja_shopee(
        integ.id, fabrica_cliente=_fabrica(cliente), agora=T0, **kw
    )


async def _linha(db: AsyncSession, cid: str) -> AtendimentoAvaliacaoLoja:
    """Relida do banco (a rodada grava na sessão dela)."""
    return (
        await db.execute(
            select(AtendimentoAvaliacaoLoja)
            .where(AtendimentoAvaliacaoLoja.comentario_id == cid)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _conversa_da_avaliacao(db: AsyncSession, cid: str) -> AtendimentoConversa | None:
    return (
        await db.execute(
            select(AtendimentoConversa)
            .where(
                AtendimentoConversa.canal == CANAL_AVALIACAO,
                AtendimentoConversa.externo_id == cid,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _mensagens(db: AsyncSession, conversa_id: UUID) -> list[AtendimentoMensagem]:
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


async def _chat_do_pedido(db: AsyncSession, integ, *, plataforma="shopee", pedido=SN, externo="c1"):
    """A conversa do comprador (chat da Shopee / pack do ML) do mesmo pedido."""
    integration = await db.get(Integration, integ.id)
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=integration,
        plataforma=plataforma,
        canal_nome="chat" if plataforma == "shopee" else "pos_venda",
        externo_id=externo,
        comprador_id="777",
        comprador_nome="comprador_x",
        pedido_marketplace=pedido,
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="m1",
        autor="cliente",
        texto="oi",
        enviada_em=T0 - timedelta(days=3),
    )
    # Respondida (fora da fila): só a avaliação põe a conversa em evidência.
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="m2",
        autor="loja",
        texto="Olá! Em que posso ajudar?",
        enviada_em=T0 - timedelta(days=3) + timedelta(hours=1),
    )
    await db.commit()
    return conversa.id


# ─────────────── as peças puras ───────────────


def test_avaliacao_shopee_no_formato_medido():
    c = comentario(
        9001,
        2,
        estrelas=2,
        texto="veio riscado",
        resposta="Sentimos muito!",
        resposta_horas_atras=1,
        media={"video_url_list": [URL_VIDEO], "image_url_list": ["http://inseguro/x.jpg"]},
        editable="HAVE_EDITED_ONCE",
    )
    linha = indice.avaliacao_shopee(c)
    assert linha["comentario_id"] == "9001" and linha["pedido"] == SN
    assert linha["estrelas"] == 2 and linha["texto"] == "veio riscado"
    assert linha["resposta_loja"] == "Sentimos muito!"
    assert linha["resposta_em"] == T0 - timedelta(hours=1)
    assert linha["resposta_oculta"] is False
    assert linha["editavel"] == "HAVE_EDITED_ONCE" and linha["modelo_id"] == "3300001"
    # Só https: a URL insegura não entra.
    assert linha["midia"] == [{"tipo": "video", "url": URL_VIDEO}]
    assert linha["oculta"] is False
    # Sem `comment_reply` (a avaliação sem resposta): nada de resposta.
    sem = indice.avaliacao_shopee(comentario(9002, 1))
    assert (sem["resposta_loja"], sem["resposta_em"], sem["resposta_oculta"]) == (None, None, None)


def _opiniao(
    rid: int,
    order: int,
    *,
    rate: int = 5,
    dias: float = 3,
    status: str = "published",
    item: str = "MLB4803096089",
    media: list | None = None,
) -> dict:
    """Uma opinião do `/reviews/item` (24 chaves, medido em 02/10/2026)."""
    return {
        "id": rid,
        "reviewable_object": {"id": item, "type": "product"},
        "date_created": (T0 - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "title": "Bom produto",
        "content": "Chegou certinho" if rate > 3 else "Veio com defeito",
        "rate": rate,
        "valorization": 0,
        "likes": 0,
        "dislikes": 0,
        "buying_date": (T0 - timedelta(days=dias + 5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "relevance": 1,
        "forbidden_words": 0,
        "attributes": None,
        "media": media or [],
        "reactions": None,
        "attributes_variation": None,
        "translations": {"es": "..."},
        "secondary_key": "MLBU3044172112",
        "order_id": order,
        "catalog_listing": False,
        "earned_rewards": 0,
        "is_highlighted": False,
        "is_public_review": False,
    }


def test_opiniao_do_ml_no_formato_medido():
    midia = [
        {
            "id": "a" * 24,
            "status": "published",
            "type": "image",
            "alt": "foto",
            "variations": [],
            "url": URL_FOTO_ML,
            "thumbnail": URL_FOTO_ML,
            "preview_url": URL_FOTO_ML,
            "duration_ms": 0,
        }
    ]
    linha = avaliacoes.opiniao_ml(_opiniao(77, 2000018509205724, rate=2, media=midia))
    assert linha["plataforma"] == "ml" and linha["comentario_id"] == "77"
    assert linha["pedido"] == "2000018509205724" and linha["item_id"] == "MLB4803096089"
    assert (linha["estrelas"], linha["titulo"]) == (2, "Bom produto")
    assert linha["criado_em"] == T0 - timedelta(days=3)
    assert linha["midia"][0]["tipo"] == "imagem" and linha["midia"][0]["url"] == URL_FOTO_ML
    assert avaliacoes.opiniao_ml(_opiniao(78, 1, status="moderated")) is None
    sem_pedido = _opiniao(79, 1)
    sem_pedido["order_id"] = None
    assert avaliacoes.opiniao_ml(sem_pedido) is None


def test_etiqueta_avaliacao_na_prioridade_e_volta_ao_status_anterior():
    # 02/10/2026: o Carrinho (sites) entra entre Avaliação e Pré-venda e a
    # Mídia (redes, uma base) no fim.
    assert PRIORIDADE_ETIQUETAS == (
        "reclamacao",
        "ag_cancelamento",
        "devolucao",
        "avaliacao",
        "carrinho",
        "pre_venda",
        "pos_venda",
        "midia",
    )
    pendente = FatosEtiqueta(
        tem_pedido=True, avaliacao_pendente=True, motivo_avaliacao="Avaliação 2★"
    )
    assert calcular(pendente).etiqueta == ETIQUETA_AVALIACAO
    # Reclamação aberta vale mais; a avaliação vira o indicador pequeno.
    com_reclamacao = FatosEtiqueta(tem_pedido=True, reclamacao_aberta=True, avaliacao_pendente=True)
    assert calcular(com_reclamacao).etiqueta == "reclamacao"
    assert calcular(com_reclamacao).secundarias == ["avaliacao"]
    # Respondida: volta ao que os outros fatos dão.
    assert calcular(FatosEtiqueta(tem_pedido=True)).etiqueta == "pos_venda"
    assert calcular(FatosEtiqueta(devolucao_aberta=True)).etiqueta == "devolucao"


def test_mapa_de_pedidos_e_produtos_do_espelho():
    conta = UUID(int=1)
    linhas = [
        SimpleNamespace(
            integration_id=conta,
            pedido="10",
            comprador="900",
            apelido="NICK",
            pack="20",
            item="MLB1",
            produto="MLBU1",
            titulo="Mala",
            criado=T0 - timedelta(days=2),
        ),
        SimpleNamespace(
            integration_id=conta,
            pedido="10",
            comprador=None,
            apelido=None,
            pack="20",
            item="MLB2",
            produto="MLBU1",
            titulo="Mala azul",
            criado=T0 - timedelta(days=2),
        ),
        SimpleNamespace(
            integration_id=conta,
            pedido="11",
            comprador="901",
            apelido=None,
            pack="11",
            item="MLB3",
            produto=None,
            titulo=None,
            criado=T0 - timedelta(days=60),
        ),
    ]
    mapa = avaliacoes.mapa_de_pedidos(linhas)
    assert mapa["10"].comprador_id == "900" and mapa["10"].pack_id == "20"
    assert mapa["10"].titulos == {"MLB1": "Mala", "MLB2": "Mala azul"}
    # Pack igual ao pedido não é pack.
    assert mapa["11"].pack_id is None
    # Um anúncio por PRODUTO, só os vendidos na janela.
    desde = T0 - timedelta(days=45)
    assert avaliacoes.anuncios_por_produto(linhas, conta, desde) == {"MLBU1": "MLB1"}


# ─────────────── Shopee: a leitura ───────────────


async def test_shopee_importa_30_dias_e_depois_le_so_o_topo(db, make_user, redis_falso):
    integ = await _loja(db, make_user)
    loja = ShopeeFalso()
    # 250 avaliações, uma a cada 4 h: 30 dias ≈ 180; o resto é mais velho.
    loja.comentarios = [comentario(1000 + i, 4 * i + 0.5, resposta="Obrigado!") for i in range(250)]
    r = await _rodada_shopee(integ, loja)
    paginas = loja.de("pagina")
    assert [p[1] for p in paginas] == [100, 100]  # parou na página que passou da janela
    assert r["paginas"] == 2 and r["novas"] == 200
    assert redis_falso.dados[avaliacoes.CHAVE_IMPORTADA_SHOPEE.format(integ.id)] == "ok"

    # Rodada seguinte: só o topo (tudo conhecido, decrescente → para).
    loja.chamadas.clear()
    loja.comentarios.insert(0, comentario(5000, 0.1))
    r = await _rodada_shopee(integ, loja)
    assert len(loja.de("pagina")) == 2 and r["novas"] == 1
    # A página seguinte (só conhecidas) encerra; nenhuma releitura (carência).
    assert loja.de("por_id") == []
    linha = await _linha(db, "5000")
    assert linha.pode_responder is True and linha.motivo_sem_resposta is None


async def test_sem_redis_nao_reimporta_a_cada_rodada(db, make_user, monkeypatch):
    class RedisFora:
        async def get(self, *_a, **_k):
            raise ConnectionError("fora")

        async def set(self, *_a, **_k):
            raise ConnectionError("fora")

    monkeypatch.setattr(avaliacoes, "redis", RedisFora())
    integ = await _loja(db, make_user)
    loja = ShopeeFalso()
    loja.comentarios = [comentario(1000 + i, i + 0.5, resposta="ok") for i in range(30)]
    await _rodada_shopee(integ, loja)
    # Só o topo (página de 100), sem a janela de importação.
    assert len(loja.de("pagina")) == 1


async def test_pendencia_da_shopee_etiqueta_no_pedido_e_volta_quando_responde(
    db, make_user, redis_falso
):
    integ = await _loja(db, make_user)
    chat_id = await _chat_do_pedido(db, integ)
    db.add(
        AtendimentoPedidoComprador(
            integration_id=integ.id, plataforma="shopee", comprador_id="777", pedido=SN
        )
    )
    await db.commit()
    loja = ShopeeFalso()
    loja.comentarios = [
        comentario(9001, 2, estrelas=2, texto="veio riscado", media={"video_url_list": [URL_VIDEO]})
    ]
    redis_falso.dados[avaliacoes.CHAVE_IMPORTADA_SHOPEE.format(integ.id)] = "ok"

    await _rodada_shopee(integ, loja)
    # Passou da carência (2★, 1 h): relida pelo id antes de virar pendência.
    assert loja.de("por_id") == ["9001"]
    r = await avaliacoes.atualizar_pendencias(agora=T0)
    assert r == {"pendentes_novas": 1, "resolvidas": 0, "atualizadas": 0, "erros": 0}

    linha = await _linha(db, "9001")
    assert linha.pendente_desde == T0 and linha.comprador_id == "777"
    conversa = await _conversa_da_avaliacao(db, "9001")
    assert conversa is not None and linha.conversa_id == conversa.id
    assert conversa.situacao != CONVERSA_BLOQUEADA  # a Shopee responde pela API
    assert (conversa.plataforma, conversa.pedido_marketplace) == ("shopee", SN)
    assert conversa.comprador_id == "777" and conversa.etiqueta == ETIQUETA_AVALIACAO
    # Na fila, com prazo de 24 h a partir da pendência (interno).
    assert conversa.aguardando_resposta is True
    assert conversa.prazo_resposta_em == T0 + timedelta(hours=24)
    msgs = await _mensagens(db, conversa.id)
    assert [m.autor for m in msgs] == ["cliente"]
    assert msgs[0].texto == "Avaliação 2★ — veio riscado"
    assert msgs[0].anexos == [{"tipo": "video", "url": URL_VIDEO}]
    # O chat do mesmo pedido também fica AVALIAÇÃO, com a linha do tempo.
    chat = await db.get(AtendimentoConversa, chat_id, populate_existing=True)
    assert chat.etiqueta == ETIQUETA_AVALIACAO
    hist = (
        (
            await db.execute(
                select(AtendimentoEtiquetaHistorico).where(
                    AtendimentoEtiquetaHistorico.conversa_id == chat_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [(h.de, h.para) for h in hist] == [("pos_venda", "avaliacao")]
    assert hist[0].motivo == (
        "Avaliação 2★ sem resposta da loja na Shopee (leitura das avaliações)"
    )

    # A loja respondeu (por fora): a releitura traz e a etiqueta volta.
    loja.comentarios[0] = comentario(
        9001,
        2,
        estrelas=2,
        texto="veio riscado",
        resposta="Vamos resolver!",
        resposta_horas_atras=0.2,
    )
    await avaliacoes.sincronizar_loja_shopee(
        integ.id, fabrica_cliente=_fabrica(loja), agora=T0 + timedelta(minutes=30)
    )
    r = await avaliacoes.atualizar_pendencias(agora=T0 + timedelta(minutes=30))
    assert r["resolvidas"] == 1
    linha = await _linha(db, "9001")
    assert linha.pendente_desde is None and linha.resposta_loja == "Vamos resolver!"
    conversa = await _conversa_da_avaliacao(db, "9001")
    assert conversa.aguardando_resposta is False and conversa.etiqueta == "pos_venda"
    msgs = await _mensagens(db, conversa.id)
    assert [(m.autor, m.origem) for m in msgs] == [("cliente", "cliente"), ("loja", "externo")]
    chat = await db.get(AtendimentoConversa, chat_id, populate_existing=True)
    assert chat.etiqueta == "pos_venda"
    ultima = (
        await db.execute(
            select(AtendimentoEtiquetaHistorico)
            .where(AtendimentoEtiquetaHistorico.conversa_id == chat_id)
            .order_by(AtendimentoEtiquetaHistorico.em.desc())
            .limit(1)
        )
    ).scalar_one()
    assert (ultima.de, ultima.para) == ("avaliacao", "pos_venda")
    assert ultima.motivo == "Avaliação resolvida (avaliação respondida)"


@pytest.mark.parametrize(
    ("estrelas", "horas", "resposta", "pendente"),
    [
        (1, 0.5, None, False),  # 1–3★ antes de 1 h: o robô de fora ainda pode responder
        (2, 1.5, None, True),
        (5, 2, None, False),  # 4–5★ antes de 24 h
        (5, 25, None, True),
        (4, 30, "Obrigado!", False),  # respondida nunca é pendência
        (1, 24 * 31, None, False),  # fora da janela de 30 dias
    ],
)
async def test_carencia_e_janela(db, make_user, redis_falso, estrelas, horas, resposta, pendente):
    integ = await _loja(db, make_user)
    loja = ShopeeFalso()
    loja.comentarios = [comentario(9100, horas, estrelas=estrelas, resposta=resposta)]
    redis_falso.dados[avaliacoes.CHAVE_IMPORTADA_SHOPEE.format(integ.id)] = "ok"
    await _rodada_shopee(integ, loja)
    await avaliacoes.atualizar_pendencias(agora=T0)
    linha = await _linha(db, "9100")
    assert (linha.pendente_desde is not None) is pendente
    assert (await _conversa_da_avaliacao(db, "9100") is not None) is pendente


async def test_pendente_editada_pelo_comprador_atualiza_a_conversa(db, make_user, redis_falso):
    integ = await _loja(db, make_user)
    loja = ShopeeFalso()
    loja.comentarios = [comentario(9400, 2, estrelas=2, texto="veio riscado")]
    redis_falso.dados[avaliacoes.CHAVE_IMPORTADA_SHOPEE.format(integ.id)] = "ok"
    await _rodada_shopee(integ, loja)
    await avaliacoes.atualizar_pendencias(agora=T0)
    conversa = await _conversa_da_avaliacao(db, "9400")
    assert conversa.dados["estrelas"] == 2
    # O comprador editou (nota e texto) enquanto ela estava pendente.
    loja.comentarios[0] = comentario(
        9400, 2, estrelas=4, texto="resolvido, chegou ok", editable="HAVE_EDITED_ONCE"
    )
    await avaliacoes.sincronizar_loja_shopee(
        integ.id, fabrica_cliente=_fabrica(loja), agora=T0 + timedelta(minutes=30)
    )
    r = await avaliacoes.atualizar_pendencias(agora=T0 + timedelta(minutes=30))
    assert r["atualizadas"] == 1 and r["resolvidas"] == 0
    conversa = await _conversa_da_avaliacao(db, "9400")
    assert conversa.dados["estrelas"] == 4
    assert [m.texto for m in await _mensagens(db, conversa.id)] == [
        "Avaliação 2★ — veio riscado",
        "Avaliação 4★ — resolvido, chegou ok",
    ]
    assert (await _linha(db, "9400")).pendente_desde == T0
    # Sem mudança: não mexe de novo.
    r = await avaliacoes.atualizar_pendencias(agora=T0 + timedelta(hours=1))
    assert r["atualizadas"] == 0


async def test_releitura_pelo_id_tem_intervalo_e_a_que_sumiu_nao_vira_pendencia(
    db, make_user, redis_falso
):
    integ = await _loja(db, make_user)
    loja = ShopeeFalso()
    loja.comentarios = [comentario(9200, 30, estrelas=5), comentario(9201, 30, estrelas=3)]
    redis_falso.dados[avaliacoes.CHAVE_IMPORTADA_SHOPEE.format(integ.id)] = "ok"
    await _rodada_shopee(integ, loja)
    assert sorted(loja.de("por_id")) == ["9200", "9201"]
    # Logo depois: já conferidas — nada de GET de novo.
    loja.chamadas.clear()
    await avaliacoes.sincronizar_loja_shopee(
        integ.id, fabrica_cliente=_fabrica(loja), agora=T0 + timedelta(minutes=10)
    )
    assert loja.de("por_id") == []
    # A 9201 sumiu da Shopee. A 1ª leitura vazia pode ser falha passageira:
    # ainda não "sumiu", mas perde a conferida (não vira pendência no meio).
    sumida = loja.comentarios.pop(1)
    await avaliacoes.sincronizar_loja_shopee(
        integ.id, fabrica_cliente=_fabrica(loja), agora=T0 + timedelta(hours=4)
    )
    assert sorted(loja.de("por_id")) == ["9200", "9201"]
    await avaliacoes.atualizar_pendencias(agora=T0 + timedelta(hours=4))
    linha = await _linha(db, "9201")
    assert linha.dados.get("vazias") == 1 and "sumiu" not in linha.dados
    assert "conferida_em" not in linha.dados and linha.pendente_desde is None
    assert (await _linha(db, "9200")).pendente_desde is not None
    # A 2ª vazia seguida (relida logo, sem a conferida): sumiu, sem pendência.
    loja.chamadas.clear()
    await avaliacoes.sincronizar_loja_shopee(
        integ.id, fabrica_cliente=_fabrica(loja), agora=T0 + timedelta(hours=4, minutes=30)
    )
    assert loja.de("por_id") == ["9201"]
    await avaliacoes.atualizar_pendencias(agora=T0 + timedelta(hours=4, minutes=30))
    linha = await _linha(db, "9201")
    assert linha.dados.get("sumiu") is True and linha.pendente_desde is None
    # Voltou na página da Shopee: a marca sai, é relida e vira pendência.
    loja.comentarios.append(sumida)
    await avaliacoes.sincronizar_loja_shopee(
        integ.id, fabrica_cliente=_fabrica(loja), agora=T0 + timedelta(hours=5)
    )
    await avaliacoes.atualizar_pendencias(agora=T0 + timedelta(hours=5))
    linha = await _linha(db, "9201")
    assert "sumiu" not in linha.dados and "vazias" not in linha.dados
    assert linha.pendente_desde == T0 + timedelta(hours=5)


async def test_releitura_que_nao_gravou_nao_carimba_a_conferida(db, make_user, redis_falso):
    integ = await _loja(db, make_user)
    loja = ShopeeFalso()
    loja.comentarios = [comentario(9300, 2, estrelas=2)]
    redis_falso.dados[avaliacoes.CHAVE_IMPORTADA_SHOPEE.format(integ.id)] = "ok"
    # Gravada ainda dentro da carência (meia hora de idade): sem releitura.
    await avaliacoes.sincronizar_loja_shopee(
        integ.id, fabrica_cliente=_fabrica(loja), agora=T0 - timedelta(hours=1, minutes=30)
    )
    assert loja.de("por_id") == []
    # A releitura volta ilegível (nota que não dá para ler): conta erro e
    # NÃO carimba — senão a sem resposta gravada antes virava pendência.
    loja.comentarios[0]["rating_star"] = None
    r = await _rodada_shopee(integ, loja)
    assert loja.de("por_id") == ["9300"] and r["erros"] == 1
    await avaliacoes.atualizar_pendencias(agora=T0)
    linha = await _linha(db, "9300")
    assert "conferida_em" not in (linha.dados or {}) and linha.pendente_desde is None
    # Erro de banco na gravação (registrar devolve False): também não carimba.
    loja.comentarios[0]["rating_star"] = 2
    original = indice.registrar_avaliacao
    try:
        indice.registrar_avaliacao = AsyncMock(return_value=False)
        await avaliacoes.sincronizar_loja_shopee(
            integ.id, fabrica_cliente=_fabrica(loja), agora=T0 + timedelta(minutes=5)
        )
    finally:
        indice.registrar_avaliacao = original
    assert "conferida_em" not in ((await _linha(db, "9300")).dados or {})
    # Lida e gravada: carimba e vira pendência.
    await avaliacoes.sincronizar_loja_shopee(
        integ.id, fabrica_cliente=_fabrica(loja), agora=T0 + timedelta(minutes=10)
    )
    await avaliacoes.atualizar_pendencias(agora=T0 + timedelta(minutes=10))
    linha = await _linha(db, "9300")
    assert linha.dados.get("conferida_em") and linha.pendente_desde is not None


async def test_erro_de_uma_loja_nao_para_a_outra(db, make_user, redis_falso):
    ruim = await _loja(db, make_user, nome="aaa")
    boa = await _loja(db, make_user, nome="bbb")
    loja_boa = ShopeeFalso()
    loja_boa.comentarios = [comentario(1, 1, resposta="ok")]

    async def fabrica(integration):
        if integration.id == ruim.id:
            raise RuntimeError("shopee_comentarios error_auth: token")
        return loja_boa

    total = await avaliacoes.sincronizar_todas_shopee(fabrica_cliente=fabrica, agora=T0)
    assert total["lojas"] == 2 and total["lojas_com_erro"] == 1
    assert (await _linha(db, "1")).integration_id == boa.id


async def test_indexar_de_hora_em_hora_deixa_as_avaliacoes_para_o_cron(db, make_user, monkeypatch):
    integ = await _loja(db, make_user)

    class Cliente(ShopeeFalso):
        async def get_order_list_janela(self, **_kw):
            return [], True

        async def get_order_detail_indice(self, _sns):
            return []

    loja = Cliente()
    loja.comentarios = [comentario(1, 1)]
    monkeypatch.setattr(get_settings(), "atendimento_avaliacoes_ativa", True)
    r = await indexar.indexar_loja(integ.id, fabrica_cliente=_fabrica(loja))
    assert r["avaliacoes"] == 0 and loja.chamadas == []
    monkeypatch.setattr(get_settings(), "atendimento_avaliacoes_ativa", False)
    r = await indexar.indexar_loja(integ.id, fabrica_cliente=_fabrica(loja))
    assert r["avaliacoes"] == 1


# ─────────────── Mercado Livre ───────────────


class MLFalso:
    """`/reviews/item/{id}` (por PRODUTO) e `/orders/{id}`, com as chamadas registradas."""

    def __init__(self) -> None:
        self.produtos: dict[str, str] = {}  # anúncio → produto
        self.opinioes: dict[str, list[dict]] = {}  # produto → opiniões
        self.pedidos: dict[str, dict] = {}
        self.chamadas: list[tuple[int, str, dict]] = []

    def responder(self, seller: int, method: str, path: str, params: dict) -> httpx.Response:
        assert method == "GET", "a leitura das avaliações é SÓ GET"
        self.chamadas.append((seller, path, dict(params)))

        def r(status: int, corpo: Any) -> httpx.Response:
            return httpx.Response(
                status, json=corpo, request=httpx.Request(method, f"https://api{path}")
            )

        if path.startswith("/reviews/item/"):
            item = path.rsplit("/", 1)[-1]
            produto = self.produtos.get(item)
            if produto is None:
                return r(404, {"code": "not_found"})
            todas = self.opinioes.get(produto, [])
            ini, lim = int(params.get("offset", 0)), int(params.get("limit", 5))
            niveis = dict.fromkeys(
                ("one_star", "two_star", "three_star", "four_star", "five_star"), 0
            )
            for o in todas:
                niveis[list(niveis)[o["rate"] - 1]] += 1
            return r(
                200,
                {
                    "paging": {
                        "total": len(todas),
                        "limit": lim,
                        "offset": ini,
                        "total_pageable": len(todas),
                    },
                    "helpful_reviews": {"best_max_stars": None, "best_min_stars": None},
                    "quali_attributes": [],
                    "rating_average": 5,
                    "stars": 5,
                    "rating_levels": niveis,
                    "cross_site_enabled": False,
                    "user_product_id": produto,
                    "reviews": todas[ini : ini + lim],
                },
            )
        if path.startswith("/orders/"):
            pedido = self.pedidos.get(path.rsplit("/", 1)[-1])
            return r(200, pedido) if pedido else r(404, {"code": "not_found"})
        return r(404, {"code": "rota_desconhecida"})

    def de(self, trecho: str) -> list:
        return [c for c in self.chamadas if trecho in c[1]]


@pytest.fixture
def ml(monkeypatch) -> MLFalso:
    falso = MLFalso()

    async def _request(self, method, path, *, params=None, json=None):  # noqa: A002
        return falso.responder(int(self.creds.get("user_id") or 0), method, path, params or {})

    monkeypatch.setattr(MercadoLivreClient, "_request", _request)
    return falso


async def _fabrica_ml(integration: Integration) -> MercadoLivreClient:
    return MercadoLivreClient(decrypt_json(integration.credentials))


async def _conta_ml(db, make_user, nome: str, seller: int):
    return await _loja(
        db,
        make_user,
        plataforma="ml",
        nome=nome,
        creds={"access_token": "t", "refresh_token": "r", "user_id": seller, "expires_at": 9e9},
    )


async def _venda_ml(
    db, integ, pedido: str, *, item: str, produto: str | None, comprador="900", pack=None
):
    """A linha do espelho financeiro (`raw.order`, como o financeiro grava)."""
    db.add(
        MarketplaceOrderFinancial(
            platform=IntegrationPlatform.ML,
            integration_id=integ.id,
            external_order_id=pack or pedido,
            raw={
                "pack_id": pack,
                "order": {
                    "id": int(pedido),
                    "pack_id": int(pack) if pack else None,
                    "buyer": {"id": int(comprador), "nickname": "NICK_X"},
                    "order_items": [
                        {"item": {"id": item, "title": "Mala de bordo", "user_product_id": produto}}
                    ],
                },
            },
            created_at=T0 - timedelta(days=5),
        )
    )
    await db.commit()


async def test_ml_le_por_produto_na_conta_dona_do_pedido(db, make_user, redis_falso, ml):
    a = await _conta_ml(db, make_user, "aguiar", SELLER_A)
    b = await _conta_ml(db, make_user, "aguiar2", SELLER_B)
    # A conta A vendeu dois anúncios do MESMO produto e um de outro; a B, o
    # mesmo produto (anúncio irmão).
    await _venda_ml(
        db, a, "2000000000000001", item="MLB1", produto="MLBU1", pack="2000000000009999"
    )
    await _venda_ml(db, a, "2000000000000002", item="MLB2", produto="MLBU1")
    await _venda_ml(db, b, "2000000000000003", item="MLB9", produto="MLBU1", comprador="901")
    ml.produtos = {"MLB1": "MLBU1", "MLB2": "MLBU1", "MLB9": "MLBU1"}
    ml.opinioes["MLBU1"] = [
        _opiniao(501, 2000000000000001, rate=2, item="MLB1"),
        _opiniao(502, 2000000000000003, rate=5, item="MLB9"),
        _opiniao(503, 2000000000000077, rate=1, item="MLB1"),  # pedido de outro vendedor
        _opiniao(504, 2000000000000001, rate=4, dias=40, item="MLB1"),  # fora da janela
    ]
    ml.pedidos["2000000000000077"] = {"id": 2000000000000077, "seller": {"id": 123}}

    total = await avaliacoes.sincronizar_todas_ml(fabrica_cliente=_fabrica_ml, agora=T0)
    assert total["contas"] == 2 and total["contas_com_erro"] == 0
    # O produto é lido UMA vez na rodada (a impressão + a página), por uma conta só.
    assert len(ml.de("/reviews/item/")) == 2
    assert total["alheias"] == 1 and total["antigas"] == 1
    linhas = {
        x.comentario_id: x
        for x in (
            await db.execute(
                select(AtendimentoAvaliacaoLoja).execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    }
    assert set(linhas) == {"501", "502"}
    assert linhas["501"].integration_id == a.id and linhas["502"].integration_id == b.id
    assert linhas["501"].comprador_id == "900" and linhas["502"].comprador_id == "901"
    assert linhas["501"].dados["pack_id"] == "2000000000009999"
    assert linhas["501"].dados["anuncio_titulo"] == "Mala de bordo"
    assert linhas["501"].pode_responder is False
    assert "Mercado Livre não permite responder" in linhas["501"].motivo_sem_resposta
    # O pedido alheio fica marcado: não se pergunta de novo.
    assert redis_falso.dados[avaliacoes.CHAVE_PEDIDO_ALHEIO_ML.format("2000000000000077")] == "1"

    # Rodada seguinte, nada mudou: só a impressão (limit=1).
    ml.chamadas.clear()
    await avaliacoes.sincronizar_todas_ml(fabrica_cliente=_fabrica_ml, agora=T0)
    assert [c[2]["limit"] for c in ml.de("/reviews/item/")] == [1]
    assert ml.de("/orders/") == []


async def test_ml_nota_baixa_vira_pendencia_bloqueada_e_sai_com_tratada(
    client, db, make_user, auth_as, redis_falso, ml, rotas, monkeypatch
):
    a = await _conta_ml(db, make_user, "aguiar", SELLER_A)
    await _venda_ml(
        db, a, "2000000000000001", item="MLB1", produto="MLBU1", pack="2000000000009999"
    )
    ml.produtos = {"MLB1": "MLBU1"}
    ml.opinioes["MLBU1"] = [
        _opiniao(601, 2000000000000001, rate=2, item="MLB1"),
        _opiniao(602, 2000000000000001, rate=5, item="MLB1"),
    ]
    # A conversa do comprador no pós-venda é a do PACK.
    pack_id = await _chat_do_pedido(db, a, plataforma="ml", pedido="2000000000009999")
    await avaliacoes.sincronizar_todas_ml(fabrica_cliente=_fabrica_ml, agora=T0)
    r = await avaliacoes.atualizar_pendencias(agora=T0)
    assert r["pendentes_novas"] == 1  # a 5★ do ML é só informação

    conversa = await _conversa_da_avaliacao(db, "601")
    assert conversa.situacao == CONVERSA_BLOQUEADA
    assert "não permite responder" in conversa.bloqueio_motivo
    assert conversa.dados["pack_id"] == "2000000000009999"
    assert conversa.aguardando_resposta is True  # alguém precisa agir (tratar)
    pack = await db.get(AtendimentoConversa, pack_id, populate_existing=True)
    assert pack.etiqueta == ETIQUETA_AVALIACAO

    monkeypatch.setattr(rota_atendimento, "SO_ADMIN", False)
    auth_as(await make_user(role=UserRole.ADMIN))
    linha = await _linha(db, "601")
    # Responder no ML: não há API.
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)
    r = await client.post(f"/api/atendimento/avaliacoes/{linha.id}/responder", json={"texto": "x"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "somente_leitura"
    # Tratada: sai da pendência e a etiqueta volta.
    r = await client.post(
        f"/api/atendimento/avaliacoes/{linha.id}/tratada", json={"motivo": "liguei pra ele"}
    )
    assert r.status_code == 200
    corpo = r.json()["avaliacao"]
    assert corpo["pendente"] is False and corpo["tratada_em"] and corpo["tratada_por_nome"]
    linha = await _linha(db, "601")
    assert linha.dados["tratada_motivo"] == "liguei pra ele"
    conversa = await _conversa_da_avaliacao(db, "601")
    assert conversa.aguardando_resposta is False
    msgs = await _mensagens(db, conversa.id)
    assert msgs[-1].autor == "sistema" and "tratada" in msgs[-1].texto
    pack = await db.get(AtendimentoConversa, pack_id, populate_existing=True)
    assert pack.etiqueta == "pos_venda"
    # De novo: idempotente.
    r = await client.post(f"/api/atendimento/avaliacoes/{linha.id}/tratada", json={})
    assert r.status_code == 200


# ─────────────── o cron ───────────────


async def test_cron_so_com_os_dois_interruptores_e_uma_vez(db, make_user, redis_falso, monkeypatch):
    integ = await _loja(db, make_user)
    loja = ShopeeFalso()
    loja.comentarios = [comentario(1, 1, resposta="ok")]
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_leitura_ativa", False)
    monkeypatch.setattr(s, "atendimento_avaliacoes_ativa", True)
    assert await avaliacoes.atendimento_avaliacoes(None, fabrica_cliente=_fabrica(loja)) is None
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_avaliacoes_ativa", False)
    assert await avaliacoes.atendimento_avaliacoes(None, fabrica_cliente=_fabrica(loja)) is None
    assert loja.chamadas == []

    monkeypatch.setattr(s, "atendimento_avaliacoes_ativa", True)
    redis_falso.dados[avaliacoes.CHAVE_TRAVA] = "outra"
    assert await avaliacoes.atendimento_avaliacoes(None, fabrica_cliente=_fabrica(loja)) == {
        "pulado": True
    }
    del redis_falso.dados[avaliacoes.CHAVE_TRAVA]
    resumo = await avaliacoes.atendimento_avaliacoes(None, fabrica_cliente=_fabrica(loja))
    assert resumo["shopee"]["lojas"] == 1 and "ml" in resumo and "pendencias" in resumo
    assert avaliacoes.CHAVE_TRAVA not in redis_falso.dados
    # O ML só a cada INTERVALO_ML.
    resumo = await avaliacoes.atendimento_avaliacoes(None, fabrica_cliente=_fabrica(loja))
    assert "ml" not in resumo
    assert (await _linha(db, "1")).integration_id == integ.id


# ─────────────── as rotas ───────────────


async def _pendente_shopee(db, make_user, redis_falso, *, modo="humano"):
    integ = await _loja(db, make_user)
    db.add(
        AtendimentoCanal(
            integration_id=integ.id, plataforma="shopee", canal="chat", modo=modo, status="ok"
        )
    )
    await db.commit()
    chat_id = await _chat_do_pedido(db, integ)
    loja = ShopeeFalso()
    loja.comentarios = [comentario(9301, 3, estrelas=1, texto="não gostei")]
    redis_falso.dados[avaliacoes.CHAVE_IMPORTADA_SHOPEE.format(integ.id)] = "ok"
    await _rodada_shopee(integ, loja)
    # Uma avaliação ANTERIOR do mesmo comprador (outro pedido), já respondida.
    loja.comentarios = [
        comentario(9302, 24 * 20, estrelas=5, sn="250901XXXXXXXX", resposta="Valeu!")
    ]
    await _rodada_shopee(integ, loja, importar=True)
    await avaliacoes.atualizar_pendencias(agora=T0)
    return integ, chat_id


async def test_aba_avaliacao_e_responder_desligado(
    client, db, make_user, auth_as, redis_falso, rotas, monkeypatch
):
    integ, chat_id = await _pendente_shopee(db, make_user, redis_falso)
    # Só admin (em ATENDIMENTO_USUARIOS) enquanto a caixa é observação.
    auth_as(await make_user(role=UserRole.USER))
    r = await client.get(f"/api/atendimento/conversas/{chat_id}/avaliacoes")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "admin_only"

    monkeypatch.setattr(rota_atendimento, "SO_ADMIN", False)
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get(f"/api/atendimento/conversas/{chat_id}/avaliacoes")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["pendentes"] == 1 and corpo["pior_pendente"] == 1
    assert corpo["envio_ativo"] is False and "PÚBLICA" in corpo["aviso"]
    primeira, anterior = corpo["itens"]
    assert (primeira["comentario_id"], primeira["do_pedido"], primeira["nota_baixa"]) == (
        "9301",
        True,
        True,
    )
    assert primeira["texto"] == "não gostei" and primeira["pendente"] is True
    assert primeira["pode_responder"] is False
    assert "Resposta pública" in primeira["motivo_sem_resposta"]
    assert primeira["url_plataforma"].endswith(f"/portal/sale/order/{SN}")
    assert (anterior["comentario_id"], anterior["do_pedido"], anterior["respondida"]) == (
        "9302",
        False,
        True,
    )

    # Responder com o envio desligado: 409 ANTES de tudo — nada criado, nada sai.
    linha = await _linha(db, "9301")
    conversa_antes = linha.conversa_id
    r = await client.post(
        f"/api/atendimento/avaliacoes/{linha.id}/responder", json={"texto": "Sentimos muito"}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "envio_desligado"
    assert "Resposta pública" in r.json()["detail"]["detail"]
    assert (await _linha(db, "9301")).conversa_id == conversa_antes
    n = (
        (await db.execute(select(AtendimentoMensagem).where(AtendimentoMensagem.autor == "loja")))
        .scalars()
        .all()
    )
    assert n == [] or all(m.origem == "externo" for m in n)

    # Os erros de sempre.
    r = await client.get("/api/atendimento/conversas/ig:abc/avaliacoes")
    assert r.status_code == 200 and r.json()["itens"] == []
    nenhuma = "00000000-0000-0000-0000-000000000000"
    r = await client.post(f"/api/atendimento/avaliacoes/{nenhuma}/responder", json={"texto": "x"})
    assert r.status_code == 404


class _Rede:
    """O httpx de verdade sobre um transporte falso (a Shopee): guarda cada pedido."""

    def __init__(self, monkeypatch, handler) -> None:
        self.feitos: list[httpx.Request] = []

        def registrar(request: httpx.Request) -> httpx.Response:
            self.feitos.append(request)
            return handler(request)

        def fabrica(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(registrar)
            return _AsyncClientReal(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", fabrica)


def _shopee_responde(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v2/product/reply_comment":
        corpo = json.loads(request.content)
        (item,) = corpo["comment_list"]
        return httpx.Response(
            200,
            json={
                "error": "",
                "message": "",
                "response": {
                    "result_list": [
                        {"comment_id": item["comment_id"], "fail_error": "", "fail_message": ""}
                    ],
                    "warning": [],
                },
            },
        )
    return httpx.Response(404, json={"error": "error_not_found", "message": "x"})


async def test_responder_ligado_sai_pela_api_e_a_etiqueta_volta(
    client, db, make_user, auth_as, redis_falso, rotas, monkeypatch
):
    integ, chat_id = await _pendente_shopee(db, make_user, redis_falso)
    monkeypatch.setattr(rota_atendimento, "SO_ADMIN", False)
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)
    rede = _Rede(monkeypatch, _shopee_responde)

    async def fabrica(_integration):
        return ShopeeClient({"shop_id": 111, "access_token": "t", "expires_at": 9_999_999_999})

    monkeypatch.setattr(clientes, "cliente_da_integracao", fabrica)
    auth_as(await make_user(role=UserRole.ADMIN))
    linha = await _linha(db, "9301")
    r = await client.post(
        f"/api/atendimento/avaliacoes/{linha.id}/responder",
        json={"texto": "Sentimos muito pelo ocorrido, estamos à disposição."},
    )
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["mensagem"]["status"] == "enviada" and corpo["mensagem"]["autor"] == "loja"
    assert corpo["avaliacao"]["respondida"] is True and corpo["avaliacao"]["pendente"] is False
    # A resposta saiu pela API pública da avaliação — e só ela.
    assert [(p.method, p.url.path) for p in rede.feitos] == [
        ("POST", "/api/v2/product/reply_comment")
    ]
    assert json.loads(rede.feitos[0].content) == {
        "comment_list": [
            {"comment_id": 9301, "comment": "Sentimos muito pelo ocorrido, estamos à disposição."}
        ]
    }
    linha = await _linha(db, "9301")
    assert linha.resposta_loja.startswith("Sentimos muito") and linha.pendente_desde is None
    assert linha.dados["respondida_pelo_davinci"] is True
    conversa = await _conversa_da_avaliacao(db, "9301")
    msgs = await _mensagens(db, conversa.id)
    assert msgs[-1].externo_id == "resposta:9301" and msgs[-1].origem == "davinci_humano"
    assert conversa.aguardando_resposta is False
    chat = await db.get(AtendimentoConversa, chat_id, populate_existing=True)
    assert chat.etiqueta == "pos_venda"

    # A leitura seguinte traz a mesma resposta: não duplica.
    loja = ShopeeFalso()
    loja.comentarios = [
        comentario(9301, 3, estrelas=1, texto="não gostei", resposta=linha.resposta_loja)
    ]
    await _rodada_shopee(integ, loja)
    await avaliacoes.atualizar_pendencias(agora=T0)
    assert len(await _mensagens(db, conversa.id)) == len(msgs)

    # Já respondida: 409.
    r = await client.post(f"/api/atendimento/avaliacoes/{linha.id}/responder", json={"texto": "x"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ja_respondida"
    # A caixa de baixo da conversa `avaliacao` também recusa (o `enviar`, não
    # só a rota da avaliação) — e a faixa acima dela diz o mesmo.
    r = await client.post(
        f"/api/atendimento/conversas/{conversa.id}/responder", json={"texto": "de novo"}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ja_respondida"
    recusa = await enviar.motivo_para_nao_enviar(db, conversa)
    assert recusa is not None and recusa.code == enviar.RECUSA_AVALIACAO_JA_RESPONDIDA
    assert [(p.method, p.url.path) for p in rede.feitos] == [
        ("POST", "/api/v2/product/reply_comment")
    ]


async def test_responder_com_a_loja_em_observar_recusa(
    client, db, make_user, auth_as, redis_falso, rotas, monkeypatch
):
    await _pendente_shopee(db, make_user, redis_falso, modo="observar")
    monkeypatch.setattr(rota_atendimento, "SO_ADMIN", False)
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)
    rede = _Rede(monkeypatch, _shopee_responde)
    auth_as(await make_user(role=UserRole.ADMIN))
    linha = await _linha(db, "9301")
    r = await client.post(
        f"/api/atendimento/avaliacoes/{linha.id}/responder", json={"texto": "Obrigado"}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "canal_em_observacao"
    assert rede.feitos == []


async def test_a_ia_nunca_responde_avaliacao(db, make_user, redis_falso, monkeypatch):
    await _pendente_shopee(db, make_user, redis_falso)
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)
    conversa = await _conversa_da_avaliacao(db, "9301")
    recusa = await enviar.motivo_para_nao_enviar(db, conversa, origem=ORIGEM_IA)
    assert recusa is not None and recusa.code == enviar.RECUSA_AUTO_DESLIGADO
    # Com o envio desligado, a faixa diz "resposta pública".
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", False)
    recusa = await enviar.motivo_para_nao_enviar(db, conversa)
    assert recusa.code == "envio_desligado" and "Resposta pública" in recusa.detail


@pytest.mark.parametrize(
    ("resposta", "esperado"),
    [
        ({"result_list": [{"comment_id": 9301, "fail_error": "error_param"}]}, ("falhou", None)),
        ({"result_list": []}, ("ambiguo", "shopee resposta_sem_resultado")),
        (
            {"result_list": [{"comment_id": 9301, "fail_error": ""}], "warning": []},
            ("ok", "resposta:9301"),
        ),
    ],
)
async def test_adaptador_da_resposta_da_shopee(resposta, esperado):
    class Cliente:
        async def reply_comment(self, comment_id, texto):
            assert (comment_id, texto) == ("9301", "Obrigado!")
            return resposta

    conversa = SimpleNamespace(id="c", externo_id="9301")
    r = await shopee_adapter.responder_avaliacao(None, conversa, None, Cliente(), "Obrigado!")
    if esperado[0] == "ok":
        assert r.ok and r.externo_id == esperado[1]
    elif esperado[0] == "ambiguo":
        assert not r.ok and r.ambiguo and r.erro == esperado[1]
    else:
        assert not r.ok and not r.ambiguo and r.erro == "shopee error_param"

    class Lento:
        async def reply_comment(self, *_a):
            raise httpx.ReadTimeout("t")

    r = await shopee_adapter.responder_avaliacao(None, conversa, None, Lento(), "Obrigado!")
    assert r.ambiguo is True


async def test_cliente_shopee_reply_comment_e_post_com_o_corpo_certo(monkeypatch):
    rede = _Rede(monkeypatch, _shopee_responde)
    cliente = ShopeeClient({"shop_id": 111, "access_token": "t", "expires_at": 9_999_999_999})
    resp = await cliente.reply_comment("9301", "Obrigado!")
    assert resp["result_list"][0]["comment_id"] == 9301
    (feito,) = rede.feitos
    assert feito.method == "POST" and feito.url.params["shop_id"] == "111"


async def test_cliente_ml_opinioes_so_get(monkeypatch):
    feitos = []

    async def _request(self, method, path, *, params=None, json=None):  # noqa: A002
        feitos.append((method, path, params))
        return httpx.Response(200, json={"reviews": []}, request=httpx.Request(method, "https://x"))

    monkeypatch.setattr(MercadoLivreClient, "_request", _request)
    cliente = MercadoLivreClient({"access_token": "t", "expires_at": 9e9})
    await cliente.opinioes_do_anuncio("MLB1", limit=500, offset=-3)
    assert feitos == [("GET", "/reviews/item/MLB1", {"limit": 50, "offset": 0})]


# ─────────────── a lista, o resumo, o contexto da IA ───────────────


async def test_lista_filtra_por_avaliacao_com_as_estrelas_e_conta_no_resumo(
    client, db, make_user, auth_as, redis_falso, monkeypatch
):
    _, chat_id = await _pendente_shopee(db, make_user, redis_falso)
    monkeypatch.setattr(rota_atendimento, "SO_ADMIN", False)
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get("/api/atendimento/conversas", params={"filtro": "avaliacao"})
    assert r.status_code == 200
    itens = r.json()["itens"]
    conversa = await _conversa_da_avaliacao(db, "9301")
    assert {i["id"] for i in itens} == {str(chat_id), str(conversa.id)}
    assert {i["avaliacao_estrelas"] for i in itens} == {1}
    assert all(i["etiqueta"] == "avaliacao" for i in itens)
    r = await client.get(
        "/api/atendimento/conversas", params={"filtro": "aguardando", "etiqueta": "avaliacao"}
    )
    assert [i["id"] for i in r.json()["itens"]] == [str(conversa.id)]
    r = await client.get("/api/atendimento/resumo")
    assert r.json()["etiquetas"]["avaliacao"] == 2
    # O detalhe também traz as estrelas.
    r = await client.get(f"/api/atendimento/conversas/{conversa.id}")
    assert r.json()["conversa"]["avaliacao_estrelas"] == 1


async def test_reclamacao_aberta_vale_mais_e_a_avaliacao_vira_indicador(db, make_user, redis_falso):
    integ, chat_id = await _pendente_shopee(db, make_user, redis_falso)
    db.add(
        AtendimentoReclamacao(
            integration_id=integ.id,
            plataforma="shopee",
            externo_id="R1",
            tipo="reclamacao",
            pedido_marketplace=SN,
            aberta_em=T0,
        )
    )
    await db.commit()
    chat = await db.get(AtendimentoConversa, chat_id, populate_existing=True)
    await etiqueta.recalcular_etiqueta(db, chat, motivo="teste")
    await db.commit()
    assert (chat.etiqueta, chat.etiquetas_secundarias) == ("reclamacao", ["avaliacao"])


async def test_contexto_e_ia_sabem_da_avaliacao_sem_o_texto(db, make_user, redis_falso):
    _, chat_id = await _pendente_shopee(db, make_user, redis_falso)
    chat = await db.get(AtendimentoConversa, chat_id, populate_existing=True)
    ctx = await contexto.contexto_da_conversa(db, chat)
    pendente = next(a for a in ctx["avaliacoes"] if a["pendente"])
    assert pendente["estrelas"] == 1 and pendente["do_pedido"] is True
    # Nada de texto do comprador no contexto (ele vai para o log do rascunho).
    assert "não gostei" not in json.dumps(ctx, ensure_ascii=False, default=str)
    fatos = ia._fatos_para_o_modelo(chat, ctx, {})
    assert fatos["avaliacao_pendente_estrelas"] == 1
    assert contexto.vazio()["avaliacoes"] == []


# ─────────────── a importação inicial e o script ───────────────


async def test_ml_com_produto_adiado_volta_na_rodada_seguinte(
    db, make_user, redis_falso, ml, monkeypatch
):
    a = await _conta_ml(db, make_user, "aguiar", SELLER_A)
    await _venda_ml(db, a, "2000000000000001", item="MLB1", produto="MLBU1")
    await _venda_ml(db, a, "2000000000000002", item="MLB2", produto="MLBU2")
    ml.produtos = {"MLB1": "MLBU1", "MLB2": "MLBU2"}
    ml.opinioes = {
        "MLBU1": [_opiniao(701, 2000000000000001, rate=5, item="MLB1")],
        "MLBU2": [_opiniao(702, 2000000000000002, rate=4, item="MLB2")],
    }
    monkeypatch.setattr(avaliacoes, "MAX_PRODUTOS_LIDOS_ML", 1)
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_avaliacoes_ativa", True)
    r = await avaliacoes.atendimento_avaliacoes(None, fabrica_cliente=_fabrica_ml)
    assert r["ml"]["adiados"] == 1 and r["ml"]["produtos_lidos"] == 1
    # Ficou produto: a marca do ML não foi gravada — a rodada seguinte continua.
    assert avaliacoes.CHAVE_ULTIMA_ML not in redis_falso.dados
    r = await avaliacoes.atendimento_avaliacoes(None, fabrica_cliente=_fabrica_ml)
    assert r["ml"]["produtos_lidos"] == 1 and not r["ml"].get("adiados")
    assert avaliacoes.CHAVE_ULTIMA_ML in redis_falso.dados
    assert {(await _linha(db, x)).comentario_id for x in ("701", "702")} == {"701", "702"}


async def test_ml_produto_acima_do_teto_de_paginas_nao_fica_adiado(
    db, make_user, redis_falso, ml, monkeypatch
):
    a = await _conta_ml(db, make_user, "aguiar", SELLER_A)
    await _venda_ml(db, a, "2000000000000001", item="MLB1", produto="MLBU1")
    ml.produtos = {"MLB1": "MLBU1"}
    ml.opinioes["MLBU1"] = [
        _opiniao(800 + i, 2000000000000001, rate=5, item="MLB1") for i in range(5)
    ]
    # O teto (2 páginas de 2) fica abaixo das 5 pagináveis.
    monkeypatch.setattr(avaliacoes, "PAGINA_ML", 2)
    monkeypatch.setattr(avaliacoes, "MAX_PAGINAS_PRODUTO_ML", 2)
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_avaliacoes_ativa", True)
    r = await avaliacoes.atendimento_avaliacoes(None, fabrica_cliente=_fabrica_ml)
    # Truncado, não adiado: a impressão e a marca do ML são gravadas (senão
    # o produto voltava a cada 30 min e o ML não esperava as 4 h).
    assert r["ml"]["truncados"] == 1 and not r["ml"].get("adiados")
    assert avaliacoes.CHAVE_PRODUTO_ML.format("MLBU1") in redis_falso.dados
    assert avaliacoes.CHAVE_ULTIMA_ML in redis_falso.dados
    gravadas = (
        (
            await db.execute(
                select(AtendimentoAvaliacaoLoja.comentario_id).where(
                    AtendimentoAvaliacaoLoja.plataforma == "ml"
                )
            )
        )
        .scalars()
        .all()
    )
    assert sorted(gravadas) == ["800", "801", "802", "803"]


async def test_script_seco_so_conta_pelo_banco_e_gravar_precisa_da_leitura(
    db, make_user, redis_falso, monkeypatch, capsys
):
    from scripts import atendimento_avaliacoes_backfill as script

    integ = await _loja(db, make_user)
    for cid, horas, estrelas in (("1", 30, 5), ("2", 3, 2), ("3", 0.5, 1)):
        linha = indice.avaliacao_shopee(comentario(int(cid), horas, estrelas=estrelas))
        await indice.registrar_avaliacao(db, integration_id=integ.id, **linha)
    await db.commit()
    monkeypatch.setattr(get_settings(), "atendimento_leitura_ativa", False)

    assert await script._rodar(script._argumentos([])) == 0
    saida = json.loads(capsys.readouterr().out)
    assert saida["seco"] is True
    assert saida["shopee"]["lojas"] == 1 and saida["shopee"]["lojas_importadas"] == 0
    # A de 0,5 h ainda está na carência; as outras duas, não.
    assert saida["shopee"]["sem_resposta_30d_fora_da_carencia"] == 2
    # Sem conferir de novo na Shopee, nenhuma vira pendência.
    assert saida["virariam_pendentes_agora"] == 0 and saida["pendentes_agora"] == 0
    # Gravar fala com as lojas: só com a leitura ligada.
    assert await script._rodar(script._argumentos(["--gravar"])) == 2
    assert "LEITURA" in capsys.readouterr().out


# ─────────────── a integração: main.py e worker.py ───────────────


def test_router_das_avaliacoes_esta_no_main_uma_vez_com_a_trava():
    base = "/api/atendimento"
    esperadas = {
        ("GET", f"{base}/conversas/{{conversa_id}}/avaliacoes"),
        ("POST", f"{base}/avaliacoes/{{avaliacao_id}}/responder"),
        ("POST", f"{base}/avaliacoes/{{avaliacao_id}}/tratada"),
    }
    for metodo, caminho in esperadas:
        achadas = [
            r
            for r in app.routes
            if isinstance(r, APIRoute) and r.path == caminho and metodo in r.methods
        ]
        assert len(achadas) == 1, (metodo, caminho, len(achadas))
        assert achadas[0].endpoint.__module__ == rota_avaliacoes.__name__, caminho
        deps = [d.call for d in achadas[0].dependant.dependencies]
        assert rota_atendimento._so_admin in deps, caminho


def test_cron_das_avaliacoes_no_worker():
    crons = {c.name: c for c in worker.WorkerSettings.cron_jobs}
    aval = crons["cron:atendimento_avaliacoes"]
    assert aval.minute == {12, 42}  # a cada 30 min
    assert not aval.run_at_startup
    assert not {0, 30} & aval.minute  # crons de token (o ML tem refresh de uso único)
    # Nenhum outro cron do atendimento no mesmo minuto: leitura das caixas
    # (ímpares), reclamações, etiquetas, índice de pedidos; nem o preço da
    # Amazon / espelho de NF-e ({4,14,…}).
    for outro in (
        "cron:atendimento_sincronizar",
        "cron:atendimento_reclamacoes",
        "cron:atendimento_etiquetas",
        "cron:atendimento_indexar_pedidos",
        "cron:pos_vendas_notas_sync",
    ):
        assert not crons[outro].minute & aval.minute, outro
    # O arq mata o job junto com o fim da trava (25 min): a trava de um job
    # morto não fica viva por cima da próxima rodada; e cabe nos 30 min.
    assert aval.timeout_s == avaliacoes.TRAVA_TTL_S == 1500 < 30 * 60
    funcoes = {
        getattr(f, "name", getattr(f, "__name__", None)) for f in worker.WorkerSettings.functions
    }
    assert "atendimento_avaliacoes" in funcoes


async def test_cron_das_avaliacoes_no_worker_so_com_os_dois_interruptores(monkeypatch):
    # O worker lê o `_settings` do import.
    s = worker._settings
    rodada = AsyncMock(return_value={"shopee": {}})
    monkeypatch.setattr(avaliacoes, "atendimento_avaliacoes", rodada)
    # Nasce DESLIGADO: com a leitura já ligada em produção, o deploy não liga.
    assert Settings.model_fields["atendimento_avaliacoes_ativa"].default is False

    monkeypatch.setattr(s, "atendimento_avaliacoes_ativa", True)
    monkeypatch.setattr(s, "atendimento_leitura_ativa", False)
    assert await worker.atendimento_avaliacoes({}) is None
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_avaliacoes_ativa", False)
    assert await worker.atendimento_avaliacoes({}) is None
    rodada.assert_not_awaited()

    monkeypatch.setattr(s, "atendimento_avaliacoes_ativa", True)
    assert await worker.atendimento_avaliacoes({}) == {"shopee": {}}
    rodada.assert_awaited_once()

    # Erro inesperado no serviço vira log, nunca derruba o worker.
    rodada.side_effect = RuntimeError("quebrou")
    assert await worker.atendimento_avaliacoes({}) is None
