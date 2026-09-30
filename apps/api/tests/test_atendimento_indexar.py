# ruff: noqa: S105  (tokens de teste de um cliente falso, nada real)
"""O job `atendimento_indexar_pedidos` e o índice do cartão "Cliente" (P5).

Sem rede: a Shopee é um cliente FALSO no formato medido em produção em
28/09/2026 (`formatos_pedido.txt`, `formato_cliente.txt`). Os métodos novos
dos clientes de verdade são conferidos trocando a ida HTTP (`_call`/
`_request`) — nenhuma chamada sai do Mac.

O que se mede: o job só roda com a leitura ligada e uma rodada por vez; pede
os pedidos das últimas 2 h por `update_time` e o detalhe em lotes de 50 SÓ com
os campos do índice; grava sem duplicar e sem apagar o que já sabia; guarda o
id do comprador e nunca o nome; as avaliações param nas já conhecidas (se a
ordem for do mais novo para trás); erro numa parte (ou numa loja) não para o
resto.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoAvaliacaoLoja,
    AtendimentoCanal,
    AtendimentoPedidoComprador,
    Integration,
    IntegrationPlatform,
    User,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import indexar, indice
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient

AGORA = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)


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
    monkeypatch.setattr(indexar, "redis", r)
    monkeypatch.setattr(indice, "redis", r)
    return r


@pytest.fixture
def leitura(monkeypatch):
    def _f(ligada: bool) -> None:
        monkeypatch.setattr(get_settings(), "atendimento_leitura_ativa", ligada)

    _f(True)
    monkeypatch.setattr(indexar, "_agora", lambda: AGORA)
    return _f


def _pedido(sn: str, comprador: int, status: str = "READY_TO_SHIP", total: float = 99.9) -> dict:
    """Pedido no formato do `get_order_detail` — com campos que o índice NÃO guarda."""
    return {
        "order_sn": sn,
        "buyer_user_id": comprador,
        "buyer_username": "apelido_secreto",
        "message_to_seller": "deixar com o porteiro",
        "order_status": status,
        "total_amount": total,
        "create_time": int((AGORA - timedelta(hours=1)).timestamp()),
        "currency": "BRL",
        "item_list": [
            {"item_name": "Mala de Bordo ABS", "model_quantity_purchased": 2, "item_id": 1},
            {"item_name": "Cadeado TSA", "model_quantity_purchased": 1, "item_id": 2},
        ],
    }


def _comentario(cid: int, dias: int, estrelas: int = 5, sn: str = "S1", reply: str = "") -> dict:
    return {
        "comment_id": cid,
        "comment": "chegou rápido" if estrelas > 2 else "veio quebrado",
        "buyer_username": "cliente_x",
        "order_sn": sn,
        "item_id": 1,
        "model_id": 11,
        "create_time": int((AGORA - timedelta(days=dias)).timestamp()),
        "rating_star": estrelas,
        "editable": "EDITABLE",
        "hidden": False,
        "comment_reply": {"reply": reply, "hidden": False, "create_time": 0} if reply else {},
        "media": {},
    }


class ShopeeFalso:
    def __init__(self) -> None:
        self.shop_id = 111
        self.pedidos: dict[str, dict] = {}
        self.completo = True
        self.comentarios: list[dict] = []  # na ordem em que a Shopee devolve
        self.chamadas: list[tuple[str, Any]] = []
        self.erro_lista: Exception | None = None
        self.erro_comentarios: Exception | None = None

    async def get_order_list_janela(self, *, time_from, time_to, time_range_field, maximo):
        self.chamadas.append(("lista", (time_from, time_to, time_range_field, maximo)))
        if self.erro_lista is not None:
            raise self.erro_lista
        return list(self.pedidos), self.completo

    async def get_order_detail_indice(self, order_sns):
        self.chamadas.append(("detalhe", list(order_sns)))
        return [self.pedidos[s] for s in order_sns if s in self.pedidos]

    async def get_comments(self, *, cursor="", page_size=50, item_id=None, comment_id=None):
        self.chamadas.append(("comentarios", cursor))
        if self.erro_comentarios is not None:
            raise self.erro_comentarios
        inicio = int(cursor or 0)
        pagina = self.comentarios[inicio : inicio + page_size]
        fim = inicio + len(pagina)
        mais = fim < len(self.comentarios)
        return {"item_comment_list": pagina, "more": mais, "next_cursor": str(fim) if mais else ""}


async def _loja(db: AsyncSession, user: User, nome: str = "kfa") -> Integration:
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.SHOPEE,
        name=nome,
        credentials=encrypt_json({"access_token": "t", "refresh_token": "r", "shop_id": 111}),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


def _fabrica(por_loja: dict):
    async def _f(integration):
        falso = por_loja[integration.id]
        if isinstance(falso, Exception):
            raise falso
        return falso

    return _f


async def _linhas(db: AsyncSession, modelo):
    db.expire_all()
    return list((await db.execute(select(modelo))).scalars().all())


# ─────────────── o job ───────────────


async def test_desligado_nao_faz_nada(db, make_user, leitura, redis_falso):
    leitura(False)
    user = await make_user()
    integ = await _loja(db, user)
    falso = ShopeeFalso()
    assert await indexar.indexar_pedidos(fabrica_cliente=_fabrica({integ.id: falso})) == {
        "ligado": False
    }
    assert falso.chamadas == []


async def test_uma_rodada_por_vez(db, make_user, leitura, redis_falso):
    user = await make_user()
    integ = await _loja(db, user)
    falso = ShopeeFalso()
    redis_falso.dados[indexar.CHAVE_TRAVA] = "outra-rodada"
    assert await indexar.indexar_pedidos(fabrica_cliente=_fabrica({integ.id: falso})) == {
        "pulado": True
    }
    assert falso.chamadas == []
    assert redis_falso.dados[indexar.CHAVE_TRAVA] == "outra-rodada"  # a trava não é nossa


async def test_pedidos_das_ultimas_2h_em_lotes_de_50_sem_dado_pessoal(
    db, make_user, leitura, redis_falso
):
    user = await make_user()
    integ = await _loja(db, user)
    falso = ShopeeFalso()
    for i in range(120):
        falso.pedidos[f"S{i:03d}"] = _pedido(f"S{i:03d}", comprador=5000 + i % 3)

    resumo = await indexar.indexar_pedidos(fabrica_cliente=_fabrica({integ.id: falso}))

    assert resumo == {"lojas": 1, "pedidos": 120, "avaliacoes": 0, "erros": 0}
    nome, (de, ate, campo, maximo) = falso.chamadas[0]
    assert nome == "lista"
    assert (ate - de, campo, maximo) == (2 * 3600, "update_time", indexar.MAX_PEDIDOS_RODADA)
    assert ate == int(AGORA.timestamp())
    assert [len(a) for n, a in falso.chamadas if n == "detalhe"] == [50, 50, 20]
    linhas = await _linhas(db, AtendimentoPedidoComprador)
    assert len(linhas) == 120
    um = next(x for x in linhas if x.pedido == "S000")
    assert (um.comprador_id, um.plataforma, um.status, um.total) == (
        "5000",
        "shopee",
        "READY_TO_SHIP",
        99.9,
    )
    assert um.itens_resumo == "2× Mala de Bordo ABS; Cadeado TSA"
    assert um.criado_em == AGORA - timedelta(hours=1)
    for x in linhas:
        guardado = f"{x.comprador_id} {x.itens_resumo} {x.status}"
        assert "apelido_secreto" not in guardado
        assert "porteiro" not in guardado
    assert indexar.CHAVE_TRAVA not in redis_falso.dados  # soltou a trava


async def test_upsert_nao_duplica_e_status_anda(db, make_user, leitura, redis_falso):
    user = await make_user()
    integ = await _loja(db, user)
    falso = ShopeeFalso()
    falso.pedidos["S1"] = _pedido("S1", comprador=7)
    await indexar.indexar_pedidos(fabrica_cliente=_fabrica({integ.id: falso}))

    falso.pedidos["S1"] = {**_pedido("S1", comprador=7, status="COMPLETED"), "item_list": []}
    falso.pedidos["S1"].pop("total_amount")
    await indexar.indexar_pedidos(fabrica_cliente=_fabrica({integ.id: falso}))

    linhas = await _linhas(db, AtendimentoPedidoComprador)
    assert len(linhas) == 1
    assert linhas[0].status == "COMPLETED"
    assert linhas[0].total == 99.9  # vazio não apaga o que já se sabia
    assert linhas[0].itens_resumo == "2× Mala de Bordo ABS; Cadeado TSA"


async def test_avaliacoes_param_nas_conhecidas(db, make_user, leitura, redis_falso):
    user = await make_user()
    integ = await _loja(db, user)
    falso = ShopeeFalso()
    # Do mais novo para trás, 120 avaliações (3 páginas de 50).
    falso.comentarios = [_comentario(1000 - i, dias=i) for i in range(120)]

    primeira = await indexar.indexar_pedidos(fabrica_cliente=_fabrica({integ.id: falso}))
    assert primeira["avaliacoes"] == 120
    assert len([c for c in falso.chamadas if c[0] == "comentarios"]) == 3

    # Chegou uma nova no topo (com a resposta da loja numa antiga já da 1ª página).
    falso.comentarios = [_comentario(2000, dias=0, estrelas=1)] + falso.comentarios
    falso.comentarios[3] = _comentario(998, dias=2, reply="Obrigado pela compra!")
    falso.chamadas.clear()
    segunda = await indexar.indexar_pedidos(fabrica_cliente=_fabrica({integ.id: falso}))
    assert segunda["avaliacoes"] == 1
    # A 1ª página tinha uma nova: lê a 2ª, toda conhecida, e para.
    assert len([c for c in falso.chamadas if c[0] == "comentarios"]) == 2

    linhas = {x.comentario_id: x for x in await _linhas(db, AtendimentoAvaliacaoLoja)}
    assert len(linhas) == 121
    assert (linhas["2000"].estrelas, linhas["2000"].texto) == (1, "veio quebrado")
    assert linhas["998"].resposta_loja == "Obrigado pela compra!"
    assert linhas["2000"].comprador_nome_loja == "cliente_x"
    assert linhas["2000"].pedido == "S1"


async def test_avaliacoes_fora_de_ordem_nao_param_cedo(db, make_user, leitura, redis_falso):
    user = await make_user()
    integ = await _loja(db, user)
    falso = ShopeeFalso()
    # Do mais VELHO para o mais novo: "achei conhecidas" não quer dizer "acabou".
    falso.comentarios = [_comentario(i, dias=200 - i) for i in range(120)]
    await indexar.indexar_pedidos(fabrica_cliente=_fabrica({integ.id: falso}))
    falso.comentarios.append(_comentario(5000, dias=0))
    falso.chamadas.clear()

    r = await indexar.indexar_pedidos(fabrica_cliente=_fabrica({integ.id: falso}))

    assert r["avaliacoes"] == 1
    assert len([c for c in falso.chamadas if c[0] == "comentarios"]) == 3


async def test_erro_numa_parte_ou_numa_loja_nao_para_o_resto(db, make_user, leitura, redis_falso):
    user = await make_user()
    kfa = await _loja(db, user, "kfa")
    kia = await _loja(db, user, "kia")
    sem_credencial = await _loja(db, user, "velha")
    falso_kfa = ShopeeFalso()
    falso_kfa.erro_lista = RuntimeError("shopee_order_list error_server: busy")
    falso_kfa.comentarios = [_comentario(1, dias=1)]
    falso_kia = ShopeeFalso()
    falso_kia.pedidos["K1"] = _pedido("K1", comprador=9)
    falso_kia.erro_comentarios = RuntimeError("shopee_comentarios error_permission: sem escopo")

    resumo = await indexar.indexar_pedidos(
        fabrica_cliente=_fabrica(
            {kfa.id: falso_kfa, kia.id: falso_kia, sem_credencial.id: ValueError("sem token")}
        )
    )

    assert resumo == {"lojas": 3, "pedidos": 1, "avaliacoes": 1, "erros": 3}
    assert [x.pedido for x in await _linhas(db, AtendimentoPedidoComprador)] == ["K1"]
    assert [x.comentario_id for x in await _linhas(db, AtendimentoAvaliacaoLoja)] == ["1"]


async def test_loja_arquivada_fica_de_fora(db, make_user, leitura, redis_falso):
    user = await make_user()
    integ = await _loja(db, user)
    integ.archived_at = AGORA
    await db.commit()
    resumo = await indexar.indexar_pedidos(fabrica_cliente=_fabrica({}))
    assert resumo["lojas"] == 0


async def test_loja_com_canal_desligado_fica_de_fora(db, make_user, leitura, redis_falso):
    """SEG-06: `desligado` é o interruptor da equipe quando a Shopee devolve 429 —
    o índice de hora em hora não pode continuar gastando a cota da loja."""
    user = await make_user()
    desligada = await _loja(db, user, "desligada")
    ligada = await _loja(db, user, "ligada")
    db.add_all(
        [
            AtendimentoCanal(
                integration_id=desligada.id, plataforma="shopee", canal="chat", status="desligado"
            ),
            AtendimentoCanal(
                integration_id=ligada.id, plataforma="shopee", canal="chat", status="ok"
            ),
        ]
    )
    await db.commit()
    falso_off, falso_on = ShopeeFalso(), ShopeeFalso()
    falso_on.pedidos["S1"] = _pedido("S1", comprador=1)

    resumo = await indexar.indexar_pedidos(
        fabrica_cliente=_fabrica({desligada.id: falso_off, ligada.id: falso_on})
    )

    assert falso_off.chamadas == []
    assert resumo["lojas"] == 1 and resumo["pedidos"] == 1


async def test_avaliacoes_no_teto_devolvem_o_cursor_para_continuar(
    db, make_user, leitura, redis_falso
):
    """LOGICA-03: no teto de páginas, a leitura diz que NÃO acabou e de onde seguir."""
    user = await make_user()
    integ = await _loja(db, user)
    falso = ShopeeFalso()
    falso.comentarios = [_comentario(1000 - i, dias=i // 10) for i in range(250)]

    primeira = await indexar.indexar_avaliacoes(
        db, integ, falso, max_paginas=2, parar_nas_conhecidas=False
    )
    assert (primeira.paginas, primeira.novas, primeira.acabou) == (2, 100, False)
    assert primeira.cursor == "100"
    resto = await indexar.indexar_avaliacoes(
        db, integ, falso, max_paginas=10, parar_nas_conhecidas=False, cursor=primeira.cursor
    )
    assert (resto.novas, resto.acabou) == (150, True)


# ─────────────── o índice ───────────────


async def test_registrar_do_retrato_e_campos_curtos(db, make_user):
    user = await make_user()
    integ = await _loja(db, user)
    retrato = {
        "fonte": "shopee",
        "pedido": "250928ABC",
        "status": "SHIPPED",
        "criado_em": "2026-09-27T10:00:00+00:00",
        "total": 150.0,
        "itens": [{"titulo": "Mochila", "quantidade": 3}, {"titulo": None}],
    }
    await indice.registrar_do_retrato(
        db, integration_id=integ.id, plataforma="shopee", comprador_id="777", retrato=retrato
    )
    # Sem comprador não há linha (ninguém a acharia).
    await indice.registrar_do_retrato(
        db, integration_id=integ.id, plataforma="shopee", comprador_id=None, retrato=retrato
    )
    await db.commit()
    linhas = await _linhas(db, AtendimentoPedidoComprador)
    assert len(linhas) == 1
    assert (linhas[0].comprador_id, linhas[0].itens_resumo, linhas[0].status) == (
        "777",
        "3× Mochila",
        "SHIPPED",
    )
    assert linhas[0].criado_em == datetime(2026, 9, 27, 10, 0, tzinfo=UTC)


def test_pedido_shopee_sem_comprador_usa_o_da_conversa():
    o = _pedido("S9", comprador=0)
    assert indice.pedido_shopee(o) is None
    linha = indice.pedido_shopee(o, comprador_id="555")
    assert linha["comprador_id"] == "555"
    assert indice.avaliacao_shopee({"comment_id": 1}) is None  # sem estrelas
    grande = indice.avaliacao_shopee({**_comentario(1, 1), "comment": "x" * 900, "rating_star": 9})
    assert len(grande["texto"]) == indice.TEXTO_AVALIACAO_MAX
    assert grande["estrelas"] == 5


async def test_erro_de_banco_no_indice_nao_estraga_a_transacao(db, make_user):
    user = await make_user()
    integ = await _loja(db, user)
    # Integração que não existe: a FK recusa — só o SAVEPOINT desfaz.
    from uuid import uuid4

    await indice.registrar_pedido(
        db,
        integration_id=uuid4(),
        plataforma="shopee",
        comprador_id="1",
        pedido="X",
        criado_em=None,
        total=None,
        status=None,
        itens_resumo=None,
    )
    await indice.registrar_pedido(
        db,
        integration_id=integ.id,
        plataforma="shopee",
        comprador_id="1",
        pedido="Y",
        criado_em=None,
        total=None,
        status=None,
        itens_resumo=None,
    )
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(AtendimentoPedidoComprador)) == 1


# ─────────────── os métodos novos dos clientes (sem rede) ───────────────


async def test_shopee_detalhe_do_indice_pede_so_os_campos_do_indice(monkeypatch):
    cliente = ShopeeClient({"access_token": "t", "shop_id": 111, "partner_id": 1})
    feitas: list[tuple[str, dict]] = []

    async def _call(self, method, path, *, params=None, json=None, what="shopee"):
        feitas.append((path, dict(params or {})))
        return {"order_list": [{"order_sn": "S1"}], "item_comment_list": [], "more": False}

    monkeypatch.setattr(ShopeeClient, "_call", _call)

    await cliente.get_order_detail_indice([f"S{i}" for i in range(60)])
    caminho, params = feitas[-1]
    assert caminho == "/api/v2/order/get_order_detail"
    assert params["response_optional_fields"] == (
        "buyer_user_id,order_status,total_amount,create_time,item_list"
    )
    assert "buyer_username" not in params["response_optional_fields"]
    assert len(params["order_sn_list"].split(",")) == 50

    await cliente.get_comments(cursor="abc", page_size=500, item_id="12")
    caminho, params = feitas[-1]
    assert caminho == "/api/v2/product/get_comment"
    assert params == {"cursor": "abc", "page_size": 100, "item_id": 12}


async def test_shopee_lista_da_janela_deduplica_e_respeita_o_teto(monkeypatch):
    cliente = ShopeeClient({"access_token": "t", "shop_id": 111, "partner_id": 1})
    paginas = {
        "": {
            "order_list": [{"order_sn": "A"}, {"order_sn": "B"}],
            "more": True,
            "next_cursor": "2",
        },
        "2": {"order_list": [{"order_sn": "B"}, {"order_sn": "C"}], "more": False},
    }
    feitas: list[dict] = []

    async def _lista(self, **kw):
        feitas.append(kw)
        return paginas[kw["cursor"]]

    monkeypatch.setattr(ShopeeClient, "get_order_list", _lista)
    assert await cliente.get_order_list_janela(time_from=0, time_to=7200) == (
        ["A", "B", "C"],
        True,
    )
    assert feitas[0]["time_range_field"] == "update_time"
    assert await cliente.get_order_list_janela(time_from=0, time_to=7200, maximo=2) == (
        ["A", "B"],
        False,
    )


async def test_ml_pedidos_do_comprador_e_feedback_404(monkeypatch):
    cliente = MercadoLivreClient({"access_token": "t", "user_id": 999, "expires_at": 9e9})
    feitas: list[tuple[str, dict | None]] = []

    async def _request(self, method, path, *, params=None, json=None):
        feitas.append((path, params))
        req = httpx.Request(method, f"https://api.mercadolibre.com{path}")
        if path.endswith("/feedback") and "404" in path:
            return httpx.Response(404, json={"error": "not_found", "status": 404}, request=req)
        if path.endswith("/feedback"):
            return httpx.Response(200, json={"sale": {"rating": "positive"}}, request=req)
        return httpx.Response(200, json={"results": [], "paging": {"total": 0}}, request=req)

    monkeypatch.setattr(MercadoLivreClient, "_request", _request)

    await cliente.pedidos_do_comprador(123, limit=500)
    caminho, params = feitas[-1]
    assert caminho == "/orders/search"
    assert params == {
        "seller": "999",
        "buyer": "123",
        "sort": "date_desc",
        "limit": 50,
        "offset": 0,
    }
    assert await cliente.feedback_do_pedido("404") is None
    assert await cliente.feedback_do_pedido("55") == {"sale": {"rating": "positive"}}

    sem_conta = MercadoLivreClient({"access_token": "t", "expires_at": 9e9})
    with pytest.raises(ValueError):
        await sem_conta.pedidos_do_comprador(1)
