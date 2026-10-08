"""POR QUE o pedido está em "Aguardando Cancelamento" (83955) — o motivo do item 4.

PURO: recebe o `PedidoBling` já carregado (a trilha da Margem e os fatos da
NF vêm de `etiqueta_fatos._com_origem`) e devolve o `Motivo`. Não importa
`contexto` nem `etiqueta_fatos` em tempo de execução: `etiqueta_fatos`
importa `contexto` (`_chaves_do_pedido`) e os dois usam isto — o tipo
`PedidoBling` entra só para o verificador de tipos.

QUEM PÕE O PEDIDO EM 83955 (produção, 02/10/2026):
  • o robô da Margem (`margem_auto_hold`, trilha `margens_auto`, pino
    'Pendente'/'Reprovado'): trava INTERNA de margem, não é cancelamento;
  • a pessoa na aba Margem (trilha `margens`; pino com `aprovado_por`);
  • o sweep de NF e o botão Enfileirar (`nf_auto_enfileirar`,
    `routers/nf.py`): falta de estoque (`nf_faturamento.status_faturamento`
    'sem_estoque', erro "Aguardando Cancelamento — saldo negativo: <skus>")
    e restrição de envio ('restricao') — SEM trilha;
  • a pessoa no Bling, à mão — o caso mais comum hoje (Shopee depois da
    NF, com NF em `nf_nota` e rastreio): o motivo não fica registrado.

A MARCA DA NF ENVELHECE (crítica A1): `nf_faturamento` tem uma linha por
pedido e o sweep nunca reescreve um status preenchido; depois de uma troca
de item à mão, a marca 'sem_estoque' continua lá (31 de 37 marcas em 45
dias já não tinham o SKU do erro no pedido). Por isso a marca só explica o
83955 (`nf_ativa`) quando (1) algum SKU do erro ainda está nos itens do
espelho e (2) nenhuma trilha `situacao` (qualquer direção) nem `sku` é mais
nova que ela — senão é velha (`nf_vencida`) e o pedido cai em "movido à
mão" (`pos_nf_manual`). A troca do item 4c grava a trilha `sku`
(`origem='atendimento_troca'`): a marca de antes da troca envelhece sozinha.

TRÊS LEITORES, TRÊS TEXTOS:
  • `etiqueta` — o contrato de `etiqueta_fatos.ag_cancelamento_visivel`
    (a tabela-verdade de `tests/test_atendimento_etiqueta.py` não muda);
  • `texto_interno` — etiqueta, painel e lista (pode falar da Margem: é a
    equipe que lê; <= 300 caracteres);
  • `texto_ia` — o VOCABULÁRIO FECHADO que a IA pode usar (`TEXTOS_IA`),
    nunca margem, custo ou lucro. None = a IA não recebe motivo (a trava da
    Margem e a reprovação sem pessoa: "em processamento" para o comprador).
  `fala_cancelamento` = a IA e a oferta podem falar em cancelamento: só
  com decisão de pessoa (Reprovado por pessoa), pedido do comprador ou
  pedido já cancelado na plataforma, ou o sweep de NF com marca viva.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.services.bling_situacoes import SITUACAO_AGUARDANDO_CANCELAMENTO_STR

if TYPE_CHECKING:
    from app.services.atendimento.etiqueta_fatos import PedidoBling

# ── Códigos do motivo (`Motivo.codigo`) ───────────────────────────────────
SEM_ESTOQUE = "sem_estoque"
RESTRICAO_ENVIO = "restricao_envio"
MARGEM_TRAVA = "margem_trava"
MARGEM_REPROVADA = "margem_reprovada"
PEDIDO_CLIENTE = "pedido_cliente"
CANCELADO_PLATAFORMA = "cancelado_plataforma"
POS_NF_MANUAL = "pos_nf_manual"
MANUAL = "manual"
CODIGOS = (
    SEM_ESTOQUE,
    RESTRICAO_ENVIO,
    MARGEM_TRAVA,
    MARGEM_REPROVADA,
    PEDIDO_CLIENTE,
    CANCELADO_PLATAFORMA,
    POS_NF_MANUAL,
    MANUAL,
)
# NÃO é motivo do classificador: é o lado SEGURO de quem chama quando a
# classificação falhou (o contexto da IA e o painel) — sem falar em
# cancelamento e sem motivo para a IA.
DESCONHECIDO = "desconhecido"

# ── A trilha e o pino da Margem (reexportados em `etiqueta_fatos`) ───────
# `margem_audit.origem` do robô da Margem (services/margem_auto_hold.py): a
# trava interna de margem, que NÃO é cancelamento para o comprador.
ORIGEM_ROBO_MARGEM = "margens_auto"
# `margem_audit.origem` da pessoa na aba Margem (`routers/margens`).
ORIGEM_PESSOA_MARGEM = "margens"
# O pino que só a análise de margem grava (`bling_orders.status`): segurado
# para alguém decidir — também não é cancelamento.
PINO_MARGEM_PENDENTE = "Pendente"
# O pino da reprovação. Gravado por PESSOA (o Reprovar da aba Margem, com
# `bling_orders.aprovado_por` = quem clicou) é cancelamento de verdade.
PINO_MARGEM_REPROVADO = "Reprovado"
# A margem já aprovada: o que segura o pedido é outra coisa.
PINO_MARGEM_APROVADO = "Aprovado"

# ── A marca da NF (`nf_faturamento.status_faturamento`) ──────────────────
NF_SEM_ESTOQUE = "sem_estoque"
NF_RESTRICAO = "restricao"
# As marcas do sweep que põem o pedido em 83955.
MARCAS_DA_NF = frozenset({NF_SEM_ESTOQUE, NF_RESTRICAO})
# A NF já saiu (ou está saindo): o 83955 veio depois, à mão.
NF_EMITIDA = frozenset({"ok", "processando"})
# O que vem depois disto no `erro_faturamento` de 'sem_estoque' são os SKUs
# negativos, separados por ", " (`nf_auto_enfileirar`, `routers/nf.py`). A
# marca anterior a 10/08/2026 não trazia os SKUs ("… — saldo negativo").
_PREFIXO_SKUS = "saldo negativo:"

# O status do pedido NA PLATAFORMA (retrato `dados.pedido_mkt.status`: Shopee
# e ML). Só o `IN_CANCEL` da Shopee é o comprador pedindo o cancelamento. O
# `CANCELLED` da Shopee e o `cancelled` do ML são o estado FINAL, cancele
# quem cancelar (comprador, loja ou sistema — ex.: a equipe cancelou na
# Shopee o pedido sem estoque e o Bling ficou em 83955): o retrato não traz
# quem cancelou, então o texto não atribui a ninguém.
STATUS_PEDIDO_DO_COMPRADOR = frozenset({"IN_CANCEL"})
STATUS_CANCELADO_PLATAFORMA = frozenset({"CANCELLED", "CANCELED"})

# ── O vocabulário fechado da IA (`Motivo.texto_ia`) ──────────────────────
TEXTO_IA_DECIDIDO_PELA_LOJA = "cancelamento decidido pela loja"
TEXTO_IA_PEDIDO_DO_COMPRADOR = "cancelamento pedido pelo comprador"
TEXTO_IA_CANCELADO_NA_PLATAFORMA = "pedido cancelado na plataforma"
TEXTO_IA_FALTA_DE_ESTOQUE = "falta de estoque do item"
TEXTO_IA_RESTRICAO = "restrição de envio para a região"
TEXTO_IA_EM_VERIFICACAO = "em verificação pela equipe"
TEXTOS_IA = frozenset(
    {
        TEXTO_IA_DECIDIDO_PELA_LOJA,
        TEXTO_IA_PEDIDO_DO_COMPRADOR,
        TEXTO_IA_CANCELADO_NA_PLATAFORMA,
        TEXTO_IA_FALTA_DE_ESTOQUE,
        TEXTO_IA_RESTRICAO,
        TEXTO_IA_EM_VERIFICACAO,
    }
)

# O texto interno vai para a linha do tempo: == `etiqueta.MAX_MOTIVO` (sem
# importar `etiqueta` aqui; o teste confere que os dois andam juntos).
MAX_TEXTO_INTERNO = 300


@dataclass(frozen=True)
class Motivo:
    """Por que o pedido está em 83955 — o que cada leitor pode saber."""

    codigo: str
    # == `etiqueta_fatos.ag_cancelamento_visivel` (o contrato da etiqueta).
    etiqueta: bool
    # A IA e a oferta podem falar em cancelamento com o comprador.
    fala_cancelamento: bool
    # A 4b pode sugerir troca de produto (só falta de estoque).
    pode_sugerir_troca: bool
    # Etiqueta, painel e lista (<= 300). Pode falar da Margem.
    texto_interno: str
    # Vocabulário fechado (`TEXTOS_IA`), nunca "margem". None = sem motivo.
    texto_ia: str | None
    # Os SKUs em falta (só com a marca de falta de estoque VIVA): os do erro
    # da marca que AINDA estão no pedido — o item já trocado sai.
    skus: tuple[str, ...] = ()
    # A trava da Margem venceu, mas a NF também marcou falta de estoque ou
    # restrição: o cartão interno avisa (a IA nunca vê).
    conflito: str | None = None


def cortar(texto: str) -> str:
    """O texto numa linha só, com no máximo `MAX_TEXTO_INTERNO` caracteres. PURA.

    O mesmo corte de `etiqueta._cortar`: o motivo da etiqueta
    (`etiqueta_fatos._montar_fatos`) e a observação do painel usam este.
    """
    texto = " ".join(texto.split())
    return texto if len(texto) <= MAX_TEXTO_INTERNO else texto[: MAX_TEXTO_INTERNO - 1] + "…"


def _sem_caixa(texto: str | None) -> str:
    return (texto or "").strip().lower()


def skus_do_erro(erro: str | None) -> tuple[str, ...]:
    """Os SKUs em falta no `erro_faturamento` da marca 'sem_estoque'. PURA.

    "Aguardando Cancelamento — saldo negativo: dg053.sp, a001.sp" →
    ('dg053.sp', 'a001.sp'); o kit vem inteiro ("dg057.ci+a001.ci" é UM SKU).
    A marca antiga, sem os SKUs ("… — saldo negativo"), o erro de outra
    etapa e o vazio dão (). Sem repetição, na ordem do erro.
    """
    texto = erro or ""
    pos = texto.lower().find(_PREFIXO_SKUS)
    if pos < 0:
        return ()
    cauda = texto[pos + len(_PREFIXO_SKUS) :]
    return tuple(dict.fromkeys(s for s in (p.strip() for p in cauda.split(",")) if s))


def status_na_plataforma(dados: Mapping[str, Any] | None) -> str | None:
    """O status do pedido na plataforma pelo retrato da conversa (`dados.pedido_mkt.status`)."""
    retrato = dados.get("pedido_mkt") if isinstance(dados, Mapping) else None
    if not isinstance(retrato, Mapping):
        return None
    status = str(retrato.get("status") or "").strip()
    return status or None


def nf_vencida(p: PedidoBling) -> bool:
    """A marca de NF ('sem_estoque'/'restricao') é VELHA — já não explica o 83955. PURA.

    Velha (crítica A1) quando alguma trilha `situacao` (qualquer direção) ou
    `sku` da `margem_audit` é mais nova que a marca (`nf_faturamento.
    updated_at`), ou quando, na falta de estoque, nenhum SKU do erro está
    mais nos itens do espelho (o item foi trocado). Sem como provar que a
    marca é viva (sem SKUs no erro, itens desconhecidos, sem a hora da
    marca com trilha), ela é velha: o lado seguro é não falar em
    cancelamento por falta de estoque.
    """
    status = _sem_caixa(p.nf_status)
    if status not in MARCAS_DA_NF:
        return False
    trilha, marca = p.ultima_trilha_em, p.nf_marcada_em
    if trilha is not None and (marca is None or _depois(trilha, marca)):
        return True
    return status == NF_SEM_ESTOQUE and not _skus_em_falta(p)


def _skus_em_falta(p: PedidoBling) -> tuple[str, ...]:
    """Os SKUs do erro da marca que AINDA estão nos itens do espelho (sem caixa), na ordem do erro.

    O erro "dg053.sp, a001.sp" com o a001.sp já trocado à mão dá
    ('dg053.sp',): o que falta é só o que ficou no pedido.
    """
    itens = {_sem_caixa(s) for s in p.skus_itens if _sem_caixa(s)}
    return tuple(s for s in skus_do_erro(p.nf_erro) if _sem_caixa(s) in itens)


def _depois(a: datetime, b: datetime) -> bool:
    """`a` mais nova que `b`; com e sem fuso misturados, compara pelo relógio."""
    if (a.tzinfo is None) != (b.tzinfo is None):
        a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)
    return a > b


def nf_ativa(p: PedidoBling) -> str | None:
    """'sem_estoque'/'restricao' quando a marca da NF ainda explica o 83955; senão None. PURA.

    Viva = marca do sweep, sem NF nem etiqueta do pedido e não vencida
    (`nf_vencida`).
    """
    status = _sem_caixa(p.nf_status)
    if status not in MARCAS_DA_NF or p.tem_nf_ou_etiqueta or nf_vencida(p):
        return None
    return status


def _conflito(ativa: str | None, skus: tuple[str, ...]) -> str | None:
    if ativa == NF_SEM_ESTOQUE:
        return "a NF também marcou falta de estoque" + (f": {', '.join(skus)}" if skus else "")
    if ativa == NF_RESTRICAO:
        return "a NF também marcou restrição de envio"
    return None


def _trava(conflito: str | None, skus: tuple[str, ...]) -> Motivo:
    texto = "trava interna da Margem (segurado para análise): não é cancelamento"
    return Motivo(
        codigo=MARGEM_TRAVA,
        etiqueta=False,
        fala_cancelamento=False,
        pode_sugerir_troca=False,
        texto_interno=cortar(f"{texto} — {conflito}" if conflito else texto),
        texto_ia=None,
        skus=skus,
        conflito=conflito,
    )


def _texto_restricao(erro: str | None) -> str:
    erro = (erro or "").strip()
    if not erro:
        return "restrição de envio para a região"
    # "Restrição Shopee — Apple não envia pro RJ: …", "Restrição da loja: …"
    return erro if _sem_caixa(erro).startswith("restrição") else f"restrição de envio: {erro}"


def _texto_pos_nf(p: PedidoBling) -> str:
    """Por que o 83955 parece ter vindo da pessoa no Bling (o motivo não ficou registrado)."""
    status = _sem_caixa(p.nf_status)
    if status == "ok":
        detalhe = "NF já emitida"
    elif status == "processando":
        detalhe = "NF em emissão"
    elif p.tem_nf_ou_etiqueta:
        detalhe = "NF ou etiqueta já gerada"
    else:
        marca = "falta de estoque" if status == NF_SEM_ESTOQUE else "restrição de envio"
        detalhe = f"a marca de {marca} da NF é velha: o pedido mudou depois"
    return f"motivo não registrado, movido à mão no Bling ({detalhe})"


def classificar(p: PedidoBling | None, *, status_plataforma: str | None = None) -> Motivo | None:
    """O motivo do pedido em 83955 — None fora de 83955. PURA.

    `status_plataforma` = o status do pedido NA PLATAFORMA (o retrato da
    conversa, `status_na_plataforma(conversa.dados)`), quando houver.

    A primeira regra que casar vale (desenho §1.1 com a crítica A1/A2):
      1. pino 'Reprovado' gravado por PESSOA → margem_reprovada, fala em
         cancelamento ("decidido pela loja");
      2. trilha `margens_auto` (o robô segurou) → margem_trava, OCULTA — a
         não ser que a margem já esteja 'Aprovado' e a marca da NF viva: aí
         o que segura é o estoque e segue para a regra 5;
      3. pino 'Pendente' (qualquer origem) → margem_trava, OCULTA;
      4. trilha `margens` ou pino 'Reprovado' sem pessoa → margem_reprovada
         VISÍVEL, mas SEM falar em cancelamento (o robô reavalia e pode
         soltar; "marcar pendente" limpa `aprovado_por`);
      5. o comprador pediu o cancelamento na plataforma (`IN_CANCEL`) →
         pedido_cliente; o pedido já cancelado lá (`CANCELLED`, sem dizer
         por quem) → cancelado_plataforma;
      6. marca de falta de estoque viva → sem_estoque (sugere troca);
      7. marca de restrição viva → restricao_envio;
      8. NF emitida/em emissão, NF ou etiqueta do pedido, ou marca velha →
         pos_nf_manual;
      9. o resto → manual.
    Nas regras 2 a 4, a marca viva da NF vira `conflito` (cartão interno).
    """
    if p is None or (p.situacao or "").strip() != SITUACAO_AGUARDANDO_CANCELAMENTO_STR:
        return None
    ativa = nf_ativa(p)
    skus = _skus_em_falta(p) if ativa == NF_SEM_ESTOQUE else ()
    conflito = _conflito(ativa, skus)
    pino = (p.pino_margem or "").strip()
    origem = (p.origem_ag_cancelamento or "").strip()

    # 1. A pessoa reprovou na aba Margem: cancelamento de verdade.
    if pino == PINO_MARGEM_REPROVADO and p.pino_por_pessoa:
        return Motivo(
            codigo=MARGEM_REPROVADA,
            etiqueta=True,
            fala_cancelamento=True,
            pode_sugerir_troca=False,
            texto_interno="reprovado por pessoa na aba Margem: cancelamento decidido",
            texto_ia=TEXTO_IA_DECIDIDO_PELA_LOJA,
        )
    # 2. O robô da Margem segurou. Com a margem já aprovada e a falta de
    # estoque (ou a restrição) viva, quem segura é a NF.
    if origem == ORIGEM_ROBO_MARGEM and not (pino == PINO_MARGEM_APROVADO and ativa):
        return _trava(conflito, skus)
    # 3. Segurado para análise, venha de onde vier.
    if pino == PINO_MARGEM_PENDENTE:
        return _trava(conflito, skus)
    # 4. Reprovação da Margem sem decisão de pessoa registrada.
    if origem == ORIGEM_PESSOA_MARGEM or pino == PINO_MARGEM_REPROVADO:
        texto = "reprovado na Margem sem decisão de pessoa registrada: confira antes de cancelar"
        return Motivo(
            codigo=MARGEM_REPROVADA,
            etiqueta=True,
            fala_cancelamento=False,
            pode_sugerir_troca=False,
            texto_interno=cortar(f"{texto} — {conflito}" if conflito else texto),
            texto_ia=None,
            skus=skus,
            conflito=conflito,
        )
    # 5. A plataforma: o comprador pediu o cancelamento, ou o pedido já está
    # cancelado lá — sem dizer por quem (o rascunho não diz ao cliente que foi ele).
    status = (status_plataforma or "").strip().upper()
    if status in STATUS_PEDIDO_DO_COMPRADOR:
        return Motivo(
            codigo=PEDIDO_CLIENTE,
            etiqueta=True,
            fala_cancelamento=True,
            pode_sugerir_troca=False,
            texto_interno=f"o comprador pediu o cancelamento na plataforma ({status})",
            texto_ia=TEXTO_IA_PEDIDO_DO_COMPRADOR,
        )
    if status in STATUS_CANCELADO_PLATAFORMA:
        return Motivo(
            codigo=CANCELADO_PLATAFORMA,
            etiqueta=True,
            fala_cancelamento=True,
            pode_sugerir_troca=False,
            texto_interno=f"pedido já cancelado na plataforma, sem dizer por quem ({status})",
            texto_ia=TEXTO_IA_CANCELADO_NA_PLATAFORMA,
        )
    # 6. e 7. O sweep de NF, com a marca viva.
    if ativa == NF_SEM_ESTOQUE:
        return Motivo(
            codigo=SEM_ESTOQUE,
            etiqueta=True,
            fala_cancelamento=True,
            pode_sugerir_troca=True,
            texto_interno=cortar(f"falta de estoque: {', '.join(skus)}"),
            texto_ia=TEXTO_IA_FALTA_DE_ESTOQUE,
            skus=skus,
        )
    if ativa == NF_RESTRICAO:
        return Motivo(
            codigo=RESTRICAO_ENVIO,
            etiqueta=True,
            fala_cancelamento=True,
            pode_sugerir_troca=False,
            texto_interno=cortar(_texto_restricao(p.nf_erro)),
            texto_ia=TEXTO_IA_RESTRICAO,
        )
    # 8. Depois da NF (ou com a marca velha): a pessoa moveu à mão no Bling.
    if _sem_caixa(p.nf_status) in NF_EMITIDA or p.tem_nf_ou_etiqueta or nf_vencida(p):
        return Motivo(
            codigo=POS_NF_MANUAL,
            etiqueta=True,
            fala_cancelamento=False,
            pode_sugerir_troca=False,
            texto_interno=cortar(_texto_pos_nf(p)),
            texto_ia=TEXTO_IA_EM_VERIFICACAO,
        )
    # 9. Sem registro nenhum.
    return Motivo(
        codigo=MANUAL,
        etiqueta=True,
        fala_cancelamento=False,
        pode_sugerir_troca=False,
        texto_interno="motivo não registrado, movido à mão no Bling",
        texto_ia=TEXTO_IA_EM_VERIFICACAO,
    )
