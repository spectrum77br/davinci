"""Item 4a — o MOTIVO do pedido em "Aguardando Cancelamento" (83955).

- `classificar` (PURA): a ordem de decisão do desenho §1.1 com as correções
  da crítica — A2 (a reprovação da Margem sem pessoa aparece, mas NÃO fala
  em cancelamento; 'Pendente' de qualquer origem é trava) e A1 (a marca da
  NF só explica o 83955 com o SKU do erro ainda no pedido e sem trilha de
  situação/SKU mais nova que ela; senão é velha → "movido à mão");
- a etiqueta continua o contrato de `ag_cancelamento_visivel` (a
  tabela-verdade de `test_atendimento_etiqueta.py` não muda);
- `skus_do_erro` lê os formatos reais de `nf_faturamento.erro_faturamento`,
  e o motivo só lista os SKUs do erro que ainda estão no pedido;
- o comprador pediu (`IN_CANCEL`) × o pedido já cancelado na plataforma
  (`CANCELLED`, sem dizer por quem);
- nenhum texto para a IA fala de margem, custo ou lucro;
- no banco: o motivo da etiqueta diz o porquê, a marca velha (item
  trocado, trilha mais nova) não vira "falta de estoque", e a leitura dos
  fatos da NF que quebra não derruba o pedido.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoConversa,
    BlingOrder,
    MargemAudit,
    NfEtiquetaArquivo,
    NfFaturamento,
    NfNota,
)
from app.services.atendimento import ag_cancelamento as ag
from app.services.atendimento import etiqueta, etiqueta_fatos
from app.services.atendimento.ag_cancelamento import (
    CANCELADO_PLATAFORMA,
    MANUAL,
    MARGEM_REPROVADA,
    MARGEM_TRAVA,
    PEDIDO_CLIENTE,
    POS_NF_MANUAL,
    RESTRICAO_ENVIO,
    SEM_ESTOQUE,
    TEXTO_IA_CANCELADO_NA_PLATAFORMA,
    TEXTO_IA_DECIDIDO_PELA_LOJA,
    TEXTO_IA_EM_VERIFICACAO,
    TEXTO_IA_FALTA_DE_ESTOQUE,
    TEXTO_IA_PEDIDO_DO_COMPRADOR,
    TEXTO_IA_RESTRICAO,
    TEXTOS_IA,
    Motivo,
    classificar,
    skus_do_erro,
)
from app.services.atendimento.etiqueta_fatos import PedidoBling, ag_cancelamento_visivel

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
ERRO_SEM_ESTOQUE = "Aguardando Cancelamento — saldo negativo: dg053.sp, a001.sp"
ERRO_RESTRICAO = "Restrição Shopee — Apple não envia pro RJ: Iphone 15 Apple 128GB"


def _p(**kw) -> PedidoBling:
    return PedidoBling(**{"numero": "1", "situacao": "83955", **kw})


# A marca VIVA de falta de estoque: o SKU do erro ainda está no pedido e
# nada mexeu nele depois da marca.
SEM_ESTOQUE_VIVO = {
    "nf_status": "sem_estoque",
    "nf_erro": ERRO_SEM_ESTOQUE,
    "nf_marcada_em": T0,
    "skus_itens": ("dg053.sp",),
}
RESTRICAO_VIVA = {"nf_status": "restricao", "nf_erro": ERRO_RESTRICAO, "nf_marcada_em": T0}


# (caso, pedido, status na plataforma, código, etiqueta, fala_cancelamento,
#  pode_sugerir_troca, texto_ia, conflito?)
CASOS = [
    # ── fora de 83955 ──
    ("sem pedido", None, None, None, False, None, None, None, False),
    (
        "em aberto",
        PedidoBling(numero="1", situacao="6"),
        None,
        None,
        False,
        None,
        None,
        None,
        False,
    ),
    (
        "em aberto com marca",
        PedidoBling(numero="1", situacao="6", **SEM_ESTOQUE_VIVO),
        None,
        None,
        False,
        None,
        None,
        None,
        False,
    ),
    # ── os 8 casos da tabela-verdade de hoje (o booleano não muda) ──
    (
        "sem trilha nem marca",
        _p(),
        None,
        MANUAL,
        True,
        False,
        False,
        TEXTO_IA_EM_VERIFICACAO,
        False,
    ),
    (
        "robô segurou (Pendente)",
        _p(origem_ag_cancelamento="margens_auto", pino_margem="Pendente"),
        None,
        MARGEM_TRAVA,
        False,
        False,
        False,
        None,
        False,
    ),
    (
        "robô reprovou",
        _p(origem_ag_cancelamento="margens_auto", pino_margem="Reprovado"),
        None,
        MARGEM_TRAVA,
        False,
        False,
        False,
        None,
        False,
    ),
    (
        "Pendente sem trilha",
        _p(pino_margem="Pendente"),
        None,
        MARGEM_TRAVA,
        False,
        False,
        False,
        None,
        False,
    ),
    # A2: a reprovação SEM pessoa registrada aparece, mas não fala em cancelamento.
    (
        "trilha margens + Reprovado sem pessoa",
        _p(origem_ag_cancelamento="margens", pino_margem="Reprovado"),
        None,
        MARGEM_REPROVADA,
        True,
        False,
        False,
        None,
        False,
    ),
    (
        "Reprovado sem trilha nem pessoa",
        _p(pino_margem="Reprovado"),
        None,
        MARGEM_REPROVADA,
        True,
        False,
        False,
        None,
        False,
    ),
    # ── regra 1: a PESSOA reprovou — o único da Margem que fala em cancelamento ──
    (
        "Reprovado por pessoa",
        _p(pino_margem="Reprovado", pino_por_pessoa=True),
        None,
        MARGEM_REPROVADA,
        True,
        True,
        False,
        TEXTO_IA_DECIDIDO_PELA_LOJA,
        False,
    ),
    (
        "pessoa reprovou o que o robô segurou",
        _p(origem_ag_cancelamento="margens_auto", pino_margem="Reprovado", pino_por_pessoa=True),
        None,
        MARGEM_REPROVADA,
        True,
        True,
        False,
        TEXTO_IA_DECIDIDO_PELA_LOJA,
        False,
    ),
    # ── A2: 'Pendente' de QUALQUER origem é trava ──
    (
        "trilha margens + Pendente",
        _p(origem_ag_cancelamento="margens", pino_margem="Pendente"),
        None,
        MARGEM_TRAVA,
        False,
        False,
        False,
        None,
        False,
    ),
    (
        "trilha margens sem pino",
        _p(origem_ag_cancelamento="margens"),
        None,
        MARGEM_REPROVADA,
        True,
        False,
        False,
        None,
        False,
    ),
    # ── o sweep de NF, com a marca viva ──
    (
        "falta de estoque",
        _p(**SEM_ESTOQUE_VIVO),
        None,
        SEM_ESTOQUE,
        True,
        True,
        True,
        TEXTO_IA_FALTA_DE_ESTOQUE,
        False,
    ),
    (
        "falta de estoque, SKU em outra caixa",
        _p(**{**SEM_ESTOQUE_VIVO, "skus_itens": ("x9", "A001.SP")}),
        None,
        SEM_ESTOQUE,
        True,
        True,
        True,
        TEXTO_IA_FALTA_DE_ESTOQUE,
        False,
    ),
    (
        "falta de estoque, trilha ANTES da marca (prioridade no mesmo tick)",
        _p(**SEM_ESTOQUE_VIVO, ultima_trilha_em=T0 - timedelta(minutes=1)),
        None,
        SEM_ESTOQUE,
        True,
        True,
        True,
        TEXTO_IA_FALTA_DE_ESTOQUE,
        False,
    ),
    (
        "falta de estoque, trilha na MESMA transação da marca",
        _p(**SEM_ESTOQUE_VIVO, ultima_trilha_em=T0),
        None,
        SEM_ESTOQUE,
        True,
        True,
        True,
        TEXTO_IA_FALTA_DE_ESTOQUE,
        False,
    ),
    (
        "restrição de envio",
        _p(**RESTRICAO_VIVA),
        None,
        RESTRICAO_ENVIO,
        True,
        True,
        False,
        TEXTO_IA_RESTRICAO,
        False,
    ),
    # ── depois da NF / marca velha (A1): movido à mão ──
    (
        "NF ok",
        _p(nf_status="ok", nf_marcada_em=T0),
        None,
        POS_NF_MANUAL,
        True,
        False,
        False,
        TEXTO_IA_EM_VERIFICACAO,
        False,
    ),
    (
        "NF processando",
        _p(nf_status="processando"),
        None,
        POS_NF_MANUAL,
        True,
        False,
        False,
        TEXTO_IA_EM_VERIFICACAO,
        False,
    ),
    (
        "só a nota em nf_nota",
        _p(tem_nf_ou_etiqueta=True),
        None,
        POS_NF_MANUAL,
        True,
        False,
        False,
        TEXTO_IA_EM_VERIFICACAO,
        False,
    ),
    (
        "sem_estoque mas com NF/etiqueta",
        _p(**SEM_ESTOQUE_VIVO, tem_nf_ou_etiqueta=True),
        None,
        POS_NF_MANUAL,
        True,
        False,
        False,
        TEXTO_IA_EM_VERIFICACAO,
        False,
    ),
    (
        "A1: trilha de situação mais nova que a marca",
        _p(**SEM_ESTOQUE_VIVO, ultima_trilha_em=T0 + timedelta(hours=1)),
        None,
        POS_NF_MANUAL,
        True,
        False,
        False,
        TEXTO_IA_EM_VERIFICACAO,
        False,
    ),
    (
        "A1: o SKU do erro já não está no pedido (item trocado)",
        _p(**{**SEM_ESTOQUE_VIVO, "skus_itens": ("dg053.ci",)}),
        None,
        POS_NF_MANUAL,
        True,
        False,
        False,
        TEXTO_IA_EM_VERIFICACAO,
        False,
    ),
    (
        "A1: itens do pedido desconhecidos",
        _p(**{**SEM_ESTOQUE_VIVO, "skus_itens": ()}),
        None,
        POS_NF_MANUAL,
        True,
        False,
        False,
        TEXTO_IA_EM_VERIFICACAO,
        False,
    ),
    (
        "A1: marca antiga sem os SKUs",
        _p(**{**SEM_ESTOQUE_VIVO, "nf_erro": "Aguardando Cancelamento — saldo negativo"}),
        None,
        POS_NF_MANUAL,
        True,
        False,
        False,
        TEXTO_IA_EM_VERIFICACAO,
        False,
    ),
    (
        "A1: trilha sem a hora da marca",
        _p(**{**SEM_ESTOQUE_VIVO, "nf_marcada_em": None}, ultima_trilha_em=T0),
        None,
        POS_NF_MANUAL,
        True,
        False,
        False,
        TEXTO_IA_EM_VERIFICACAO,
        False,
    ),
    (
        "A1: restrição com trilha mais nova",
        _p(**RESTRICAO_VIVA, ultima_trilha_em=T0 + timedelta(minutes=5)),
        None,
        POS_NF_MANUAL,
        True,
        False,
        False,
        TEXTO_IA_EM_VERIFICACAO,
        False,
    ),
    # ── a plataforma: o comprador pediu (IN_CANCEL) × já cancelado (sem dizer por quem) ──
    (
        "Shopee IN_CANCEL",
        _p(),
        "IN_CANCEL",
        PEDIDO_CLIENTE,
        True,
        True,
        False,
        TEXTO_IA_PEDIDO_DO_COMPRADOR,
        False,
    ),
    (
        "comprador pediu e falta estoque: vale o comprador",
        _p(**SEM_ESTOQUE_VIVO),
        "IN_CANCEL",
        PEDIDO_CLIENTE,
        True,
        True,
        False,
        TEXTO_IA_PEDIDO_DO_COMPRADOR,
        False,
    ),
    (
        "Shopee CANCELLED",
        _p(),
        "CANCELLED",
        CANCELADO_PLATAFORMA,
        True,
        True,
        False,
        TEXTO_IA_CANCELADO_NA_PLATAFORMA,
        False,
    ),
    (
        "ML cancelled",
        _p(nf_status="ok"),
        "cancelled",
        CANCELADO_PLATAFORMA,
        True,
        True,
        False,
        TEXTO_IA_CANCELADO_NA_PLATAFORMA,
        False,
    ),
    # A equipe cancelou na Shopee o pedido sem estoque e o Bling ficou em
    # 83955: cancelado, mas NÃO "pedido pelo comprador".
    (
        "falta de estoque e CANCELLED na Shopee: sem dizer por quem",
        _p(**SEM_ESTOQUE_VIVO),
        "CANCELLED",
        CANCELADO_PLATAFORMA,
        True,
        True,
        False,
        TEXTO_IA_CANCELADO_NA_PLATAFORMA,
        False,
    ),
    (
        "pronto para enviar não é cancelamento",
        _p(),
        "READY_TO_SHIP",
        MANUAL,
        True,
        False,
        False,
        TEXTO_IA_EM_VERIFICACAO,
        False,
    ),
    (
        "a Margem vence o comprador",
        _p(origem_ag_cancelamento="margens_auto", pino_margem="Pendente"),
        "IN_CANCEL",
        MARGEM_TRAVA,
        False,
        False,
        False,
        None,
        False,
    ),
    # ── Margem × NF ──
    (
        "conflito: robô + Pendente + falta de estoque viva",
        _p(origem_ag_cancelamento="margens_auto", pino_margem="Pendente", **SEM_ESTOQUE_VIVO),
        None,
        MARGEM_TRAVA,
        False,
        False,
        False,
        None,
        True,
    ),
    (
        "conflito: Pendente sem trilha + restrição viva",
        _p(pino_margem="Pendente", **RESTRICAO_VIVA),
        None,
        MARGEM_TRAVA,
        False,
        False,
        False,
        None,
        True,
    ),
    (
        "robô + Aprovado + falta de estoque viva: quem segura é o estoque",
        _p(origem_ag_cancelamento="margens_auto", pino_margem="Aprovado", **SEM_ESTOQUE_VIVO),
        None,
        SEM_ESTOQUE,
        True,
        True,
        True,
        TEXTO_IA_FALTA_DE_ESTOQUE,
        False,
    ),
    (
        "robô + Aprovado + restrição viva",
        _p(origem_ag_cancelamento="margens_auto", pino_margem="Aprovado", **RESTRICAO_VIVA),
        None,
        RESTRICAO_ENVIO,
        True,
        True,
        False,
        TEXTO_IA_RESTRICAO,
        False,
    ),
    (
        "robô + Aprovado sem marca: continua trava",
        _p(origem_ag_cancelamento="margens_auto", pino_margem="Aprovado"),
        None,
        MARGEM_TRAVA,
        False,
        False,
        False,
        None,
        False,
    ),
    (
        "robô + Aprovado + marca VELHA: continua trava, sem conflito",
        _p(
            origem_ag_cancelamento="margens_auto",
            pino_margem="Aprovado",
            **{**SEM_ESTOQUE_VIVO, "skus_itens": ("dg053.ci",)},
        ),
        None,
        MARGEM_TRAVA,
        False,
        False,
        False,
        None,
        False,
    ),
    (
        "robô + Aprovado + NF ok: continua trava",
        _p(origem_ag_cancelamento="margens_auto", pino_margem="Aprovado", nf_status="ok"),
        None,
        MARGEM_TRAVA,
        False,
        False,
        False,
        None,
        False,
    ),
    (
        "Aprovado sem trilha (resgatado e voltou à mão)",
        _p(pino_margem="Aprovado"),
        None,
        MANUAL,
        True,
        False,
        False,
        TEXTO_IA_EM_VERIFICACAO,
        False,
    ),
]


@pytest.mark.parametrize(
    ("caso", "pedido", "status", "codigo", "etiqueta", "fala", "troca", "texto_ia", "conflito"),
    CASOS,
    ids=[c[0] for c in CASOS],
)
def test_classificar_tabela(
    caso, pedido, status, codigo, etiqueta, fala, troca, texto_ia, conflito
):
    m = classificar(pedido, status_plataforma=status)
    if codigo is None:
        assert m is None, caso
        assert ag_cancelamento_visivel(pedido) is False
        return
    assert isinstance(m, Motivo)
    assert (m.codigo, m.etiqueta, m.fala_cancelamento, m.pode_sugerir_troca, m.texto_ia) == (
        codigo,
        etiqueta,
        fala,
        troca,
        texto_ia,
    ), caso
    assert (m.conflito is not None) is conflito, caso
    # A etiqueta É o contrato de `ag_cancelamento_visivel` (que não recebe o
    # status da plataforma: ele nunca muda a visibilidade).
    assert ag_cancelamento_visivel(pedido) is m.etiqueta
    assert ag_cancelamento_visivel(pedido) is bool(classificar(pedido) and m.etiqueta)
    assert m.texto_interno and len(m.texto_interno) <= 300


def test_falta_de_estoque_traz_os_skus_e_o_conflito_avisa():
    # Os dois SKUs do erro ainda no pedido.
    m = classificar(_p(**{**SEM_ESTOQUE_VIVO, "skus_itens": ("dg053.sp", "a001.sp")}))
    assert m.skus == ("dg053.sp", "a001.sp")
    assert m.texto_interno == "falta de estoque: dg053.sp, a001.sp"
    # O a001.sp já foi trocado à mão: só o que ficou no pedido é o que falta
    # (texto, `skus` para a troca e conflito), na ordem do erro.
    m = classificar(_p(**SEM_ESTOQUE_VIVO))
    assert m.skus == ("dg053.sp",)
    assert m.texto_interno == "falta de estoque: dg053.sp"
    trava = classificar(
        _p(origem_ag_cancelamento="margens_auto", pino_margem="Pendente", **SEM_ESTOQUE_VIVO)
    )
    assert trava.conflito == "a NF também marcou falta de estoque: dg053.sp"
    assert "não é cancelamento" in trava.texto_interno
    assert trava.conflito in trava.texto_interno
    assert trava.skus == ("dg053.sp",)
    # Sem caixa na comparação; a grafia que sai é a do erro.
    outra_caixa = classificar(_p(**{**SEM_ESTOQUE_VIVO, "skus_itens": ("x9", "A001.SP")}))
    assert (outra_caixa.codigo, outra_caixa.skus) == (SEM_ESTOQUE, ("a001.sp",))
    # Marca velha: nada de SKU (não é mais o que falta).
    velha = classificar(_p(**SEM_ESTOQUE_VIVO, ultima_trilha_em=T0 + timedelta(hours=1)))
    assert velha.skus == ()
    assert "velha" in velha.texto_interno
    # Restrição: o texto do sweep vai para a equipe como veio.
    assert classificar(_p(**RESTRICAO_VIVA)).texto_interno == ERRO_RESTRICAO
    loja = classificar(_p(nf_status="restricao", nf_erro="não envia pro RJ", nf_marcada_em=T0))
    assert loja.texto_interno == "restrição de envio: não envia pro RJ"


def test_texto_interno_longo_e_cortado():
    skus = tuple(f"sku{i:04d}.sp" for i in range(80))
    p = _p(
        nf_status="sem_estoque",
        nf_erro=f"Aguardando Cancelamento — saldo negativo: {', '.join(skus)}",
        nf_marcada_em=T0,
        skus_itens=skus,
    )
    m = classificar(p)
    assert m.codigo == SEM_ESTOQUE and len(m.skus) == 80
    assert len(m.texto_interno) == 300 and m.texto_interno.endswith("…")


def test_corte_do_texto_e_o_mesmo_da_etiqueta():
    """O limite do texto interno anda junto com o do motivo da linha do tempo."""
    assert ag.MAX_TEXTO_INTERNO == etiqueta.MAX_MOTIVO == 300
    assert ag.cortar("  a \n b  ") == "a b"
    longo = ag.cortar("x" * 400)
    assert len(longo) == 300 and longo.endswith("…")
    assert ag.cortar("x" * 300) == "x" * 300


@pytest.mark.parametrize(
    ("erro", "skus"),
    [
        # Sweep (`nf_auto_enfileirar`) e botão Enfileirar (`routers/nf.py`).
        ("Aguardando Cancelamento — saldo negativo: dg053.sp", ("dg053.sp",)),
        (ERRO_SEM_ESTOQUE, ("dg053.sp", "a001.sp")),
        # O kit é UM SKU.
        (
            "Aguardando Cancelamento — saldo negativo: dg057.ci+a001.ci, x1",
            ("dg057.ci+a001.ci", "x1"),
        ),
        # A marca antes de 10/08/2026 não trazia os SKUs.
        ("Aguardando Cancelamento — saldo negativo", ()),
        ("Aguardando Cancelamento - Saldo Negativo:  z0167.mala ,, z0167.mala ,", ("z0167.mala",)),
        # Outras marcas e erros de outras etapas.
        (ERRO_RESTRICAO, ()),
        ("Restrição da loja: não envia pro RJ — R$ 700,00", ()),
        ("falha ao emitir a NF no Upseller", ()),
        ("", ()),
        (None, ()),
    ],
)
def test_skus_do_erro(erro, skus):
    assert skus_do_erro(erro) == skus


def _todos_os_motivos() -> list[Motivo]:
    motivos = [classificar(c[1], status_plataforma=c[2]) for c in CASOS]
    return [m for m in motivos if m is not None]


def test_texto_ia_nunca_fala_margem():
    motivos = _todos_os_motivos()
    assert {m.codigo for m in motivos} == set(ag.CODIGOS)
    proibidas = ("margem", "custo", "lucro")
    for texto in TEXTOS_IA | {m.texto_ia for m in motivos if m.texto_ia}:
        assert not any(p in texto.lower() for p in proibidas), texto
    for m in motivos:
        # Vocabulário fechado: nada fora da lista chega à IA.
        assert m.texto_ia is None or m.texto_ia in TEXTOS_IA
        # Sem `fala_cancelamento`, o texto da IA não fala em cancelamento.
        if not m.fala_cancelamento:
            assert "cancel" not in (m.texto_ia or "").lower(), m
        # A trava da Margem nunca vira etiqueta nem texto para a IA.
        if m.codigo == MARGEM_TRAVA:
            assert (m.etiqueta, m.fala_cancelamento, m.texto_ia) == (False, False, None)


def test_status_na_plataforma():
    assert ag.status_na_plataforma({"pedido_mkt": {"status": " IN_CANCEL "}}) == "IN_CANCEL"
    assert ag.status_na_plataforma({"pedido_mkt": {"status": None}}) is None
    assert ag.status_na_plataforma({"pedido_mkt": None}) is None
    assert ag.status_na_plataforma({}) is None
    assert ag.status_na_plataforma(None) is None


def test_constantes_moram_no_classificador_e_continuam_em_etiqueta_fatos():
    for nome in ("ORIGEM_ROBO_MARGEM", "PINO_MARGEM_PENDENTE", "PINO_MARGEM_REPROVADO"):
        assert getattr(etiqueta_fatos, nome) is getattr(ag, nome)
    assert (ag.ORIGEM_ROBO_MARGEM, ag.PINO_MARGEM_PENDENTE, ag.PINO_MARGEM_REPROVADO) == (
        "margens_auto",
        "Pendente",
        "Reprovado",
    )


def test_classificador_nao_importa_contexto_nem_etiqueta_fatos():
    """Puro: `etiqueta_fatos` importa `contexto`, e os dois importam isto (ciclo)."""
    import ast
    import inspect

    arvore = ast.parse(inspect.getsource(ag))
    em_tempo_de_execucao = [
        n
        for n in arvore.body
        if isinstance(n, ast.ImportFrom)
        and n.module
        and n.module.startswith("app.")
        and n.module != "app.services.bling_situacoes"
    ]
    assert em_tempo_de_execucao == []


# ─────────────── banco: a carga e o motivo da etiqueta ───────────────


async def _conversa(
    db: AsyncSession, pedido: str, *, dados: dict | None = None
) -> AtendimentoConversa:
    c = AtendimentoConversa(
        plataforma="shopee",
        canal="chat",
        externo_id=f"conv-{pedido}",
        pedido_marketplace=pedido,
        dados=dados or {},
        ultima_mensagem_em=T0,
    )
    db.add(c)
    await db.commit()
    return c


async def _bling(
    db: AsyncSession,
    numero: str,
    numeroloja: str,
    situacao: str,
    skus: tuple[str, ...],
    *,
    status: str | None = None,
) -> None:
    for i, sku in enumerate(skus):
        db.add(
            BlingOrder(
                bling_id=int(numero) * 10,
                numero=numero,
                numeroloja=numeroloja,
                situacao=situacao,
                status=status,
                data=T0 - timedelta(days=1),
                item_index=i,
                item_codigo=sku,
                itemvalor=Decimal("10"),
            )
        )
    await db.commit()


async def _marca(db: AsyncSession, numero: str, status: str, erro: str | None, quando) -> None:
    db.add(
        NfFaturamento(
            pedido_bling=numero,
            status_faturamento=status,
            erro_faturamento=erro,
            created_at=quando,
            updated_at=quando,
        )
    )
    await db.commit()


async def _trilha(
    db: AsyncSession, numero: str, acao: str, de: str, para: str, origem: str, quando
) -> None:
    db.add(
        MargemAudit(
            pedido_bling=numero,
            acao=acao,
            valor_antigo=de,
            valor_novo=para,
            origem=origem,
            mudado_por=None,
            created_at=quando,
        )
    )
    await db.commit()


async def test_motivo_da_etiqueta_diz_falta_de_estoque(db):
    c = await _conversa(db, "2510SEM")
    await _bling(db, "310001", "2510SEM", "83955", ("dg053.sp", "a001.sp"))
    # A prioridade trocou um SKU no MESMO tick, antes do check de estoque.
    await _trilha(
        db, "310001", "sku", "dg053.ci", "dg053.sp", "prioridade_estoque", T0 - timedelta(minutes=1)
    )
    await _marca(
        db, "310001", "sem_estoque", "Aguardando Cancelamento — saldo negativo: dg053.sp", T0
    )

    pedido = await etiqueta_fatos.pedido_bling(db, "2510SEM")
    assert pedido.skus_itens == ("dg053.sp", "a001.sp")
    assert (pedido.nf_status, pedido.nf_marcada_em, pedido.tem_nf_ou_etiqueta) == (
        "sem_estoque",
        T0,
        False,
    )
    assert pedido.ultima_trilha_em == T0 - timedelta(minutes=1)
    assert classificar(pedido).codigo == SEM_ESTOQUE

    fatos = await etiqueta_fatos.fatos_da_conversa(db, c)
    assert fatos.ag_cancelamento is True
    assert fatos.motivo_ag_cancelamento == (
        "pedido 310001 em Aguardando Cancelamento — falta de estoque: dg053.sp"
    )

    # A pessoa trocou o item à mão no Bling (o espelho já tem o SKU novo):
    # a marca ficou velha — "movido à mão", e a IA não fala em cancelamento.
    linha = (
        await db.execute(
            select(BlingOrder).where(BlingOrder.numero == "310001", BlingOrder.item_index == 0)
        )
    ).scalar_one()
    linha.item_codigo = "dg053.ci"
    await db.commit()
    pedido = await etiqueta_fatos.pedido_bling(db, "310001")
    m = classificar(pedido)
    assert (m.codigo, m.etiqueta, m.fala_cancelamento) == (POS_NF_MANUAL, True, False)
    fatos = await etiqueta_fatos.fatos_da_conversa(db, c)
    assert fatos.ag_cancelamento is True
    assert fatos.motivo_ag_cancelamento.startswith(
        "pedido 310001 em Aguardando Cancelamento — motivo não registrado"
    )


async def test_trilha_mais_nova_que_a_marca_envelhece_a_marca(db):
    await _bling(db, "310002", "2510VELHA", "83955", ("z0167.mala",))
    await _marca(
        db, "310002", "sem_estoque", "Aguardando Cancelamento — saldo negativo: z0167.mala", T0
    )
    assert classificar(await etiqueta_fatos.pedido_bling(db, "310002")).codigo == SEM_ESTOQUE
    # Trilha de outro tipo (Saldo Final) não conta.
    await _trilha(db, "310002", "saldo_final", "1", "2", "margens", T0 + timedelta(hours=1))
    assert classificar(await etiqueta_fatos.pedido_bling(db, "310002")).codigo == SEM_ESTOQUE
    # A pessoa soltou na aba Margem (83955 → 6) e alguém pôs de volta à mão:
    # a marca de antes não explica o 83955 de agora.
    await _trilha(db, "310002", "situacao", "83955", "6", "margens", T0 + timedelta(hours=2))
    pedido = await etiqueta_fatos.pedido_bling(db, "310002")
    assert pedido.ultima_trilha_em == T0 + timedelta(hours=2)
    assert pedido.origem_ag_cancelamento is None
    assert classificar(pedido).codigo == POS_NF_MANUAL


async def test_nf_ou_etiqueta_do_pedido_e_pos_nf(db):
    await _bling(db, "310003", "2510NOTA", "83955", ("dg055.sp",))
    await _bling(db, "310004", "2510ETQ", "83955", ("dg055.sp",))
    await _bling(db, "310005", "2510STQ", "83955", ("dg055.sp",))
    erro = "Aguardando Cancelamento — saldo negativo: dg055.sp"
    for numero in ("310003", "310004", "310005"):
        await _marca(db, numero, "sem_estoque", erro, T0)
    db.add(NfNota(chave="4" * 44, pedido_bling="310003", numero="1", xml=b"<nfe/>"))
    db.add(
        NfEtiquetaArquivo(
            pedido_bling="310004", filename="e.pdf", content_type="application/pdf", blob=b"%"
        )
    )
    linha = (
        await db.execute(select(NfFaturamento).where(NfFaturamento.pedido_bling == "310005"))
    ).scalar_one()
    linha.status_etiqueta = "ok"
    await db.commit()
    for numero in ("310003", "310004", "310005"):
        pedido = await etiqueta_fatos.pedido_bling(db, numero)
        assert pedido.tem_nf_ou_etiqueta is True, numero
        assert classificar(pedido).codigo == POS_NF_MANUAL, numero


async def test_comprador_pediu_pelo_retrato_da_conversa(db):
    c = await _conversa(db, "2510CANC", dados={"pedido_mkt": {"status": "IN_CANCEL"}})
    await _bling(db, "310006", "2510CANC", "83955", ("dg053.sp",))
    fatos = await etiqueta_fatos.fatos_da_conversa(db, c)
    assert fatos.ag_cancelamento is True
    assert fatos.motivo_ag_cancelamento == (
        "pedido 310006 em Aguardando Cancelamento — o comprador pediu o cancelamento na "
        "plataforma (IN_CANCEL)"
    )


async def test_robo_com_margem_aprovada_e_falta_de_estoque_aparece(db):
    c = await _conversa(db, "2510APROV")
    await _bling(db, "310007", "2510APROV", "83955", ("dg053.sp",), status="Aprovado")
    await _marca(
        db,
        "310007",
        "sem_estoque",
        "Aguardando Cancelamento — saldo negativo: dg053.sp",
        T0 - timedelta(hours=1),
    )
    # A trilha do robô ANTES da marca (a entrada em 83955 com a margem).
    await _trilha(db, "310007", "situacao", "6", "83955", "margens_auto", T0 - timedelta(hours=2))
    pedido = await etiqueta_fatos.pedido_bling(db, "310007")
    assert pedido.origem_ag_cancelamento == "margens_auto"
    assert classificar(pedido).codigo == SEM_ESTOQUE
    assert (await etiqueta_fatos.fatos_da_conversa(db, c)).ag_cancelamento is True
    # Pino de volta a Pendente: a Margem vence, com o conflito no cartão.
    await db.execute(
        update(BlingOrder).where(BlingOrder.numero == "310007").values(status="Pendente")
    )
    await db.commit()
    m = classificar(await etiqueta_fatos.pedido_bling(db, "310007"))
    assert (m.codigo, m.etiqueta) == (MARGEM_TRAVA, False)
    assert m.conflito == "a NF também marcou falta de estoque: dg053.sp"
    assert (await etiqueta_fatos.fatos_da_conversa(db, c)).ag_cancelamento is False


async def test_fatos_da_nf_que_quebram_nao_derrubam_o_pedido(db, monkeypatch):
    """Erro de banco na leitura dos fatos da NF: SAVEPOINT próprio — o pedido
    vem com os campos da NF no padrão, a trava da Margem continua oculta e o
    resto cai em "movido à mão", sem falar em cancelamento."""
    c = await _conversa(db, "2510QUEBRA")
    await _bling(db, "310008", "2510QUEBRA", "83955", ("dg053.sp",), status="Pendente")
    await _trilha(db, "310008", "situacao", "6", "83955", "margens_auto", T0 - timedelta(hours=2))
    await _bling(db, "310009", "2510QUEBRA2", "83955", ("dg053.sp",))
    await _marca(
        db, "310009", "sem_estoque", "Aguardando Cancelamento — saldo negativo: dg053.sp", T0
    )

    async def _quebra(session, numeros):
        # Erro DE BANCO de verdade: sem o SAVEPOINT, a transação ficaria suja.
        await session.execute(text("SELECT 1 FROM tabela_que_nao_existe"))

    monkeypatch.setattr(etiqueta_fatos, "_fatos_nf", _quebra)
    trava = await etiqueta_fatos.pedido_bling(db, "310008")
    assert (trava.origem_ag_cancelamento, trava.nf_status) == ("margens_auto", None)
    assert ag_cancelamento_visivel(trava) is False
    assert (await etiqueta_fatos.fatos_da_conversa(db, c)).ag_cancelamento is False
    sem = await etiqueta_fatos.pedido_bling(db, "310009")
    assert (sem.nf_status, sem.nf_erro, sem.tem_nf_ou_etiqueta) == (None, None, False)
    m = classificar(sem)
    assert (m.codigo, m.etiqueta, m.fala_cancelamento) == (MANUAL, True, False)
    # A sessão segue usável: o SAVEPOINT desfez só a leitura que quebrou.
    status = (
        await db.execute(
            select(NfFaturamento.status_faturamento).where(NfFaturamento.pedido_bling == "310009")
        )
    ).scalar_one()
    assert status == "sem_estoque"


def test_replace_mantem_os_campos_novos_com_padrao():
    """`PedidoBling` só ganhou campos COM valor padrão (o contrato)."""
    p = PedidoBling(numero="1")
    assert (p.nf_status, p.nf_erro, p.nf_marcada_em, p.ultima_trilha_em) == (None,) * 4
    assert (p.skus_itens, p.tem_nf_ou_etiqueta) == ((), False)
    assert replace(p, situacao="83955") == _p()
