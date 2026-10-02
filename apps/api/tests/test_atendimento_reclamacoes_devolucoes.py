# ruff: noqa: S105  (tokens de teste de um cliente falso, nada real)
"""Devoluções e disputas de Shopee e TikTok no atendimento (RF2, 01/10/2026).

Sem rede e sem API: a fonte é o que a Logística JÁ gravou — o caso do pedido
na assinatura da linha (`logistica.meli_status.return_status`/`return_type`,
com a data em `status_datas.return_status`) e, para o pedido que passou por
Aguardando Devolução, o id, o tipo, a ação e o prazo em `devolucao_rastreio`.

O que se mede: o caso vira linha em `atendimento_reclamacoes` ligada à
conversa do chat do pedido, com o tipo certo (disputa = Reclamação; pedido de
devolução/reembolso = Devolução) e o prazo; a etiqueta muda e volta quando o
caso acaba; a Shopee ACCEPTED (reembolso já pago) só fica aberta com prazo da
loja pendente; o id entra quando aparece (sem duplicar); o caso novo encerra
o anterior; a história velha fica de fora; um caso com problema não derruba
os outros; a rodada repetida não mexe em nada.

Desde 02/10/2026 também: a aberta sem chat ganha a conversa da devolução
(`canal = 'reclamacao'`, só leitura, uma por pedido, nunca a encerrada); o
motivo do COMPRADOR é lido na plataforma (Shopee `get_return_detail`.reason,
TikTok `returns/{id}/records`.reason_text) — com a Shopee e a TikTok FALSAS
atrás dos clientes de verdade (só a ida HTTP trocada), para provar que só sai
GET e que o texto livre do comprador nunca é guardado —, com cota por rodada
(somando as lojas) e a falha contida por loja; e o status em português pela
tabela. Na revisão: a ação/prazo do rastreio lidos em OUTRO status não viram
pendência; o comprador é lido no máximo 3 vezes (e nunca com o chat); a
conversa da devolução nunca envia (nem reaberta); nada do comprador no log.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoReclamacao,
    DevolucaoRastreio,
    Integration,
    IntegrationPlatform,
    Logistica,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import etiqueta, etiqueta_fatos, reclamacoes
from app.services.atendimento.reclamacoes_devolucoes import (
    CasoLogistica,
    caso_aberto,
    ligar_devolucoes,
    plataforma_da_logistica,
    tipo_do_caso,
)
from app.services.marketplaces.shopee import ShopeeClient
from app.services.marketplaces.tiktok import TikTokClient

T0 = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
SN = "250930ABCDEF12"
TT = "585411441781475242"


async def _logistica(
    db: AsyncSession,
    *,
    plataforma: str = "Shopee",
    pedido: str = SN,
    bling: str = "290580",
    conta: str = "kfa",
    status: str = "REQUESTED",
    tipo: str | None = None,
    mudou: datetime | None = None,
) -> Logistica:
    meli = {"order_status": "COMPLETED", "return_status": status}
    if tipo:
        meli["return_type"] = tipo
    linha = Logistica(
        plataforma=plataforma,
        pedido_marketplace=pedido,
        pedido_bling=bling,
        conta=conta,
        meli_status=meli,
        status_datas={
            "return_status": {
                "em": (mudou or T0 - timedelta(hours=2)).isoformat(),
                "fonte": "plataforma",
            }
        },
    )
    db.add(linha)
    await db.commit()
    return linha


async def _mudar(db: AsyncSession, pedido: str, status: str, mudou: datetime) -> None:
    linha = (
        await db.execute(select(Logistica).where(Logistica.pedido_marketplace == pedido))
    ).scalar_one()
    linha.meli_status = {**linha.meli_status, "return_status": status}
    linha.status_datas = {"return_status": {"em": mudou.isoformat(), "fonte": "plataforma"}}
    await db.commit()


async def _rastreio(
    db: AsyncSession,
    *,
    bling: str = "290580",
    fonte: str = "shopee",
    rid: str | None = "RSN123",
    status: str = "REQUESTED",
    tipo: str | None = "RETURN_AND_REFUND",
    acao: str | None = "SHOPEE_RESPONDER_SOLICITACAO",
    prazo: datetime | None = None,
) -> None:
    existente = await db.get(DevolucaoRastreio, bling)
    dr = existente or DevolucaoRastreio(pedido_bling=bling)
    dr.fonte_auto = fonte
    dr.devolucao_id_auto = rid
    dr.devolucao_status_auto = status
    dr.devolucao_tipo_auto = tipo
    dr.acao_auto = acao
    dr.prazo_acao_auto = prazo
    dr.devolucao_criada_em = T0 - timedelta(days=1)
    if existente is None:
        db.add(dr)
    await db.commit()


async def _chat(
    db: AsyncSession, plataforma: str = "shopee", pedido: str = SN, esperada: str = "pos_venda"
):
    c = AtendimentoConversa(
        plataforma=plataforma,
        canal="chat",
        externo_id=f"chat-{pedido}",
        pedido_marketplace=pedido,
        dados={},
        ultima_mensagem_em=T0 - timedelta(days=2),
    )
    db.add(c)
    await db.commit()
    await etiqueta.recalcular_etiqueta(db, c, motivo="cron", agora=T0 - timedelta(days=1))
    await db.commit()
    assert c.etiqueta == esperada
    return c.id


async def _ligar(db: AsyncSession, agora: datetime = T0, **kw) -> dict:
    resumo = await ligar_devolucoes(db, agora=agora, **kw)
    await db.commit()
    db.expire_all()
    return resumo


async def _linhas(db: AsyncSession, pedido: str = SN) -> list[AtendimentoReclamacao]:
    return list(
        (
            await db.execute(
                select(AtendimentoReclamacao)
                .where(AtendimentoReclamacao.pedido_marketplace == pedido)
                .order_by(AtendimentoReclamacao.created_at)
            )
        )
        .scalars()
        .all()
    )


async def test_devolucao_shopee_liga_a_conversa_com_prazo_e_etiqueta(db, make_user):
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.SHOPEE,
        name="kfa",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.commit()
    integ_id = integ.id
    conversa_id = await _chat(db)
    await _logistica(db)
    prazo = T0 + timedelta(days=2)
    await _rastreio(db, prazo=prazo)

    resumo = await _ligar(db)

    assert (resumo["novas"], resumo["ligadas"], resumo["erros"]) == (1, 1, 0)
    [linha] = await _linhas(db)
    assert (linha.plataforma, linha.externo_id, linha.tipo) == ("shopee", "RSN123", "devolucao")
    assert linha.status == "REQUESTED" and linha.encerrada_em is None
    assert linha.prazo_em == prazo
    assert linha.conversa_id == conversa_id
    assert linha.integration_id == integ_id
    # Sem leitura na plataforma (sem fábrica de cliente): o motivo do
    # comprador fica vazio; o tipo de solução vai para `dados.solucao`.
    assert linha.motivo is None
    assert linha.dados["solucao"] == "Devolução + reembolso"
    assert linha.aberta_em == T0 - timedelta(days=1)
    assert linha.dados["fonte"] == "logistica" and linha.dados["pedido_bling"] == "290580"
    assert linha.dados["acao_texto"] == "Responder à solicitação de devolução na Shopee"
    conversa = await db.get(AtendimentoConversa, conversa_id)
    assert conversa.etiqueta == "devolucao"
    [h] = await etiqueta.historico_da_conversa(db, conversa_id)
    assert (h.de, h.para) == ("pos_venda", "devolucao")
    assert h.motivo == "Devolução RSN123 aberta no Shopee (devolução lida pela Logística)"

    tela = reclamacoes.para_tela(linha)
    assert tela["numero"] == "RSN123" and tela["tipo_rotulo"] == "Devolução"
    assert tela["status_rotulo"] == "Pedido de devolução aberto"
    assert (tela["motivo"], tela["solucao"]) == (None, "Devolução + reembolso")
    # A devolução tem return_sn: a lista de devoluções buscando por ele (sem
    # o return_id interno, que só os cartões rr do chat trazem).
    assert tela["url_plataforma"] == (
        "https://seller.shopee.com.br/portal/sale/returnrefundcancel"
        "?keyword=RSN123&keywordType=return_sn"
    )

    # Rodada repetida sem novidade: nada muda, nada é recalculado.
    resumo = await _ligar(db, T0 + timedelta(minutes=10))
    assert (resumo["novas"], resumo["atualizadas"], resumo["etiquetas"]) == (0, 0, 0)
    assert len(await _linhas(db)) == 1


async def test_disputa_e_reclamacao_e_volta_quando_acaba(db):
    """TikTok: a loja recusou o pacote e a TikTok analisa (disputa) → cancelado."""
    conversa_id = await _chat(db, "tiktok", TT)
    await _logistica(
        db,
        plataforma="TikTok",
        pedido=TT,
        bling="288403",
        status="REJECT_RECEIVE_PACKAGE",
        tipo="RETURN_AND_REFUND",
    )
    await _ligar(db)
    [linha] = await _linhas(db, TT)
    # Sem o id da TikTok na Logística: a linha espera por ele ("pedido <nº>").
    assert linha.externo_id == f"pedido {TT}"
    assert linha.tipo == "reclamacao"
    assert reclamacoes.para_tela(linha)["tipo_rotulo"] == "Disputa"
    assert reclamacoes.para_tela(linha)["numero"] is None
    conversa = await db.get(AtendimentoConversa, conversa_id)
    assert conversa.etiqueta == "reclamacao"

    await _mudar(db, TT, "RETURN_OR_REFUND_REQUEST_CANCEL", T0 + timedelta(hours=1))
    await _ligar(db, T0 + timedelta(hours=1, minutes=5))
    [linha] = await _linhas(db, TT)
    assert linha.encerrada_em == T0 + timedelta(hours=1) and linha.prazo_em is None
    assert reclamacoes.para_tela(linha)["status_rotulo"] == "Cancelada pelo comprador"
    conversa = await db.get(AtendimentoConversa, conversa_id)
    assert conversa.etiqueta == "pos_venda"
    hist = await etiqueta.historico_da_conversa(db, conversa_id)
    assert [(h.de, h.para) for h in hist] == [
        ("pos_venda", "reclamacao"),
        ("reclamacao", "pos_venda"),
    ]
    assert hist[-1].motivo.startswith("Reclamação encerrada")


async def test_disputa_da_shopee_e_reclamacao(db):
    conversa_id = await _chat(db)
    await _logistica(db, status="JUDGING")
    await _ligar(db)
    [linha] = await _linhas(db)
    assert linha.tipo == "reclamacao"
    assert (await db.get(AtendimentoConversa, conversa_id)).etiqueta == "reclamacao"


async def test_o_id_entra_quando_aparece_sem_duplicar(db):
    await _chat(db)
    await _logistica(db)
    await _ligar(db)
    [linha] = await _linhas(db)
    assert linha.externo_id == f"pedido {SN}"
    primeira = linha.id

    await _rastreio(db, prazo=T0 + timedelta(days=2))
    await _ligar(db, T0 + timedelta(minutes=10))
    [linha] = await _linhas(db)
    assert (linha.id, linha.externo_id) == (primeira, "RSN123")
    assert linha.prazo_em == T0 + timedelta(days=2)

    # O rastreio some (a linha do pedido foi limpa): a linha NÃO perde o id.
    await db.delete(await db.get(DevolucaoRastreio, "290580"))
    await db.commit()
    await _ligar(db, T0 + timedelta(minutes=20))
    [linha] = await _linhas(db)
    assert linha.externo_id == "RSN123"


async def test_caso_novo_do_pedido_encerra_o_anterior(db):
    conversa_id = await _chat(db)
    await _logistica(db)
    await _rastreio(db, rid="RSN-1", prazo=T0 + timedelta(days=1))
    await _ligar(db)
    await _rastreio(db, rid="RSN-2", prazo=T0 + timedelta(days=3))
    await _ligar(db, T0 + timedelta(minutes=10))
    antiga, nova = await _linhas(db)
    assert (antiga.externo_id, nova.externo_id) == ("RSN-1", "RSN-2")
    assert antiga.encerrada_em == T0 + timedelta(minutes=10) and antiga.prazo_em is None
    assert antiga.dados["substituida_por"] == "RSN-2"
    assert nova.encerrada_em is None
    assert (await db.get(AtendimentoConversa, conversa_id)).etiqueta == "devolucao"


async def test_shopee_aceita_so_fica_aberta_com_prazo_da_loja(db):
    conversa_id = await _chat(db)
    # ACCEPTED = reembolso já pago; sem nada pendente da loja → encerrada.
    await _logistica(db, status="ACCEPTED", mudou=T0 - timedelta(days=1))
    await _ligar(db)
    [linha] = await _linhas(db)
    assert linha.encerrada_em == T0 - timedelta(days=1)
    assert (await db.get(AtendimentoConversa, conversa_id)).etiqueta == "pos_venda"

    # O pacote voltou e a Shopee pede para conferir até amanhã: aberta.
    await _rastreio(
        db, status="ACCEPTED", acao="SHOPEE_CONFERIR_PACOTE", prazo=T0 + timedelta(days=1)
    )
    await _ligar(db, T0 + timedelta(minutes=10))
    [linha] = await _linhas(db)
    assert linha.encerrada_em is None and linha.prazo_em == T0 + timedelta(days=1)
    assert (await db.get(AtendimentoConversa, conversa_id)).etiqueta == "devolucao"


async def test_historia_velha_fica_de_fora(db):
    await _logistica(db, status="CANCELLED", mudou=T0 - timedelta(days=40))
    await _logistica(
        db, pedido="2509VIVO", bling="290581", status="CANCELLED", mudou=T0 - timedelta(days=2)
    )
    resumo = await _ligar(db)
    assert (resumo["historia"], resumo["novas"]) == (1, 1)
    assert await _linhas(db) == []
    [recente] = await _linhas(db, "2509VIVO")
    assert recente.encerrada_em is not None


async def test_caso_sem_conversa_liga_depois(db):
    await _logistica(db)
    await _ligar(db)
    [linha] = await _linhas(db)
    assert linha.conversa_id is None
    # A etiqueta já vê a devolução pelo pedido, antes mesmo da ligação.
    conversa_id = await _chat(db, esperada="devolucao")
    # A próxima rodada liga a conversa.
    await _ligar(db, T0 + timedelta(minutes=10))
    [linha] = await _linhas(db)
    assert linha.conversa_id == conversa_id


async def test_um_caso_com_problema_nao_derruba_os_outros(db, monkeypatch):
    await _logistica(db)
    await _logistica(
        db, plataforma="TikTok", pedido=TT, bling="288403", status="BUYER_SHIPPED_ITEM"
    )
    original = etiqueta_fatos.conversas_do_pedido

    async def _quebra(session, plataforma, pedido):
        if pedido == SN:
            raise RuntimeError("quebrou")
        return await original(session, plataforma, pedido)

    monkeypatch.setattr(etiqueta_fatos, "conversas_do_pedido", _quebra)
    resumo = await _ligar(db)
    assert resumo["erros"] == 1 and resumo["novas"] == 1
    assert await _linhas(db) == []
    assert len(await _linhas(db, TT)) == 1


async def test_commit_a_cada(db):
    for i in range(3):
        await _logistica(db, pedido=f"P{i}", bling=f"9{i}")
    resumo = await ligar_devolucoes(db, agora=T0, commit_a_cada=2)
    await db.rollback()  # o último (o 3º) não foi commitado
    assert resumo["novas"] == 3
    assert await db.scalar(select(func.count()).select_from(AtendimentoReclamacao)) == 2


@pytest.mark.parametrize(
    ("rotulo", "plataforma"),
    [
        ("Shopee", "shopee"),
        (" shopee ", "shopee"),
        ("TikTok Shop", "tiktok"),
        ("tik tok", "tiktok"),
        ("Mercado Livre", None),
        (None, None),
    ],
)
def test_plataforma_da_logistica(rotulo, plataforma):
    assert plataforma_da_logistica(rotulo) == plataforma


@pytest.mark.parametrize(
    ("plataforma", "status", "prazo", "tipo", "aberto"),
    [
        ("shopee", "REQUESTED", None, "devolucao", True),
        ("shopee", "PROCESSING", None, "devolucao", True),
        ("shopee", "JUDGING", None, "reclamacao", True),
        ("shopee", "SELLER_DISPUTE", None, "reclamacao", True),
        ("shopee", "ACCEPTED", None, "devolucao", False),
        ("shopee", "ACCEPTED", T0 + timedelta(hours=1), "devolucao", True),
        ("shopee", "ACCEPTED", T0 - timedelta(hours=1), "devolucao", False),
        ("shopee", "CANCELLED", None, "devolucao", False),
        ("shopee", "CLOSED", None, "devolucao", False),
        ("tiktok", "RETURN_OR_REFUND_REQUEST_PENDING", None, "devolucao", True),
        ("tiktok", "AWAITING_BUYER_SHIP", None, "devolucao", True),
        ("tiktok", "REJECT_RECEIVE_PACKAGE", None, "reclamacao", True),
        ("tiktok", "RETURN_OR_REFUND_REQUEST_CANCEL", None, "devolucao", False),
        ("tiktok", "RETURN_OR_REFUND_REQUEST_COMPLETE", None, "devolucao", False),
        ("tiktok", "RETURN_OR_REFUND_REQUEST_SUCCESS", None, "devolucao", False),
    ],
)
def test_tipo_e_aberto(plataforma, status, prazo, tipo, aberto):
    caso = CasoLogistica(plataforma=plataforma, pedido_marketplace="x", status=status, prazo=prazo)
    assert tipo_do_caso(caso) == tipo
    assert caso_aberto(caso, T0) is aberto


# ─────────────── a Shopee e a TikTok falsas (02/10/2026) ───────────────
# Os clientes de VERDADE (`ShopeeClient`, `TikTokClient`) com a ida HTTP
# trocada: o que sai é registrado e só GET responde. O formato é o medido em
# produção em 02/10/2026 (sonda só GET): o detalhe da Shopee com `reason`,
# `text_reason` (texto livre do comprador) e `return_solution`; a linha do
# tempo da TikTok com `role`/`event`/`reason_text`/`note`.

TEXTO_DO_COMPRADOR = "chegou quebrado, quero meu dinheiro de volta"


class _Plataforma:
    """O que a Shopee/TikTok falsa responde e o que ela recebeu."""

    def __init__(self) -> None:
        self.chamadas: list[tuple[str, str]] = []
        self.detalhe: dict = {
            "return_sn": "RSN123",
            "status": "REQUESTED",
            "reason": "FUNCTIONAL_DMG",
            "text_reason": TEXTO_DO_COMPRADOR,
            "return_solution": 0,
            "user": {"username": "c***r", "email": "", "portrait": ""},
        }
        self.comprador: dict = {"buyer_user_id": 998877, "buyer_username": "comprador_kfa"}
        self.registros: list[dict] = [
            {
                "role": "BUYER",
                "event": "ORDER_RETURN",
                "reason_text": "Item com defeito",
                "note": TEXTO_DO_COMPRADOR,
                "create_time": 1759300000,
            },
            {
                "role": "SELLER",
                "event": "SELLER_AGGREE_RETURN",
                "reason_text": "",
                "note": "",
                "create_time": 1759310000,
            },
        ]
        self.falhar = False
        # A Shopee responde 200 com `error` e o texto do comprador na
        # `message` (o pior caso para o log).
        self.erro_no_corpo = False
        # O comprador do pedido na TikTok (None = o pedido volta sem ele).
        self.user_id_tiktok: str | None = "7000"
        # Os pedidos lidos (o nº), na ordem.
        self.pedidos_lidos: list[str] = []

    def get(self, path: str) -> None:
        self.chamadas.append(("GET", path))
        if self.falhar:
            raise httpx.ConnectError("caiu")


def _shopee_falsa(plataforma: _Plataforma) -> ShopeeClient:
    cliente = ShopeeClient({"access_token": "t", "shop_id": 1, "expires_at": 4102444800})

    async def _request(method, path, *, params=None, json=None):
        if method != "GET":
            raise AssertionError(f"só GET: {method} {path}")
        plataforma.get(path)
        if plataforma.erro_no_corpo:
            return httpx.Response(
                200,
                json={"error": "error_server", "message": TEXTO_DO_COMPRADOR, "response": {}},
            )
        if path == "/api/v2/returns/get_return_detail":
            corpo = plataforma.detalhe
        elif path == "/api/v2/order/get_order_detail":
            plataforma.pedidos_lidos.append(params["order_sn_list"])
            corpo = {"order_list": [{"order_sn": params["order_sn_list"], **plataforma.comprador}]}
        else:
            raise AssertionError(f"chamada inesperada: {path}")
        return httpx.Response(200, json={"error": "", "message": "", "response": corpo})

    cliente._request = _request
    return cliente


def _tiktok_falsa(plataforma: _Plataforma) -> TikTokClient:
    cliente = TikTokClient({"access_token": "t", "shop_cipher": "c"})

    async def _get(path, extra_params=None, **_):
        plataforma.get(path)
        if path.endswith("/records"):
            return {"code": 0, "data": {"records": plataforma.registros}}
        if path == "/order/202309/orders":
            plataforma.pedidos_lidos.append(extra_params["ids"])
            pedido = {"id": extra_params["ids"]}
            if plataforma.user_id_tiktok:
                pedido["user_id"] = plataforma.user_id_tiktok
            return {"code": 0, "data": {"orders": [pedido]}}
        raise AssertionError(f"chamada inesperada: {path}")

    async def _so_get(*a, **k):
        raise AssertionError("só GET")

    cliente._get = _get
    cliente._post = _so_get
    cliente._put = _so_get
    return cliente


def _fabrica(plataforma: _Plataforma, quebradas: set[str] = frozenset()):
    async def fabrica(integ: Integration):
        if integ.name in quebradas:
            raise RuntimeError("token_nao_renovou")
        if integ.platform == IntegrationPlatform.SHOPEE:
            return _shopee_falsa(plataforma)
        return _tiktok_falsa(plataforma)

    return fabrica


async def _loja(db: AsyncSession, make_user, nome: str = "kfa", plataforma=None) -> Integration:
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=plataforma or IntegrationPlatform.SHOPEE,
        name=nome,
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.commit()
    return integ


async def _conversas(db: AsyncSession, pedido: str = SN, canal: str | None = None):
    consulta = select(AtendimentoConversa).where(AtendimentoConversa.pedido_marketplace == pedido)
    if canal:
        consulta = consulta.where(AtendimentoConversa.canal == canal)
    return list((await db.execute(consulta.order_by(AtendimentoConversa.created_at))).scalars())


async def _mensagens(db: AsyncSession, conversa_id) -> list[AtendimentoMensagem]:
    return list(
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa_id)
                .order_by(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
            )
        ).scalars()
    )


async def test_aberta_sem_chat_ganha_a_conversa_da_devolucao_com_o_motivo(db, make_user):
    """Shopee: 26 das 75 abertas não tinham conversa (02/10/2026)."""
    integ_id = (await _loja(db, make_user)).id
    await _logistica(db)
    prazo = T0 + timedelta(days=2)
    await _rastreio(db, prazo=prazo)
    falsa = _Plataforma()

    resumo = await _ligar(db, fabrica_cliente=_fabrica(falsa))

    assert (resumo["novas"], resumo["conversas_novas"], resumo["erros"]) == (1, 1, 0)
    assert (resumo["leituras"], resumo["motivos_lidos"], resumo["erros_leitura"]) == (2, 1, 0)
    assert falsa.chamadas == [
        ("GET", "/api/v2/returns/get_return_detail"),
        ("GET", "/api/v2/order/get_order_detail"),
    ]
    [linha] = await _linhas(db)
    # O motivo do COMPRADOR, traduzido; o código e o tipo de solução em `dados`.
    assert linha.motivo == "Produto com defeito (não funciona)"
    assert linha.dados["motivo_codigo"] == "FUNCTIONAL_DMG"
    assert linha.dados["solucao"] == "Devolução + reembolso"
    assert linha.dados["motivo_lido_status"] == "REQUESTED"
    # O texto livre do comprador nunca é guardado.
    assert TEXTO_DO_COMPRADOR not in str(linha.dados)

    [conversa] = await _conversas(db)
    conversa_id = conversa.id
    assert conversa.canal == "reclamacao" and conversa.plataforma == "shopee"
    assert conversa.integration_id == integ_id
    assert (conversa.comprador_id, conversa.comprador_nome) == ("998877", "comprador_kfa")
    assert conversa.situacao == "bloqueada"
    assert conversa.bloqueio_motivo == (
        "Devolução da Shopee: só leitura no DaVinci — as respostas e as ações ficam na Shopee."
    )
    assert conversa.etiqueta == "devolucao"
    # A vez e o prazo são da Shopee (a ação da loja com prazo).
    assert conversa.aguardando_resposta is True
    assert conversa.prazo_resposta_em == prazo
    assert linha.conversa_id == conversa.id
    textos = [m.texto for m in await _mensagens(db, conversa.id)]
    assert textos == [
        "Devolução aberta na Shopee: Produto com defeito (não funciona). "
        "Pede: Devolução + reembolso.",
        "Devolução na Shopee: Pedido de devolução aberto.",
    ]
    assert all(TEXTO_DO_COMPRADOR not in t for t in textos)
    # O cartão aparece na conversa da devolução.
    [cartao] = await reclamacoes.reclamacoes_da_conversa(db, conversa)
    tela = reclamacoes.para_tela(cartao)
    assert tela["motivo"] == "Produto com defeito (não funciona)"
    assert tela["solucao"] == "Devolução + reembolso"
    assert tela["status_rotulo"] == "Pedido de devolução aberto"

    # Rodada repetida: nenhuma chamada, nenhuma conversa nem mensagem nova.
    resumo = await _ligar(db, T0 + timedelta(minutes=10), fabrica_cliente=_fabrica(falsa))
    assert (resumo["conversas_novas"], resumo["leituras"], resumo["atualizadas"]) == (0, 0, 0)
    assert len(falsa.chamadas) == 2
    assert len(await _conversas(db)) == 1
    assert len(await _mensagens(db, conversa_id)) == 2

    # O comprador abriu o chat e ele foi ligado ao pedido: o cartão aparece
    # TAMBÉM no chat; a conversa da devolução continua (uma só).
    chat_id = await _chat(db, esperada="devolucao")
    await _ligar(db, T0 + timedelta(minutes=20), fabrica_cliente=_fabrica(falsa))
    [linha] = await _linhas(db)
    assert linha.conversa_id == chat_id
    chat = await db.get(AtendimentoConversa, chat_id)
    assert [r.id for r in await reclamacoes.reclamacoes_da_conversa(db, chat)] == [linha.id]
    assert len(await _conversas(db, canal="reclamacao")) == 1

    # Mudou de status (a Shopee aceitou, o pacote volta): relê o motivo uma
    # vez e a história ganha a linha do status novo. A releitura que volta
    # sem motivo não apaga o que já se sabia.
    falsa.detalhe = {**falsa.detalhe, "reason": ""}
    await _mudar(db, SN, "PROCESSING", T0 + timedelta(hours=1))
    resumo = await _ligar(db, T0 + timedelta(hours=1, minutes=5), fabrica_cliente=_fabrica(falsa))
    assert (resumo["leituras"], resumo["conversas_novas"]) == (1, 0)
    textos = [m.texto for m in await _mensagens(db, conversa_id)]
    assert textos[-1] == "Devolução na Shopee: Em andamento."
    [linha] = await _linhas(db)
    assert linha.motivo == "Produto com defeito (não funciona)"
    assert linha.dados["motivo_lido_status"] == "PROCESSING"

    # Encerrou: a linha do encerramento, a conversa sai da fila e da etiqueta.
    await _mudar(db, SN, "CANCELLED", T0 + timedelta(hours=2))
    resumo = await _ligar(db, T0 + timedelta(hours=2, minutes=5), fabrica_cliente=_fabrica(falsa))
    assert resumo["leituras"] == 0  # encerrada não lê nada
    conversa = await db.get(AtendimentoConversa, conversa_id)
    assert conversa.etiqueta == "pos_venda" and conversa.aguardando_resposta is False
    textos = [m.texto for m in await _mensagens(db, conversa_id)]
    assert textos[-1] == "Devolução encerrada na Shopee — Cancelada."
    assert len(await _conversas(db, canal="reclamacao")) == 1


async def test_encerrada_sem_chat_nao_ganha_conversa(db, make_user):
    await _loja(db, make_user)
    await _logistica(db, status="CANCELLED", mudou=T0 - timedelta(days=2))
    falsa = _Plataforma()
    resumo = await _ligar(db, fabrica_cliente=_fabrica(falsa))
    assert (resumo["novas"], resumo["conversas_novas"], resumo["leituras"]) == (1, 0, 0)
    [linha] = await _linhas(db)
    assert linha.encerrada_em is not None and linha.conversa_id is None
    assert await _conversas(db) == []
    assert falsa.chamadas == []


async def test_caso_novo_do_pedido_entra_na_mesma_conversa(db, make_user):
    """Uma conversa da devolução por pedido: o id que aparece depois e o caso
    novo do mesmo pedido não duplicam a conversa."""
    await _loja(db, make_user)
    await _logistica(db)
    falsa = _Plataforma()
    await _ligar(db, fabrica_cliente=_fabrica(falsa))  # sem id ainda ("pedido <nº>")
    [conversa] = await _conversas(db)
    conversa_id = conversa.id
    assert conversa.externo_id == f"pedido {SN}"
    await _rastreio(db, rid="RSN-1", prazo=T0 + timedelta(days=1))
    await _ligar(db, T0 + timedelta(minutes=10), fabrica_cliente=_fabrica(falsa))
    await _rastreio(db, rid="RSN-2", prazo=T0 + timedelta(days=3))
    await _ligar(db, T0 + timedelta(minutes=20), fabrica_cliente=_fabrica(falsa))
    antiga, nova = await _linhas(db)
    assert (antiga.externo_id, nova.externo_id) == ("RSN-1", "RSN-2")
    assert [c.id for c in await _conversas(db)] == [conversa_id]
    assert nova.conversa_id == conversa_id
    aberturas = [
        m.externo_id
        for m in await _mensagens(db, conversa_id)
        if m.externo_id.startswith("sistema:abertura:")
    ]
    assert sorted(aberturas) == sorted(
        [f"sistema:abertura:{antiga.id}", f"sistema:abertura:{nova.id}"]
    )


async def test_tiktok_motivo_pela_linha_do_tempo(db, make_user):
    integ_id = (await _loja(db, make_user, plataforma=IntegrationPlatform.TIKTOK)).id
    await _logistica(
        db,
        plataforma="TikTok",
        pedido=TT,
        bling="288403",
        status="AWAITING_BUYER_SHIP",
        tipo="RETURN_AND_REFUND",
    )
    await _rastreio(
        db, bling="288403", fonte="tiktok", rid="RT-77", status="AWAITING_BUYER_SHIP", acao=None
    )
    falsa = _Plataforma()

    resumo = await _ligar(db, fabrica_cliente=_fabrica(falsa))

    assert resumo["conversas_novas"] == 1 and resumo["motivos_lidos"] == 1
    assert falsa.chamadas == [
        ("GET", "/return_refund/202309/returns/RT-77/records"),
        ("GET", "/order/202309/orders"),
    ]
    [linha] = await _linhas(db, TT)
    assert linha.motivo == "Item com defeito"
    assert TEXTO_DO_COMPRADOR not in str(linha.dados)
    [conversa] = await _conversas(db, TT)
    conversa_id = conversa.id
    assert conversa.integration_id == integ_id and conversa.comprador_id == "7000"
    assert conversa.bloqueio_motivo.startswith("Devolução do TikTok: só leitura")
    # Sem ação da loja pendente: na caixa, mas fora do "Falta responder".
    assert conversa.aguardando_resposta is False
    textos = [m.texto for m in await _mensagens(db, conversa_id)]
    assert textos == [
        "Devolução aberta no TikTok: Item com defeito. Pede: Devolução + reembolso.",
        "Devolução no TikTok: Esperando o comprador enviar.",
    ]
    tela = reclamacoes.para_tela(linha)
    assert tela["status_rotulo"] == "Esperando o comprador enviar"


async def test_cota_por_rodada_e_erro_contido_por_loja(db, make_user, monkeypatch):
    from app.services.atendimento import reclamacoes_devolucoes as rd

    await _loja(db, make_user, "kfa")
    await _loja(db, make_user, "quebrada")
    for i in range(3):
        await _logistica(db, pedido=f"SN{i}", bling=f"80{i}")
        await _rastreio(db, bling=f"80{i}", rid=f"R{i}", prazo=T0 + timedelta(days=i + 1))
    await _logistica(db, pedido="SNQ", bling="809", conta="quebrada")
    await _rastreio(db, bling="809", rid="RQ")
    # Os chats existem: só o motivo é lido (uma chamada por caso).
    for pedido in ("SN0", "SN1", "SN2", "SNQ"):
        await _chat(db, pedido=pedido)
    monkeypatch.setattr(rd, "MAX_LEITURAS_RODADA", 2)
    falsa = _Plataforma()
    fabrica = _fabrica(falsa, quebradas={"quebrada"})

    resumo = await _ligar(db, fabrica_cliente=fabrica)
    # Prazo mais curto primeiro; a loja quebrada não para a outra.
    assert (resumo["novas"], resumo["erros"]) == (4, 0)
    assert (resumo["leituras"], resumo["motivos_lidos"]) == (2, 2)
    assert resumo["leituras_adiadas"] == 2
    lidos = {
        linha.pedido_marketplace
        for p in ("SN0", "SN1", "SN2", "SNQ")
        for linha in await _linhas(db, p)
        if linha.motivo
    }
    assert lidos == {"SN0", "SN1"}

    # A rodada seguinte lê o que ficou (a loja quebrada continua fora).
    resumo = await _ligar(db, T0 + timedelta(minutes=10), fabrica_cliente=fabrica)
    assert (resumo["leituras"], resumo["motivos_lidos"]) == (1, 1)
    [sn2] = await _linhas(db, "SN2")
    assert sn2.motivo == "Produto com defeito (não funciona)"
    [snq] = await _linhas(db, "SNQ")
    assert snq.motivo is None


async def test_leitura_que_falhou_espera_para_tentar_de_novo(db, make_user):
    await _loja(db, make_user)
    await _chat(db)
    await _logistica(db)
    await _rastreio(db)
    falsa = _Plataforma()
    falsa.falhar = True
    resumo = await _ligar(db, fabrica_cliente=_fabrica(falsa))
    assert (resumo["erros_leitura"], resumo["erros"], resumo["novas"]) == (1, 0, 1)
    [linha] = await _linhas(db)
    assert linha.dados["motivo_falhou"]["status"] == "REQUESTED"
    # Antes de `RETENTAR_FALHA`: não chama de novo.
    resumo = await _ligar(db, T0 + timedelta(minutes=10), fabrica_cliente=_fabrica(falsa))
    assert resumo["leituras"] == 0
    # Depois: tenta, e dessa vez a Shopee responde.
    falsa.falhar = False
    resumo = await _ligar(db, T0 + timedelta(hours=1, minutes=5), fabrica_cliente=_fabrica(falsa))
    assert resumo["motivos_lidos"] == 1
    [linha] = await _linhas(db)
    assert linha.motivo == "Produto com defeito (não funciona)"
    assert linha.dados["motivo_falhou"] is None


async def test_cota_acabou_a_conversa_espera_e_a_falha_nao_segura(db, make_user, monkeypatch):
    """A cota acabou antes do caso: a conversa nasce na rodada seguinte, já
    com o motivo. A plataforma falhou: a conversa nasce assim mesmo (sem
    motivo nem comprador), e o motivo e o comprador entram quando são lidos."""
    from app.services.atendimento import reclamacoes_devolucoes as rd

    await _loja(db, make_user)
    await _logistica(db)
    await _rastreio(db)
    falsa = _Plataforma()

    # 1) Sem cota: a linha entra, a conversa espera (sem chamada nenhuma).
    monkeypatch.setattr(rd, "MAX_LEITURAS_RODADA", 0)
    resumo = await _ligar(db, fabrica_cliente=_fabrica(falsa))
    assert (resumo["novas"], resumo["conversas_novas"], resumo["leituras_adiadas"]) == (1, 0, 1)
    assert await _conversas(db) == [] and falsa.chamadas == []
    monkeypatch.setattr(rd, "MAX_LEITURAS_RODADA", 30)

    # 2) A Shopee falha: a conversa nasce sem o motivo e sem o comprador.
    falsa.falhar = True
    resumo = await _ligar(db, T0 + timedelta(minutes=10), fabrica_cliente=_fabrica(falsa))
    assert (resumo["conversas_novas"], resumo["erros_leitura"], resumo["erros"]) == (1, 2, 0)
    [conversa] = await _conversas(db)
    conversa_id = conversa.id
    assert conversa.comprador_id is None
    textos = [m.texto for m in await _mensagens(db, conversa_id)]
    assert textos[0] == "Devolução aberta na Shopee. Pede: Devolução + reembolso."

    # 3) Antes de `RETENTAR_FALHA`, nada; depois, o motivo vira linha da
    # conversa e o comprador entra na conversa que nasceu sem ele.
    falsa.falhar = False
    resumo = await _ligar(db, T0 + timedelta(minutes=20), fabrica_cliente=_fabrica(falsa))
    assert resumo["leituras"] == 0
    resumo = await _ligar(db, T0 + timedelta(hours=1, minutes=15), fabrica_cliente=_fabrica(falsa))
    assert (resumo["leituras"], resumo["motivos_lidos"]) == (2, 1)
    textos = [m.texto for m in await _mensagens(db, conversa_id)]
    assert "Motivo do comprador na Shopee: Produto com defeito (não funciona)." in textos
    assert len(textos) == 3
    conversa = await db.get(AtendimentoConversa, conversa_id)
    assert (conversa.comprador_id, conversa.comprador_nome) == ("998877", "comprador_kfa")
    # E não pergunta de novo.
    resumo = await _ligar(db, T0 + timedelta(hours=3), fabrica_cliente=_fabrica(falsa))
    assert resumo["leituras"] == 0


async def test_linha_antiga_com_o_tipo_no_motivo(db):
    """Até 02/10/2026 o `motivo` guardava o tipo de solução: a tela o mostra
    como "Pede", e a rodada seguinte o tira do motivo."""
    await _logistica(db, tipo="REFUND")
    await _ligar(db)
    [linha] = await _linhas(db)
    linha.motivo = "Só reembolso (produto fica com o cliente)"
    linha.dados = {k: v for k, v in linha.dados.items() if k != "solucao"}
    await db.commit()
    tela = reclamacoes.para_tela(linha)
    assert (tela["motivo"], tela["solucao"]) == (None, "Só reembolso (produto fica com o cliente)")
    await _ligar(db, T0 + timedelta(minutes=10))
    [linha] = await _linhas(db)
    assert linha.motivo is None
    assert linha.dados["solucao"] == "Só reembolso (produto fica com o cliente)"


async def test_acao_velha_do_rastreio_nao_vira_pendencia(db, make_user):
    """A ação e o prazo do rastreio foram lidos para o status de ENTÃO. A
    Logística andou (a loja contestou: SELLER_DISPUTE) e o rastreio ficou no
    PROCESSING com o "Conferir o pacote" vencido — 7 abertas assim em
    produção, 02/10/2026. Não é pendência: sem ação, sem prazo, fora do
    "Falta responder". Relido no status de agora, a ação nova vale."""
    await _loja(db, make_user)
    await _logistica(db, status="SELLER_DISPUTE")
    await _rastreio(
        db, status="PROCESSING", acao="SHOPEE_CONFERIR_PACOTE", prazo=T0 - timedelta(days=1)
    )
    falsa = _Plataforma()
    await _ligar(db, fabrica_cliente=_fabrica(falsa))
    [linha] = await _linhas(db)
    assert linha.encerrada_em is None and linha.tipo == "reclamacao"
    assert linha.externo_id == "RSN123"  # o id do caso continua valendo
    assert linha.prazo_em is None
    assert (linha.dados["acao_pendente"], linha.dados["acao_texto"]) == (None, None)
    tela = reclamacoes.para_tela(linha)
    assert (tela["acao_pendente"], tela["prazo_em"]) == (None, None)
    [conversa] = await _conversas(db)
    conversa_id = conversa.id
    assert conversa.aguardando_resposta is False and conversa.prazo_resposta_em is None

    prazo = T0 + timedelta(days=2)
    await _rastreio(db, status="SELLER_DISPUTE", acao="SHOPEE_ENVIAR_EVIDENCIAS", prazo=prazo)
    await _ligar(db, T0 + timedelta(minutes=10), fabrica_cliente=_fabrica(falsa))
    [linha] = await _linhas(db)
    assert linha.prazo_em == prazo
    assert linha.dados["acao_texto"] == "Enviar as evidências pedidas pela Shopee"
    conversa = await db.get(AtendimentoConversa, conversa_id)
    assert conversa.aguardando_resposta is True and conversa.prazo_resposta_em == prazo


async def test_comprador_lido_no_maximo_tres_vezes_e_nunca_com_o_chat(db, make_user):
    """A TikTok devolve o pedido sem o comprador: a conversa nasce sem ele e
    a leitura é tentada de novo a cada `RETENTAR_FALHA`, no máximo
    `MAX_TENTATIVAS_COMPRADOR` vezes. Com o chat do pedido ligado, o
    comprador já está nele: não pergunta mais."""
    await _loja(db, make_user, plataforma=IntegrationPlatform.TIKTOK)
    for pedido, bling in ((TT, "288403"), ("TT2", "288404")):
        await _logistica(
            db,
            plataforma="TikTok",
            pedido=pedido,
            bling=bling,
            status="AWAITING_BUYER_SHIP",
            tipo="RETURN_AND_REFUND",
        )
    falsa = _Plataforma()
    falsa.user_id_tiktok = None
    await _ligar(db, fabrica_cliente=_fabrica(falsa))
    assert sorted(falsa.pedidos_lidos) == sorted([TT, "TT2"])
    [conversa] = await _conversas(db, TT, "reclamacao")
    assert conversa.comprador_id is None
    # O chat do TT2 aparece (ligado ao pedido): o comprador está nele.
    await _chat(db, "tiktok", "TT2", esperada="devolucao")

    passo = reclamacoes.RETENTAR_FALHA + timedelta(minutes=1)
    for i in range(1, 5):
        await _ligar(db, T0 + passo * i, fabrica_cliente=_fabrica(falsa))
    assert falsa.pedidos_lidos.count(TT) == 3
    assert falsa.pedidos_lidos.count("TT2") == 1
    [linha] = await _linhas(db, TT)
    assert linha.dados["comprador_tentativas"] == 3


async def test_conversa_da_devolucao_nunca_envia(db, make_user, monkeypatch):
    """Só leitura: nem com o envio ligado e o chat da loja em automático (a
    conversa da devolução não tem canal de envio). Reaberta pela pessoa
    (fechar → reabrir = "aberta"), continua sem envio, e a rodada seguinte a
    bloqueia de novo — e ela volta para a fila pelo prazo da plataforma."""
    from app.config import get_settings
    from app.models import AtendimentoCanal
    from app.services.atendimento import enviar
    from app.services.atendimento.constantes import ORIGEM_IA

    integ = await _loja(db, make_user)
    db.add(
        AtendimentoCanal(
            integration_id=integ.id, plataforma="shopee", canal="chat", modo="auto", status="ok"
        )
    )
    await db.commit()
    await _logistica(db)
    prazo = T0 + timedelta(days=2)
    await _rastreio(db, prazo=prazo)
    await _ligar(db, fabrica_cliente=_fabrica(_Plataforma()))
    [conversa] = await _conversas(db)
    conversa_id = conversa.id
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)
    for origem in (enviar.ORIGEM_HUMANO, ORIGEM_IA):
        recusa = await enviar.motivo_para_nao_enviar(db, conversa, origem=origem)
        assert recusa is not None and recusa.code == enviar.RECUSA_CONVERSA_BLOQUEADA

    conversa.situacao = "aberta"
    await db.commit()
    recusa = await enviar.motivo_para_nao_enviar(db, conversa)
    assert recusa is not None and recusa.code == enviar.RECUSA_SEM_INTEGRACAO

    await _ligar(db, T0 + timedelta(minutes=10), fabrica_cliente=_fabrica(_Plataforma()))
    conversa = await db.get(AtendimentoConversa, conversa_id)
    assert conversa.situacao == "bloqueada"
    assert conversa.aguardando_resposta is True and conversa.prazo_resposta_em == prazo


async def test_log_sem_texto_do_comprador(db, make_user):
    """A Shopee responde erro com o texto do comprador na mensagem; depois a
    leitura dá certo (motivo, apelido e id do comprador): nada disso vai para
    o log — só ids do caso, contagens e o código do erro."""
    import structlog

    await _loja(db, make_user)
    await _logistica(db)
    await _rastreio(db)
    falsa = _Plataforma()
    falsa.erro_no_corpo = True
    with structlog.testing.capture_logs() as logs:
        resumo = await _ligar(db, fabrica_cliente=_fabrica(falsa))
        assert resumo["erros_leitura"] == 2 and resumo["conversas_novas"] == 1
        falsa.erro_no_corpo = False
        resumo = await _ligar(
            db, T0 + timedelta(hours=1, minutes=5), fabrica_cliente=_fabrica(falsa)
        )
        assert resumo["motivos_lidos"] == 1
    assert any(e["event"] == "atendimento_devolucao_leitura_falhou" for e in logs)
    tudo = repr(logs)
    for proibido in (TEXTO_DO_COMPRADOR, "comprador_kfa", "c***r"):
        assert proibido not in tudo


async def test_cota_e_da_rodada_somando_as_lojas(db, make_user, monkeypatch):
    """O teto `MAX_LEITURAS_RODADA` é da RODADA inteira (Shopee e TikTok,
    todas as lojas juntas), não por loja: 3 de teto = 3 chamadas no total."""
    from app.services.atendimento import reclamacoes_devolucoes as rd

    await _loja(db, make_user, "kfa")
    await _loja(db, make_user, "kfa", plataforma=IntegrationPlatform.TIKTOK)
    for i in range(2):
        await _logistica(db, pedido=f"SN{i}", bling=f"70{i}")
        await _rastreio(db, bling=f"70{i}", rid=f"R{i}")
        await _chat(db, pedido=f"SN{i}")
        await _logistica(
            db,
            plataforma="TikTok",
            pedido=f"TT{i}",
            bling=f"71{i}",
            status="AWAITING_BUYER_SHIP",
            tipo="RETURN_AND_REFUND",
        )
        await _rastreio(
            db,
            bling=f"71{i}",
            fonte="tiktok",
            rid=f"RT{i}",
            status="AWAITING_BUYER_SHIP",
            acao=None,
        )
        await _chat(db, "tiktok", f"TT{i}")
    monkeypatch.setattr(rd, "MAX_LEITURAS_RODADA", 3)
    falsa = _Plataforma()
    resumo = await _ligar(db, fabrica_cliente=_fabrica(falsa))
    assert len(falsa.chamadas) == 3 == resumo["leituras"]
    assert resumo["leituras_adiadas"] == 1
    resumo = await _ligar(db, T0 + timedelta(minutes=10), fabrica_cliente=_fabrica(falsa))
    assert len(falsa.chamadas) == 4 and resumo["leituras"] == 1
    # Tudo lido: a rodada seguinte não chama nada.
    resumo = await _ligar(db, T0 + timedelta(minutes=20), fabrica_cliente=_fabrica(falsa))
    assert len(falsa.chamadas) == 4 and resumo["leituras"] == 0


# ─────────────── as tabelas (puras) ───────────────


@pytest.mark.parametrize(
    ("de_entao", "agora", "vale"),
    [
        ("PROCESSING", "PROCESSING", True),
        ("buyer_shipped_item", "BUYER_SHIPPED_ITEM", True),
        ("PROCESSING", "SELLER_DISPUTE", False),
        ("BUYER_SHIPPED_ITEM", "REJECT_RECEIVE_PACKAGE", False),
        (None, "REQUESTED", True),  # o rastreio sem status: vale o que ele tem
    ],
)
def test_acao_vale(de_entao, agora, vale):
    from app.services.atendimento.reclamacoes_devolucoes import acao_vale

    dr = DevolucaoRastreio(pedido_bling="1", devolucao_status_auto=de_entao)
    assert acao_vale(dr, agora) is vale
    assert acao_vale(None, agora) is False


@pytest.mark.parametrize(
    ("plataforma", "status", "texto"),
    [
        ("shopee", "REQUESTED", "Pedido de devolução aberto"),
        ("shopee", "PROCESSING", "Em andamento"),
        ("shopee", "JUDGING", "Em análise pela Shopee (disputa)"),
        ("shopee", "SELLER_DISPUTE", "Loja contestou"),
        ("shopee", "ACCEPTED", "Aceita — reembolso pago ao comprador"),
        ("shopee", "CANCELLED", "Cancelada"),
        ("shopee", "CLOSED", "Encerrada pela Shopee"),
        # Fora da tabela: o texto da Logística ("RTS", o sintético dela).
        ("shopee", "RTS", "Entrega falhou — pacote de volta pela Shopee Xpress"),
        ("tiktok", "AWAITING_BUYER_SHIP", "Esperando o comprador enviar"),
        ("tiktok", "BUYER_SHIPPED_ITEM", "Comprador enviou o produto"),
        ("tiktok", "REJECT_RECEIVE_PACKAGE", "Recebimento recusado"),
        (
            "tiktok",
            "RETURN_OR_REFUND_REQUEST_PENDING",
            "Pedido de devolução/reembolso pendente",
        ),
        ("tiktok", "RETURN_OR_REFUND_REQUEST_CANCEL", "Cancelada pelo comprador"),
        ("tiktok", "RETURN_OR_REFUND_REQUEST_COMPLETE", "Concluída — reembolso pago"),
        # Desconhecido de tudo: legível, nunca some.
        ("tiktok", "SOMETHING_NEW", "Devolução: SOMETHING_NEW"),
        ("shopee", None, None),
    ],
)
def test_status_tela(plataforma, status, texto):
    from app.services.atendimento.reclamacoes_devolucoes import status_tela

    assert status_tela(plataforma, status) == texto


@pytest.mark.parametrize(
    ("bruto", "texto"),
    [
        ("CHANGE_MIND", "Mudou de ideia (desistiu da compra)"),
        ("FUNCTIONAL_DMG", "Produto com defeito (não funciona)"),
        ("WRONG_ITEM", "Recebeu produto errado"),
        ("ITEM_MISSING", "Falta produto no pacote"),
        ("NOT_RECEIPT", "Não recebeu o produto"),
        ("SUSPICIOUS_PARCEL", "Pacote vazio ou violado"),
        ("ITEM_NOT_IN_THE_LIST", "Item not in the list"),  # desconhecido: legível
        ("not_working_item", "Produto não funciona"),  # o código do ML
        ("wrong_size_xyz", "Wrong size xyz"),
        ("Item com defeito", "Item com defeito"),  # o rótulo da TikTok passa
        ("", None),
        (None, None),
    ],
)
def test_motivo_legivel(bruto, texto):
    from app.services.atendimento.reclamacoes_devolucoes import motivo_legivel

    assert motivo_legivel(bruto) == texto


def test_motivo_da_shopee_e_da_tiktok_sem_texto_do_comprador():
    from app.services.atendimento.reclamacoes_devolucoes import motivo_shopee, motivo_tiktok

    assert motivo_shopee({"reason": "wrong_item", "text_reason": TEXTO_DO_COMPRADOR}) == (
        "WRONG_ITEM",
        "Recebeu produto errado",
    )
    assert motivo_shopee({"reason": "NONE", "text_reason": TEXTO_DO_COMPRADOR}) == (None, None)
    assert motivo_shopee({}) == (None, None)
    registros = [
        {"role": "SELLER", "event": "SELLER_REJECT_RECEIVE", "reason_text": "Pacote errado"},
        {
            "role": "BUYER",
            "event": "ORDER_REFUND",
            "reason_text": "Não recebi",
            "note": TEXTO_DO_COMPRADOR,
            "create_time": 10,
        },
        {
            "role": "BUYER",
            "event": "ORDER_RETURN",
            "reason_text": "Item com defeito",
            "note": TEXTO_DO_COMPRADOR,
            "create_time": 20,
        },
        {"role": "BUYER", "event": "BUYER_SHIPPED", "reason_text": "", "create_time": 30},
    ]
    assert motivo_tiktok(registros) == "Item com defeito"
    # Só a loja tem motivo: não é o do comprador.
    assert motivo_tiktok(registros[:1]) is None
    assert motivo_tiktok([]) is None
