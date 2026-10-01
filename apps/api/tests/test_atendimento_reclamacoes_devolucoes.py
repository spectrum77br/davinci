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
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoConversa,
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
    assert linha.motivo == "Devolução + reembolso"
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
    assert tela["status_rotulo"] == "Devolução solicitada pelo cliente — aguardando análise"
    assert tela["url_plataforma"] == f"https://seller.shopee.com.br/portal/sale/order/{SN}"

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
    assert reclamacoes.para_tela(linha)["status_rotulo"] == "Cancelado pelo cliente"
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
    original = etiqueta_fatos.conversa_do_pedido

    async def _quebra(session, plataforma, pedido):
        if pedido == SN:
            raise RuntimeError("quebrou")
        return await original(session, plataforma, pedido)

    monkeypatch.setattr(etiqueta_fatos, "conversa_do_pedido", _quebra)
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
