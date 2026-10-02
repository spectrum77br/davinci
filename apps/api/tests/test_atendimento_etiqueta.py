"""Etiqueta = status atual da conversa (RF1, 01/10/2026).

- `calcular` (PURA): a tabela de acontecimentos do RF1 e a prioridade
  Reclamação > Ag. cancelamento > Devolução > Avaliação > Pré-venda >
  Pós-venda (a Avaliação entrou em 02/10/2026, RF8), com as
  outras abertas no indicador pequeno (secundárias) e a base nunca nele;
- `ag_cancelamento_visivel`: 83955 vira etiqueta, MENOS a trava do robô da
  Margem (o encaixe do item 4);
- `recalcular_etiqueta`: grava etiqueta/desde/secundárias e uma linha de
  histórico por MUDANÇA (a primeira classificação não é mudança; recalcular
  sem novidade não repete linha), com o caso real 297840 (ML 2000018509205724,
  reclamação 5582543195);
- troca à mão: fica no histórico com quem trocou, vale até o próximo
  acontecimento automático, e trocar para o que o motor dá volta ao automático;
- o elo pedido → conversa (`conversa_do_pedido`, pelo nº do Bling também);
- um fato que não se lê não rebaixa a etiqueta;
- a lista da API traz a etiqueta;
- a mensagem do mediador não é resposta da loja.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoConversa,
    AtendimentoReclamacao,
    BlingOrder,
    MargemAudit,
    UserRole,
)
from app.models.pricing import StoreInfo
from app.routers import atendimento as rota
from app.services.atendimento import etiqueta_fatos, gravar
from app.services.atendimento.constantes import (
    AUTOR_MEDIADOR,
    CANAIS_POR_PLATAFORMA,
    ETIQUETAS,
    ETIQUETAS_BASE,
    PRIORIDADE_ETIQUETAS,
    e_nota,
)
from app.services.atendimento.etiqueta import (
    Calculo,
    FatosEtiqueta,
    _texto_motivo,
    calcular,
    historico_da_conversa,
    recalcular_etiqueta,
    secundarias_exibidas,
    trocar_etiqueta_manual,
)
from app.services.atendimento.etiqueta_fatos import (
    PedidoBling,
    ag_cancelamento_visivel,
    conversa_do_pedido,
    conversas_do_pedido,
    conversas_do_pedido_bling,
    e_pos_venda,
    normalizar_plataforma,
)
from app.services.bling_situacoes import (
    SITUACAO_AGUARDANDO_CANCELAMENTO,
    SITUACAO_AGUARDANDO_DEVOLUCAO,
)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PEDIDO_ML = "2000018509205724"
PEDIDO_BLING = "297840"
RECLAMACAO_ML = "5582543195"


# ─────────────── a função pura ───────────────


def _fatos(**kw) -> FatosEtiqueta:
    return FatosEtiqueta(**kw)


@pytest.mark.parametrize(
    ("acontecimento", "fatos", "esperada"),
    [
        ("pergunta/mensagem sem pedido", _fatos(), "pre_venda"),
        ("pedido criado", _fatos(tem_pedido=True), "pos_venda"),
        ("reclamação aberta", _fatos(tem_pedido=True, reclamacao_aberta=True), "reclamacao"),
        ("mediação aberta (sem pedido gravado)", _fatos(reclamacao_aberta=True), "reclamacao"),
        ("devolução iniciada", _fatos(tem_pedido=True, devolucao_aberta=True), "devolucao"),
        ("reclamação/devolução encerrada", _fatos(tem_pedido=True), "pos_venda"),
        ("Bling → Ag. cancelamento", _fatos(tem_pedido=True, ag_cancelamento=True),
         "ag_cancelamento"),
        ("Bling sai de Ag. cancelamento", _fatos(tem_pedido=True), "pos_venda"),
    ],
)
def test_tabela_de_acontecimentos_do_rf1(acontecimento, fatos, esperada):
    assert calcular(fatos).etiqueta == esperada, acontecimento


URGENTES = ("reclamacao", "ag_cancelamento", "devolucao", "avaliacao")
_CAMPO = {
    "reclamacao": "reclamacao_aberta",
    "ag_cancelamento": "ag_cancelamento",
    "devolucao": "devolucao_aberta",
    "avaliacao": "avaliacao_pendente",
}


@pytest.mark.parametrize("tem_pedido", [False, True])
@pytest.mark.parametrize(
    "abertas",
    [c for n in range(len(URGENTES) + 1) for c in itertools.combinations(URGENTES, n)],
)
def test_prioridade_vale_a_mais_urgente_e_as_outras_viram_indicador(abertas, tem_pedido):
    calculo = calcular(_fatos(tem_pedido=tem_pedido, **{_CAMPO[e]: True for e in abertas}))
    if not abertas:
        assert calculo == Calculo(
            "pos_venda" if tem_pedido else "pre_venda", [], calculo.motivo
        )
        return
    em_ordem = [e for e in PRIORIDADE_ETIQUETAS if e in abertas]
    assert calculo.etiqueta == em_ordem[0]
    # A base (pré/pós) nunca é indicador; as outras, da mais urgente à menos.
    assert calculo.secundarias == em_ordem[1:]


def test_ordem_da_prioridade_e_canal_nunca_e_etiqueta():
    assert PRIORIDADE_ETIQUETAS == (
        "reclamacao", "ag_cancelamento", "devolucao", "avaliacao", "pre_venda", "pos_venda"
    )
    assert set(ETIQUETAS_BASE) == {"pre_venda", "pos_venda"}
    # E-mail, Zap, chat, pergunta, SAC: canal, não status.
    for canal in ("email", "zap", "chat", "pergunta", "sac"):
        assert canal not in ETIQUETAS
    assert {c for cs in CANAIS_POR_PLATAFORMA.values() for c in cs} - set(ETIQUETAS) >= {
        "email", "chat", "pergunta", "sac"
    }


def test_motivo_vem_dos_fatos():
    calculo = calcular(
        _fatos(
            tem_pedido=True,
            motivo_pedido="pedido 1 ligado",
            reclamacao_aberta=True,
            motivo_reclamacao="Reclamação 5582543195 aberta no ML",
        )
    )
    assert calculo.motivo == "Reclamação 5582543195 aberta no ML"
    assert calcular(_fatos(tem_pedido=True, motivo_pedido="pedido 9 ligado")).motivo == (
        "pedido 9 ligado"
    )


def test_texto_da_linha_do_tempo():
    entrou = calcular(_fatos(tem_pedido=True, reclamacao_aberta=True,
                             motivo_reclamacao="Reclamação 5 aberta no ML"))
    assert _texto_motivo("leitura do ML", "pos_venda", entrou) == (
        "Reclamação 5 aberta no ML (leitura do ML)"
    )
    # Na volta, conta o que ACABOU — não "pedido ligado".
    voltou = calcular(_fatos(tem_pedido=True, motivo_pedido="pedido 1 ligado"))
    assert _texto_motivo("cron", "reclamacao", voltou) == "Reclamação encerrada (cron)"
    assert _texto_motivo(None, "ag_cancelamento", voltou) == (
        "Pedido saiu de Aguardando Cancelamento"
    )
    # De uma urgente para outra: o que acabou e o que abriu.
    devolucao = calcular(_fatos(tem_pedido=True, devolucao_aberta=True,
                                motivo_devolucao="pedido 7 em Aguardando Devolução no Bling"))
    assert _texto_motivo(None, "reclamacao", devolucao) == (
        "Reclamação encerrada · Pedido 7 em Aguardando Devolução no Bling"
    )
    # Saindo de troca à mão: nada "encerrou".
    assert _texto_motivo("cron", "reclamacao", voltou, era_manual=True) == (
        "Volta ao automático · Pedido 1 ligado (cron)"
    )
    assert len(_texto_motivo("x" * 1000, None, voltou)) <= 300


def test_secundarias_exibidas_com_troca_a_mao():
    calculo = calcular(_fatos(tem_pedido=True, reclamacao_aberta=True, devolucao_aberta=True))
    assert secundarias_exibidas(calculo, "reclamacao") == ["devolucao"]
    # A pessoa pôs Pós-venda com a reclamação aberta: o indicador mostra as duas.
    assert secundarias_exibidas(calculo, "pos_venda") == ["reclamacao", "devolucao"]
    assert secundarias_exibidas(calculo, "devolucao") == ["reclamacao"]
    assert secundarias_exibidas(calcular(_fatos(tem_pedido=True)), "pre_venda") == []


@pytest.mark.parametrize(
    ("canal", "pedido", "pos"),
    [
        ("pos_venda", None, True),
        ("sac", None, True),
        ("email", None, True),
        ("reclamacao", None, True),
        ("pergunta", "123", False),
        ("chat", None, False),
        ("chat", "  ", False),
        ("chat", "2409ABC", True),
    ],
)
def test_pre_e_pos_venda_pela_regra_dos_filtros(canal, pedido, pos):
    assert e_pos_venda(canal, pedido) is pos


def test_plataforma_do_cadastro():
    assert normalizar_plataforma("mercadolivre") == "ml"
    assert normalizar_plataforma(" Shopee ") == "shopee"
    assert normalizar_plataforma("") is None


@pytest.mark.parametrize(
    ("pedido", "visivel"),
    [
        (None, False),
        (PedidoBling(numero="1", situacao="6"), False),
        # Falta de estoque / restrição (sweep de NF) e movimento à mão: sem trilha.
        (PedidoBling(numero="1", situacao="83955"), True),
        # O robô da Margem segurou: trava interna, não é cancelamento.
        (PedidoBling(numero="1", situacao="83955", origem_ag_cancelamento="margens_auto",
                     pino_margem="Pendente"), False),
        (PedidoBling(numero="1", situacao="83955", origem_ag_cancelamento="margens_auto",
                     pino_margem="Reprovado"), False),
        # Pino de análise sem trilha (robô antigo): também não.
        (PedidoBling(numero="1", situacao="83955", pino_margem="Pendente"), False),
        # Pessoa reprovou na aba Margem: cancelamento de verdade.
        (PedidoBling(numero="1", situacao="83955", origem_ag_cancelamento="margens",
                     pino_margem="Reprovado"), True),
        (PedidoBling(numero="1", situacao="83955", pino_margem="Reprovado"), True),
    ],
)
def test_ag_cancelamento_visivel_menos_a_trava_da_margem(pedido, visivel):
    assert ag_cancelamento_visivel(pedido) is visivel


# ─────────────── banco ───────────────


async def _conversa(
    db: AsyncSession,
    *,
    plataforma: str = "ml",
    canal: str = "pos_venda",
    externo_id: str = "pack-1",
    pedido: str | None = PEDIDO_ML,
    dados: dict | None = None,
    ultima: datetime | None = None,
) -> AtendimentoConversa:
    c = AtendimentoConversa(
        plataforma=plataforma,
        canal=canal,
        externo_id=externo_id,
        pedido_marketplace=pedido,
        dados=dados or {},
        ultima_mensagem_em=ultima,
    )
    db.add(c)
    await db.commit()
    return c


async def _bling(
    db: AsyncSession,
    *,
    numero: str = PEDIDO_BLING,
    numeroloja: str = PEDIDO_ML,
    situacao: int | str = 83953,
    status: str | None = None,
    loja: str | None = None,
    itens: int = 2,
) -> None:
    for i in range(itens):
        db.add(
            BlingOrder(
                bling_id=9000 + int(numero) % 1000,
                numero=numero,
                numeroloja=numeroloja,
                situacao=str(situacao),
                status=status,
                loja=loja,
                data=T0 - timedelta(days=10),
                item_index=i,
                item_codigo=f"SKU-{i}",
                itemvalor=Decimal("10"),
            )
        )
    await db.commit()


async def _situacao(db: AsyncSession, numero: str, situacao: int | str) -> None:
    linhas = (
        (await db.execute(select(BlingOrder).where(BlingOrder.numero == numero))).scalars().all()
    )
    for linha in linhas:
        linha.situacao = str(situacao)
    await db.commit()


async def _reclamacao(
    db: AsyncSession,
    *,
    externo_id: str = RECLAMACAO_ML,
    tipo: str = "reclamacao",
    plataforma: str = "ml",
    pedido: str | None = PEDIDO_ML,
    conversa_id=None,
    prazo: datetime | None = None,
) -> AtendimentoReclamacao:
    r = AtendimentoReclamacao(
        plataforma=plataforma,
        externo_id=externo_id,
        tipo=tipo,
        status="opened",
        pedido_marketplace=pedido,
        conversa_id=conversa_id,
        aberta_em=T0 - timedelta(days=1),
        prazo_em=prazo,
    )
    db.add(r)
    await db.commit()
    return r


async def _historico(db: AsyncSession, conversa: AtendimentoConversa):
    return [
        (h.de, h.para, h.motivo, h.por_user_id)
        for h in await historico_da_conversa(db, conversa.id)
    ]


async def _recalcular(db: AsyncSession, conversa: AtendimentoConversa, motivo: str, minutos=0):
    mudou = await recalcular_etiqueta(
        db, conversa, motivo=motivo, agora=T0 + timedelta(minutes=minutos)
    )
    await db.commit()
    return mudou


async def test_caso_297840_reclamacao_do_ml_muda_a_etiqueta_e_volta(db):
    """Pós-venda → Reclamação → (Bling em devolução: indicador) → Devolução → Pós-venda."""
    c = await _conversa(db, dados={"pack_id": "2000009999999999"})
    await _bling(db)  # 83953 = Entregue

    # Primeira classificação: grava, mas não é mudança (sem linha).
    assert await _recalcular(db, c, "cron") is True
    assert (c.etiqueta, c.etiqueta_automatica, c.etiquetas_secundarias) == (
        "pos_venda", "pos_venda", []
    )
    assert c.etiqueta_desde == T0
    assert await _historico(db, c) == []

    await _reclamacao(db)
    assert await _recalcular(db, c, "leitura das reclamações do ML", 1) is True
    assert c.etiqueta == "reclamacao"
    assert c.etiqueta_desde == T0 + timedelta(minutes=1)
    assert await _historico(db, c) == [
        ("pos_venda", "reclamacao",
         f"Reclamação {RECLAMACAO_ML} aberta no ML (leitura das reclamações do ML)", None),
    ]

    # Recalcular sem novidade: nada muda, nenhuma linha nova.
    assert await _recalcular(db, c, "cron", 2) is False
    assert len(await _historico(db, c)) == 1

    # Bling vai para Aguardando Devolução com a reclamação aberta: vale a
    # reclamação, a devolução vira indicador — sem linha (a etiqueta não mudou).
    await _situacao(db, PEDIDO_BLING, SITUACAO_AGUARDANDO_DEVOLUCAO)
    assert await _recalcular(db, c, "webhook do Bling", 3) is False
    assert (c.etiqueta, c.etiquetas_secundarias) == ("reclamacao", ["devolucao"])

    # A reclamação encerra: sobra a devolução.
    r = (await db.execute(select(AtendimentoReclamacao))).scalar_one()
    r.encerrada_em = T0 + timedelta(minutes=4)
    await db.commit()
    assert await _recalcular(db, c, "leitura das reclamações do ML", 5) is True
    assert (c.etiqueta, c.etiquetas_secundarias) == ("devolucao", [])

    # A devolução acaba no Bling: volta ao pós-venda.
    await _situacao(db, PEDIDO_BLING, 545902)
    assert await _recalcular(db, c, "webhook do Bling", 6) is True
    assert c.etiqueta == "pos_venda"
    assert await _historico(db, c) == [
        ("pos_venda", "reclamacao",
         f"Reclamação {RECLAMACAO_ML} aberta no ML (leitura das reclamações do ML)", None),
        ("reclamacao", "devolucao",
         f"Reclamação encerrada · Pedido {PEDIDO_BLING} em Aguardando Devolução no Bling"
         " (leitura das reclamações do ML)", None),
        ("devolucao", "pos_venda", "Devolução encerrada (webhook do Bling)", None),
    ]


async def test_pre_venda_vira_pos_venda_quando_o_pedido_aparece(db):
    c = await _conversa(db, plataforma="shopee", canal="chat", externo_id="s-1", pedido=None)
    await _recalcular(db, c, "sync")
    assert c.etiqueta == "pre_venda"
    c.pedido_marketplace = "2409ABCDEF"
    await db.commit()
    assert await _recalcular(db, c, "sync", 1) is True
    assert await _historico(db, c) == [
        ("pre_venda", "pos_venda", "Pedido 2409ABCDEF ligado (sync)", None)
    ]


async def test_ag_cancelamento_pelo_bling_menos_a_trava_da_margem(db):
    c = await _conversa(db, plataforma="shopee", canal="chat", externo_id="s-2",
                        pedido="2409SEMESTOQUE")
    await _bling(db, numero="297001", numeroloja="2409SEMESTOQUE",
                 situacao=SITUACAO_AGUARDANDO_CANCELAMENTO)
    await _recalcular(db, c, "cron")
    # Sem trilha da Margem (falta de estoque, manual): aparece.
    assert c.etiqueta == "ag_cancelamento"

    # O robô da Margem segurou outro pedido: trava interna, fica Pós-venda.
    d = await _conversa(db, plataforma="shopee", canal="chat", externo_id="s-3",
                        pedido="2409MARGEM")
    await _bling(db, numero="297002", numeroloja="2409MARGEM",
                 situacao=SITUACAO_AGUARDANDO_CANCELAMENTO, status="Pendente")
    db.add(MargemAudit(pedido_bling="297002", acao="situacao", valor_antigo="6",
                       valor_novo="83955", origem="margens_auto", mudado_por=None))
    await db.commit()
    await _recalcular(db, d, "cron")
    assert d.etiqueta == "pos_venda"

    # O pedido sai de Ag. cancelamento (troca → Em aberto): volta ao pós-venda.
    await _situacao(db, "297001", 6)
    assert await _recalcular(db, c, "webhook do Bling", 1) is True
    assert await _historico(db, c) == [
        ("ag_cancelamento", "pos_venda",
         "Pedido saiu de Aguardando Cancelamento (webhook do Bling)", None)
    ]


async def test_reclamacao_pela_conversa_e_pelo_claim_do_pack(db):
    # A conversa `reclamacao` criada para a reclamação, ligada pela conversa_id.
    c = await _conversa(db, canal="reclamacao", externo_id=RECLAMACAO_ML, pedido=None)
    await _reclamacao(db, pedido=None, conversa_id=c.id, tipo="mediacao")
    await _recalcular(db, c, "leitura das reclamações do ML")
    assert c.etiqueta == "reclamacao"

    # O pack do ML com `claim_ids` (o que o adaptador do ML já lê) e o chat
    # bloqueado pela reclamação: a reserva vale.
    d = await _conversa(db, externo_id="pack-2", pedido="2000000000000002",
                        dados={"claim_ids": [123], "status_ml": "blocked",
                               "substatus_ml": "blocked_by_claim"})
    await _recalcular(db, d, "sync")
    assert d.etiqueta == "reclamacao"
    d.dados = {"claim_ids": [], "status_ml": "active"}
    await db.commit()
    await _recalcular(db, d, "sync", 1)
    assert d.etiqueta == "pos_venda"
    assert (await _historico(db, d))[-1][2] == "Reclamação encerrada (sync)"

    # Devolução da Shopee pela tabela, casada pelo pedido (mesma plataforma).
    e = await _conversa(db, plataforma="shopee", canal="chat", externo_id="s-9", pedido="2409DEV")
    await _reclamacao(db, plataforma="shopee", externo_id="RET-1", tipo="devolucao",
                      pedido="2409DEV")
    # Outra plataforma com o mesmo número não conta.
    await _reclamacao(db, plataforma="tiktok", externo_id="X-1", tipo="reclamacao",
                      pedido="2409DEV")
    await _recalcular(db, e, "sync")
    assert (e.etiqueta, e.etiquetas_secundarias) == ("devolucao", [])


async def test_troca_a_mao_vale_ate_o_proximo_acontecimento(db, make_user):
    pessoa = await make_user()
    c = await _conversa(db)
    await _bling(db)
    await _recalcular(db, c, "cron")
    assert c.etiqueta == "pos_venda"

    with pytest.raises(ValueError):
        await trocar_etiqueta_manual(db, c, "email", user_id=pessoa.id)

    assert await trocar_etiqueta_manual(
        db, c, "pre_venda", user_id=pessoa.id, motivo="cliente ainda vai comprar",
        agora=T0 + timedelta(minutes=1),
    ) is True
    await db.commit()
    assert (c.etiqueta, c.etiqueta_manual, c.etiqueta_automatica) == (
        "pre_venda", True, "pos_venda"
    )
    assert await _historico(db, c) == [
        ("pos_venda", "pre_venda", "Trocada à mão: cliente ainda vai comprar", pessoa.id)
    ]

    # O cron recalcula e nada aconteceu: a troca à mão continua valendo.
    assert await _recalcular(db, c, "cron", 2) is False
    assert (c.etiqueta, c.etiqueta_manual) == ("pre_venda", True)
    assert len(await _historico(db, c)) == 1

    # Acontecimento automático (reclamação abriu): o automático volta a valer.
    await _reclamacao(db)
    assert await _recalcular(db, c, "leitura das reclamações do ML", 3) is True
    assert (c.etiqueta, c.etiqueta_manual) == ("reclamacao", False)
    assert (await _historico(db, c))[-1] == (
        "pre_venda", "reclamacao",
        f"Volta ao automático · Reclamação {RECLAMACAO_ML} aberta no ML"
        " (leitura das reclamações do ML)", None,
    )

    # À mão para Pós-venda com a reclamação aberta: o indicador mostra a reclamação.
    await trocar_etiqueta_manual(db, c, "pos_venda", user_id=pessoa.id)
    await db.commit()
    assert (c.etiqueta, c.etiqueta_manual, c.etiquetas_secundarias) == (
        "pos_venda", True, ["reclamacao"]
    )
    # Trocar para o que o motor dá = voltar ao automático.
    await trocar_etiqueta_manual(db, c, "reclamacao", user_id=pessoa.id)
    await db.commit()
    assert (c.etiqueta, c.etiqueta_manual, c.etiquetas_secundarias) == (
        "reclamacao", False, []
    )
    assert (await _historico(db, c))[-1][2] == "Trocada à mão (de volta ao automático)"


async def test_fato_que_nao_se_le_nao_rebaixa_a_etiqueta(db, monkeypatch):
    c = await _conversa(db)
    await _reclamacao(db)
    await _recalcular(db, c, "cron")
    assert c.etiqueta == "reclamacao"

    async def quebrado(session, conversa):
        # Erro DO BANCO no meio da leitura (aborta o SAVEPOINT, não a transação).
        await session.execute(text("SELECT 1 / 0"))
        raise AssertionError("não chega aqui")

    monkeypatch.setattr(etiqueta_fatos, "fatos_da_conversa", quebrado)
    assert await _recalcular(db, c, "cron", 1) is False
    assert c.etiqueta == "reclamacao"
    # A transação de quem chamou segue viva (o erro ficou no SAVEPOINT).
    assert (await db.execute(select(AtendimentoConversa.etiqueta))).scalar_one() == "reclamacao"


async def test_pedido_leva_a_conversa(db, make_user):
    ultima = T0 - timedelta(hours=1)
    pack = await _conversa(db, pedido="2000000000000777",
                           dados={"pack_id": PEDIDO_ML}, ultima=ultima)
    reclamacao = await _conversa(db, canal="reclamacao", externo_id=RECLAMACAO_ML,
                                 pedido=PEDIDO_ML, ultima=T0)
    await _conversa(db, plataforma="shopee", canal="chat", externo_id="s-x", pedido=PEDIDO_ML)

    # A conversa com o comprador vem antes da conversa da reclamação, e o
    # pack em `dados` casa (o Bling grava o pack em numeroloja no carrinho).
    assert [c.id for c in await conversas_do_pedido(db, "mercadolivre", PEDIDO_ML)] == [
        pack.id, reclamacao.id
    ]
    assert (await conversa_do_pedido(db, "ml", PEDIDO_ML)).id == pack.id
    assert await conversa_do_pedido(db, "ml", "nao-existe") is None
    assert await conversa_do_pedido(db, "ml", "  ") is None

    # Pelo nº do Bling: numeroloja + a plataforma da loja no cadastro.
    dono = await make_user()
    db.add(StoreInfo(user_id=dono.id, platform="mercadolivre", bling_store_id="204897261"))
    await db.commit()
    await _bling(db, loja="204897261")
    assert [c.id for c in await conversas_do_pedido_bling(db, PEDIDO_BLING)] == [
        pack.id, reclamacao.id
    ]
    assert await conversas_do_pedido_bling(db, "000") == []


async def test_lista_da_api_traz_a_etiqueta(client, db, make_user, auth_as, monkeypatch):
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    auth_as(await make_user(role=UserRole.ADMIN))
    c = await _conversa(db, ultima=T0)
    await _reclamacao(db)
    await _bling(db, situacao=SITUACAO_AGUARDANDO_DEVOLUCAO)
    await _recalcular(db, c, "cron")

    r = await client.get("/api/atendimento/conversas")
    assert r.status_code == 200
    item = next(i for i in r.json()["itens"] if i["id"] == str(c.id))
    assert item["etiqueta"] == "reclamacao"
    assert item["etiquetas_secundarias"] == ["devolucao"]
    assert item["etiqueta_manual"] is False
    assert item["etiqueta_desde"].startswith("2026-10-01T12:00")

    # Conversa ainda não calculada: sem etiqueta, lista vazia.
    d = await _conversa(db, externo_id="pack-novo", ultima=T0)
    r = await client.get("/api/atendimento/conversas")
    item = next(i for i in r.json()["itens"] if i["id"] == str(d.id))
    assert (item["etiqueta"], item["etiquetas_secundarias"]) == (None, [])


async def test_mensagem_do_mediador_nao_e_resposta_da_loja(db):
    """`autor = 'mediador'` (0353): a plataforma na reclamação. Entra como o
    sistema — não é adotada como nossa, não vira `externo` e não tira a
    conversa da fila (nem conta como fala do comprador)."""
    c = await _conversa(db, canal="reclamacao", externo_id="claim-77", pedido=None)
    await gravar.gravar_mensagem(
        db, c, externo_id="m-1", autor="cliente", texto="o produto veio quebrado",
        enviada_em=T0,
    )
    m, criada = await gravar.gravar_mensagem(
        db, c, externo_id="m-2", autor=AUTOR_MEDIADOR, texto="Mediação iniciada",
        enviada_em=T0 + timedelta(minutes=5),
    )
    await db.commit()
    assert criada is True
    assert (m.autor, m.origem, m.status) == ("mediador", "sistema", "recebida")
    assert c.aguardando_resposta is True
    assert c.ultima_do_cliente_em == T0
    assert c.ultima_da_loja_em is None
    # A nota interna se reconhece por um lugar só.
    assert e_nota("davinci_nota") and e_nota(None, "nota") and not e_nota("externo", "texto")
