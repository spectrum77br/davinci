"""Etiqueta (RF1): os fatos em lote, o cron, o preenchimento e os ganchos do gravar.

- `fatos_em_lote` dá EXATAMENTE os mesmos fatos que `fatos_da_conversa`
  (o cron e o gancho do sync não podem discordar);
- a trilha da Margem: o pedido que o robô segurou e resgatou e que voltou
  para 83955 por outro motivo (falta de estoque) APARECE como Ag. cancelamento;
- `ids_da_rodada` pega só o que pode ter mudado (recente, urgente, à mão,
  Bling em 83955/83957, reclamação aberta) — barato;
- a rodada grava a etiqueta e o histórico, pula a conversa travada por
  outro processo e não desfaz a troca à mão;
- o cron sai com a leitura desligada e com outra rodada rodando;
- o preenchimento é seco por padrão, não grava nada no seco e preenche só
  as NULL (sem linha no histórico) no `--gravar`;
- o gravar recalcula na conversa nova, quando a leitura muda o pedido/claims
  e na primeira mensagem de conversa sem etiqueta — e NÃO a cada leitura.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoConversa,
    AtendimentoEtiquetaHistorico,
    AtendimentoReclamacao,
    BlingOrder,
    MargemAudit,
)
from app.redis_client import redis
from app.services.atendimento import etiqueta as etiqueta_svc
from app.services.atendimento import etiqueta_cron, etiqueta_fatos, gravar
from app.services.atendimento.etiqueta import trocar_etiqueta_manual

AGORA = datetime.now(UTC)
VELHA = AGORA - timedelta(days=40)


async def _conversa(
    db: AsyncSession,
    externo_id: str,
    *,
    plataforma: str = "shopee",
    canal: str = "chat",
    pedido: str | None = None,
    dados: dict | None = None,
    ultima: datetime | None = AGORA,
    etiqueta: str | None = None,
) -> AtendimentoConversa:
    c = AtendimentoConversa(
        plataforma=plataforma,
        canal=canal,
        externo_id=externo_id,
        pedido_marketplace=pedido,
        dados=dados or {},
        ultima_mensagem_em=ultima,
        etiqueta=etiqueta,
        etiqueta_automatica=etiqueta,
    )
    db.add(c)
    await db.commit()
    return c


async def _bling(
    db: AsyncSession,
    numero: str,
    numeroloja: str,
    situacao: int | str,
    *,
    status: str | None = None,
    itens: int = 1,
    dias: int = 10,
) -> None:
    for i in range(itens):
        db.add(
            BlingOrder(
                bling_id=int(numero) * 10,
                numero=numero,
                numeroloja=numeroloja,
                situacao=str(situacao),
                status=status,
                data=AGORA - timedelta(days=dias),
                item_index=i,
                item_codigo=f"SKU-{numero}-{i}",
                itemvalor=Decimal("10"),
            )
        )
    await db.commit()


async def _situacao(db: AsyncSession, numero: str, situacao: int | str, status=None) -> None:
    for linha in (
        await db.execute(select(BlingOrder).where(BlingOrder.numero == numero))
    ).scalars():
        linha.situacao = str(situacao)
        if status is not None:
            linha.status = status
    await db.commit()


async def _trilha(db: AsyncSession, numero: str, de: str, para: str, origem: str, quando):
    db.add(
        MargemAudit(
            pedido_bling=numero,
            acao="situacao",
            valor_antigo=de,
            valor_novo=para,
            origem=origem,
            mudado_por=None,
            created_at=quando,
        )
    )
    await db.commit()


async def _reclamacao(db: AsyncSession, externo_id: str, **kw) -> AtendimentoReclamacao:
    r = AtendimentoReclamacao(
        plataforma=kw.pop("plataforma", "shopee"),
        externo_id=externo_id,
        tipo=kw.pop("tipo", "reclamacao"),
        status="opened",
        aberta_em=AGORA - timedelta(days=1),
        **kw,
    )
    db.add(r)
    await db.commit()
    return r


async def _historico(db: AsyncSession, conversa: AtendimentoConversa) -> list[tuple]:
    return [
        (h.de, h.para, h.motivo) for h in await etiqueta_svc.historico_da_conversa(db, conversa.id)
    ]


async def _relida(db: AsyncSession, conversa: AtendimentoConversa) -> AtendimentoConversa:
    await db.refresh(conversa)
    return conversa


# ─────────────── fatos em lote = fatos de uma ───────────────


async def test_fatos_em_lote_iguais_aos_de_uma_conversa(db):
    pre = await _conversa(db, "pre")
    pos = await _conversa(db, "pos", pedido="2409POS")
    ag = await _conversa(db, "ag", pedido="2409AG")
    robo = await _conversa(db, "robo", pedido="2409ROBO")
    dev = await _conversa(db, "dev", pedido="2409DEV")
    recl = await _conversa(db, "recl", pedido="2409RECL")
    pack = await _conversa(
        db,
        "pack-1",
        plataforma="ml",
        canal="pos_venda",
        pedido="2000000000000001",
        # O chat bloqueado pela mediação: a reserva do `claim_ids` vale.
        dados={
            "pack_id": "2000000000000099",
            "claim_ids": ["777"],
            "status_ml": "blocked",
            "substatus_ml": "blocked_by_mediation",
        },
    )
    pergunta = await _conversa(db, "perg", plataforma="ml", canal="pergunta", pedido="x")
    await _bling(db, "300001", "2409POS", 15, itens=2)
    await _bling(db, "300002", "2409AG", 83955)
    await _bling(db, "300003", "2409ROBO", 83955, status="Pendente")
    await _trilha(db, "300003", "6", "83955", "margens_auto", AGORA - timedelta(hours=2))
    await _bling(db, "300004", "2409DEV", 83957)
    # O pack do ML: o Bling gravou o PACK em numeroloja (pedido de carrinho).
    await _bling(db, "300005", "2000000000000099", 83957)
    await _reclamacao(db, "R-1", pedido_marketplace="2409RECL", tipo="mediacao")
    await _reclamacao(
        db,
        "R-2",
        pedido_marketplace="2409DEV",
        tipo="devolucao",
        prazo_em=AGORA + timedelta(days=2),
    )
    # Encerrada não conta; outra plataforma com o mesmo número também não.
    await _reclamacao(
        db, "R-3", pedido_marketplace="2409POS", encerrada_em=AGORA - timedelta(hours=1)
    )
    await _reclamacao(db, "R-4", plataforma="tiktok", pedido_marketplace="2409POS")

    conversas = [pre, pos, ag, robo, dev, recl, pack, pergunta]
    lote = await etiqueta_fatos.fatos_em_lote(db, conversas)
    for c in conversas:
        assert lote[c.id] == await etiqueta_fatos.fatos_da_conversa(db, c), c.externo_id

    etiquetas = {c.externo_id: etiqueta_svc.calcular(lote[c.id]) for c in conversas}
    assert {k: v.etiqueta for k, v in etiquetas.items()} == {
        "pre": "pre_venda",
        "pos": "pos_venda",
        "ag": "ag_cancelamento",
        "robo": "pos_venda",  # a trava do robô da Margem não é cancelamento
        "dev": "devolucao",
        "recl": "reclamacao",
        "pack-1": "reclamacao",
        "perg": "pre_venda",  # a pergunta do ML é sempre antes da compra
    }
    # O pack: reclamação (claim do ML) com a devolução do Bling no indicador.
    assert etiquetas["pack-1"].secundarias == ["devolucao"]
    assert lote[dev.id].motivo_devolucao == "Devolução R-2 aberta no Shopee"
    assert lote[pos.id].numero_bling == "300001"
    assert await etiqueta_fatos.fatos_em_lote(db, []) == {}


async def test_trilha_da_margem_resgate_e_volta_por_outro_motivo(db):
    """Robô segurou (oculto) → resgatou (sai de 83955) → sweep de NF pôs em
    83955 por falta de estoque, sem trilha: agora É Ag. cancelamento."""
    c = await _conversa(db, "m-1", pedido="2409MARGEM")
    await _bling(db, "300010", "2409MARGEM", 83955, status="Reprovado")
    await _trilha(db, "300010", "6", "83955", "margens_auto", AGORA - timedelta(hours=5))
    pedido = await etiqueta_fatos.pedido_bling(db, ["2409MARGEM"])
    assert (pedido.situacao, pedido.origem_ag_cancelamento) == ("83955", "margens_auto")
    assert etiqueta_fatos.ag_cancelamento_visivel(pedido) is False

    # Resgate do robô: 83955 → Em aberto, pino Aprovado.
    await _trilha(db, "300010", "83955", "6", "margens_auto", AGORA - timedelta(hours=4))
    await _situacao(db, "300010", 6, status="Aprovado")
    # Falta de estoque (sweep de NF, sem trilha) põe de volta em 83955.
    await _situacao(db, "300010", 83955)
    pedido = await etiqueta_fatos.pedido_bling(db, "2409MARGEM")
    assert pedido.origem_ag_cancelamento is None
    assert etiqueta_fatos.ag_cancelamento_visivel(pedido) is True
    assert (await etiqueta_fatos.fatos_da_conversa(db, c)).ag_cancelamento is True

    # Pessoa reprovou na aba Margem: aparece.
    await _trilha(db, "300010", "6", "83955", "margens", AGORA - timedelta(hours=1))
    pedido = await etiqueta_fatos.pedido_bling(db, "2409MARGEM")
    assert pedido.origem_ag_cancelamento == "margens"
    assert etiqueta_fatos.ag_cancelamento_visivel(pedido) is True
    assert await etiqueta_fatos.pedido_bling(db, ["", "  "]) is None


# ─────────────── a rodada do cron ───────────────


async def test_ids_da_rodada_pega_so_o_que_pode_ter_mudado(db):
    recente = await _conversa(db, "recente")
    parada = await _conversa(db, "parada", pedido="2409PARADA", ultima=VELHA, etiqueta="pos_venda")
    sem_mensagem = await _conversa(db, "sem-msg", ultima=None)
    urgente = await _conversa(db, "urgente", ultima=VELHA, etiqueta="devolucao")
    a_mao = await _conversa(db, "a-mao", ultima=VELHA, etiqueta="pre_venda")
    a_mao.etiqueta_manual = True
    await db.commit()
    no_bling = await _conversa(db, "bling", pedido="2409CANC", ultima=VELHA, etiqueta="pos_venda")
    pack = await _conversa(
        db,
        "pack",
        plataforma="ml",
        canal="pos_venda",
        pedido="200001",
        dados={"pack_id": "2000PACK"},
        ultima=VELHA,
        etiqueta="pos_venda",
    )
    reclamada = await _conversa(db, "recl", ultima=VELHA, etiqueta="pos_venda")
    pelo_pedido = await _conversa(
        db, "recl-ped", pedido="2409RECL", ultima=VELHA, etiqueta="pos_venda"
    )
    await _bling(db, "300020", "2409CANC", 83955)
    await _bling(db, "300021", "2000PACK", 83957)
    await _bling(db, "300022", "2409PARADA", 6)
    await _reclamacao(db, "R-10", conversa_id=reclamada.id)
    await _reclamacao(db, "R-11", pedido_marketplace="2409RECL")
    await _reclamacao(
        db, "R-12", pedido_marketplace="2409PARADA", encerrada_em=AGORA - timedelta(days=1)
    )

    ids = set(await etiqueta_cron.ids_da_rodada(db))
    assert ids == {
        recente.id,
        urgente.id,
        a_mao.id,
        no_bling.id,
        pack.id,
        reclamada.id,
        pelo_pedido.id,
    }
    assert parada.id not in ids and sem_mensagem.id not in ids
    # O teto: a mais recente primeiro.
    assert await etiqueta_cron.ids_da_rodada(db, limite=1) == [recente.id]


async def test_rodada_grava_etiqueta_historico_e_respeita_a_troca_a_mao(db, make_user):
    pessoa = await make_user()
    c = await _conversa(db, "r-1", pedido="2409R1", ultima=VELHA, etiqueta="pos_venda")
    d = await _conversa(db, "r-2", pedido="2409R2", ultima=VELHA, etiqueta="pos_venda")
    await _bling(db, "300030", "2409R1", 15)
    await _bling(db, "300031", "2409R2", 15)
    # Nada mudou: a conversa parada nem entra na rodada.
    resumo = await etiqueta_cron.recalcular_rodada()
    assert (resumo["conversas"], resumo["mudaram"]) == (0, 0)

    # O Bling muda sem passar pelo sync (webhook de pedidos): 83955 e 83957.
    await _situacao(db, "300030", 83955)
    await _situacao(db, "300031", 83957)
    # E a pessoa tinha trocado `d` à mão para Pré-venda ANTES.
    await trocar_etiqueta_manual(db, await _relida(db, d), "pre_venda", user_id=pessoa.id)
    await db.commit()
    # (a troca à mão mediu o motor DEPOIS do 83957: nada aconteceu desde ela)
    resumo = await etiqueta_cron.recalcular_rodada()
    assert resumo == {"conversas": 2, "mudaram": 1, "ocupadas": 0, "falhas": 0, "cortadas": 0}
    c = await _relida(db, c)
    assert (c.etiqueta, c.etiqueta_manual) == ("ag_cancelamento", False)
    assert await _historico(db, c) == [
        (
            "pos_venda",
            "ag_cancelamento",
            "Pedido 300030 em Aguardando Cancelamento no Bling (conferência periódica)",
        ),
    ]
    d = await _relida(db, d)
    assert (d.etiqueta, d.etiqueta_manual, d.etiquetas_secundarias) == (
        "pre_venda",
        True,
        ["devolucao"],
    )

    # A devolução acaba no Bling: o acontecimento automático volta a valer.
    await _situacao(db, "300031", 545902)
    await _situacao(db, "300030", 6)
    resumo = await etiqueta_cron.recalcular_rodada()
    assert resumo["mudaram"] == 2
    d = await _relida(db, d)
    assert (d.etiqueta, d.etiqueta_manual) == ("pos_venda", False)
    assert (await _historico(db, d))[-1] == (
        "pre_venda",
        "pos_venda",
        "Volta ao automático · Pedido 2409R2 ligado (conferência periódica)",
    )
    assert (await _historico(db, await _relida(db, c)))[-1][2] == (
        "Pedido saiu de Aguardando Cancelamento (conferência periódica)"
    )


async def test_rodada_pula_a_conversa_travada_por_outro_processo(db):
    c = await _conversa(db, "t-1", pedido="2409T1", etiqueta="pos_venda")
    d = await _conversa(db, "t-2", pedido="2409T2", etiqueta="pos_venda")
    await _bling(db, "300040", "2409T1", 83955)
    await _bling(db, "300041", "2409T2", 83955)
    async with _db.SessionLocal() as outro:
        # O sync (ou a troca à mão) está com `c` travada agora.
        await outro.execute(
            select(AtendimentoConversa.id)
            .where(AtendimentoConversa.id == c.id)
            .with_for_update(key_share=True)
        )
        resumo = await etiqueta_cron.recalcular_ids([c.id, d.id], motivo="cron")
        await outro.rollback()
    assert (resumo["ocupadas"], resumo["mudaram"]) == (1, 1)
    assert (await _relida(db, c)).etiqueta == "pos_venda"
    assert (await _relida(db, d)).etiqueta == "ag_cancelamento"


async def test_lote_com_fato_ilegivel_nao_muda_nada(db, monkeypatch):
    c = await _conversa(db, "f-1", pedido="2409F1", etiqueta="reclamacao")

    async def quebrado(session, conversas):
        await session.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(etiqueta_fatos, "fatos_em_lote", quebrado)
    resumo = await etiqueta_cron.recalcular_ids([c.id], motivo="cron")
    assert (resumo["falhas"], resumo["mudaram"]) == (1, 0)
    assert (await _relida(db, c)).etiqueta == "reclamacao"


async def test_cron_sai_com_a_leitura_desligada_e_com_outra_rodada(db, monkeypatch):
    s = get_settings()
    c = await _conversa(db, "c-1")
    monkeypatch.setattr(s, "atendimento_leitura_ativa", False)
    monkeypatch.setattr(s, "atendimento_etiquetas_ativa", True)
    assert await etiqueta_cron.atendimento_etiquetas({}) is None
    # Leitura ligada, mas o interruptor PRÓPRIO desligado (o padrão): não roda.
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_etiquetas_ativa", False)
    assert await etiqueta_cron.atendimento_etiquetas({}) is None
    assert (await _relida(db, c)).etiqueta is None

    monkeypatch.setattr(s, "atendimento_etiquetas_ativa", True)
    chave = etiqueta_cron._CHAVE_RODADA.format(s.database_schema)
    await redis.delete(chave)
    try:
        await redis.set(chave, "outra-rodada", ex=60)
        assert await etiqueta_cron.atendimento_etiquetas({}) is None
        await redis.delete(chave)
        resumo = await etiqueta_cron.atendimento_etiquetas({})
        assert resumo["conversas"] == 1 and resumo["mudaram"] == 1
        # A trava foi solta no fim.
        assert await redis.get(chave) is None
    finally:
        await redis.delete(chave)


# ─────────────── o preenchimento (script) ───────────────


async def test_preencher_seco_nao_grava_e_gravar_preenche_sem_historico(db):
    await _conversa(db, "p-1", ultima=VELHA)
    await _conversa(db, "p-2", pedido="2409P2", ultima=VELHA)
    await _conversa(db, "p-3", pedido="2409P3", ultima=VELHA)
    ja = await _conversa(db, "p-4", ultima=VELHA, etiqueta="reclamacao")
    await _bling(db, "300050", "2409P3", 83957)

    seco = await etiqueta_cron.preencher(lote=2)
    assert seco["seco"] is True and seco["conversas"] == 3
    assert seco["por_etiqueta"] == {
        "reclamacao": 0,
        "ag_cancelamento": 0,
        "devolucao": 1,
        "avaliacao": 0,
        "pre_venda": 1,
        "pos_venda": 1,
    }
    assert (
        await db.execute(
            select(AtendimentoConversa.id).where(AtendimentoConversa.etiqueta.is_(None))
        )
    ).scalars().all().__len__() == 3

    gravou = await etiqueta_cron.preencher(seco=False, lote=2)
    assert (gravou["conversas"], gravou["mudaram"], gravou["falhas"]) == (3, 3, 0)
    assert gravou["por_etiqueta"] == {
        "reclamacao": 1,
        "ag_cancelamento": 0,
        "devolucao": 1,
        "avaliacao": 0,
        "pre_venda": 1,
        "pos_venda": 1,
        "sem": 0,
    }
    # A primeira classificação não é mudança: nenhuma linha no histórico.
    assert (await db.execute(select(AtendimentoEtiquetaHistorico))).scalars().all() == []
    # A que já tinha etiqueta ficou como estava; rodar de novo não acha nada.
    assert (await _relida(db, ja)).etiqueta == "reclamacao"
    assert (await etiqueta_cron.preencher(seco=False))["conversas"] == 0
    assert (await etiqueta_cron.preencher(limite=1, todas=True))["conversas"] == 1


async def test_script_e_seco_por_padrao():
    from scripts import atendimento_etiquetas_backfill as script

    assert script._argumentos([]).gravar is False
    assert script._argumentos(["--seco"]).gravar is False
    args = script._argumentos(["--gravar", "--lote", "50", "--limite", "10"])
    assert (args.gravar, args.lote, args.limite, args.todas) == (True, 50, 10, False)
    with pytest.raises(SystemExit):
        script._argumentos(["--seco", "--gravar"])
    with pytest.raises(SystemExit):
        script._argumentos(["--lote", "0"])


# ─────────────── os ganchos do gravar ───────────────


async def test_gravar_recalcula_na_conversa_nova_e_quando_o_pedido_chega(db, monkeypatch):
    chamadas: list[str] = []
    original = etiqueta_svc.recalcular_etiqueta

    async def espia(session, conversa, *, motivo, agora=None):
        chamadas.append(motivo)
        return await original(session, conversa, motivo=motivo, agora=agora)

    monkeypatch.setattr(etiqueta_svc, "recalcular_etiqueta", espia)

    async def ler(**kw):
        c, _ = await gravar.upsert_conversa(
            db,
            canal=None,
            integration=None,
            plataforma="shopee",
            canal_nome="chat",
            externo_id="g-1",
            **kw,
        )
        await db.commit()
        return c

    c = await ler(comprador_nome="Ana")
    assert (c.etiqueta, chamadas) == ("pre_venda", ["leitura da plataforma"])
    # A leitura seguinte sem nada que decida a etiqueta: não recalcula.
    await ler(comprador_nome="Ana Maria", nao_lidas=3)
    assert len(chamadas) == 1
    # O pedido apareceu: Pré-venda → Pós-venda, com o porquê na linha do tempo.
    c = await ler(pedido_marketplace="2409GANCHO")
    assert c.etiqueta == "pos_venda" and len(chamadas) == 2
    assert await _historico(db, c) == [
        ("pre_venda", "pos_venda", "Pedido 2409GANCHO ligado (leitura da plataforma)")
    ]
    # Mensagem comum não recalcula.
    await gravar.gravar_mensagem(
        db, c, externo_id="m-1", autor="cliente", texto="oi", enviada_em=AGORA
    )
    await db.commit()
    assert len(chamadas) == 2


async def test_gravar_claims_do_pack_e_mensagem_em_conversa_sem_etiqueta(db, make_user):
    async def pack(claims: list[str]):
        c, _ = await gravar.upsert_conversa(
            db,
            canal=None,
            integration=None,
            plataforma="ml",
            canal_nome="pos_venda",
            externo_id="pack-g",
            pedido_marketplace="2000000000000555",
            # Com reclamação, o ML bloqueia o chat (`blocked_by_claim`).
            dados={
                "pack_id": "2000000000000555",
                "claim_ids": claims,
                "status_ml": "blocked" if claims else "active",
                "substatus_ml": "blocked_by_claim" if claims else None,
            },
        )
        await db.commit()
        return c

    c = await pack([])
    assert c.etiqueta == "pos_venda"
    c = await pack(["5582543195"])
    assert c.etiqueta == "reclamacao"
    assert (await _historico(db, c))[-1] == (
        "pos_venda",
        "reclamacao",
        "Reclamação 5582543195 aberta no ML (leitura da plataforma)",
    )
    # Troca à mão; a leitura seguinte (mesmos claims) não a desfaz.
    pessoa = await make_user()
    await trocar_etiqueta_manual(db, c, "pos_venda", user_id=pessoa.id)
    await db.commit()
    c = await pack(["5582543195"])
    assert (c.etiqueta, c.etiqueta_manual) == ("pos_venda", True)
    # A reclamação acabou (claims vazios): acontecimento automático.
    c = await pack([])
    assert (c.etiqueta, c.etiqueta_manual) == ("pos_venda", False)

    # Conversa de antes da etiqueta (NULL): ganha a sua na primeira mensagem.
    velha = await _conversa(db, "velha", pedido="2409VELHA", ultima=VELHA)
    await gravar.gravar_mensagem(
        db, velha, externo_id="v-1", autor="cliente", texto="e aí?", enviada_em=AGORA
    )
    await db.commit()
    assert (await _relida(db, velha)).etiqueta == "pos_venda"
