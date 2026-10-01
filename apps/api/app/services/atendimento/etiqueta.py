"""Etiqueta = status atual da conversa (RF1 do PROJETO-COMUNICADOR, 01/10/2026).

Cada conversa tem UMA etiqueta, que é o status atual dela e muda sozinha:
PRÉ-VENDA (sem pedido) → PÓS-VENDA (pedido ligado) → RECLAMAÇÃO (reclamação
aberta) → PÓS-VENDA (encerrada)... Cada mudança vira uma linha em
`atendimento_etiquetas_historico` (a linha do tempo: "de Pós-venda para
Reclamação"). Filtros e contadores da lista usam a etiqueta atual.

Duas coisas abertas ao mesmo tempo: vale a mais urgente
(`PRIORIDADE_ETIQUETAS`: Reclamação > Ag. cancelamento > Devolução >
Pré-venda > Pós-venda) e as outras abertas vão para `etiquetas_secundarias`
(o indicador pequeno). A base — pré ou pós-venda — nunca é secundária.

Três partes, de propósito:
  • `calcular(fatos)` — PURA: a tabela de acontecimentos do RF1 e a
    prioridade. Testa-se sem banco.
  • `etiqueta_fatos.fatos_da_conversa` — vai ao banco buscar os fatos
    (pedido ligado, Bling 83955/83957, reclamações). Fonte nova entra LÁ.
  • `recalcular_etiqueta` / `trocar_etiqueta_manual` — gravam a etiqueta e o
    histórico. NINGUÉM grava a coluna `etiqueta` direto: mudou um fato,
    chame `recalcular_etiqueta(session, conversa, motivo=...)`.
    `recalcular_em_lote` é o mesmo para centenas de conversas (o cron
    `etiqueta_cron.atendimento_etiquetas` e o preenchimento), com os fatos
    em lote (`etiqueta_fatos.fatos_em_lote`).

Quem chama o recálculo (01/10/2026): o gravar (conversa nova, pedido ou
reclamação do pack mudou na leitura, mensagem nova em conversa ainda sem
etiqueta), o cron (o Bling, a trilha da Margem e as reclamações mudam sem
passar pelo sync), a leitura das reclamações (frente A) e a troca à mão.

Troca à mão (RF1: "o atendente pode trocar a etiqueta à mão; o próximo
acontecimento automático volta a valer"): a troca liga `etiqueta_manual` e
guarda, em `etiqueta_automatica`, o que o motor dizia naquele momento. Enquanto
o motor continuar dizendo o mesmo, a troca vale (o recálculo do cron não a
desfaz); quando o que o motor calcula MUDAR (a reclamação abriu, o pedido saiu
de Ag. cancelamento), o automático volta a valer. Trocar para a mesma etiqueta
que o motor dá = voltar ao automático.

Nada aqui commita (quem chama é dono da transação), nada sai para a
plataforma e nada escreve no Bling.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NamedTuple
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoConversa, AtendimentoEtiquetaHistorico
from app.services.atendimento.constantes import (
    ETIQUETA_AG_CANCELAMENTO,
    ETIQUETA_DEVOLUCAO,
    ETIQUETA_POS_VENDA,
    ETIQUETA_PRE_VENDA,
    ETIQUETA_RECLAMACAO,
    ETIQUETAS,
    ETIQUETAS_BASE,
    PRIORIDADE_ETIQUETAS,
    ROTULO_ETIQUETA,
    rotulo_etiqueta,
)

logger = structlog.get_logger()

# O vocabulário mora em `constantes` (um lugar só); daqui sai o contrato com
# o outro dev (`from ...etiqueta import PRIORIDADE_ETIQUETAS`).
__all__ = [
    "ETIQUETAS",
    "ETIQUETAS_BASE",
    "ETIQUETA_AG_CANCELAMENTO",
    "ETIQUETA_DEVOLUCAO",
    "ETIQUETA_POS_VENDA",
    "ETIQUETA_PRE_VENDA",
    "ETIQUETA_RECLAMACAO",
    "PRIORIDADE_ETIQUETAS",
    "ROTULO_ETIQUETA",
    "Calculo",
    "FatosEtiqueta",
    "aplicar",
    "calcular",
    "calcular_em_lote",
    "historico_da_conversa",
    "recalcular_em_lote",
    "recalcular_etiqueta",
    "rotulo_etiqueta",
    "secundarias_exibidas",
    "trocar_etiqueta_manual",
]

# O motivo vai para a linha do tempo: uma frase, não um relatório.
MAX_MOTIVO = 300

# Quando a etiqueta urgente SAI (o fato acabou), é isto que a linha do tempo
# conta — "Pós-venda: pedido X ligado" não explica a volta.
_SAIDA = {
    ETIQUETA_RECLAMACAO: "reclamação encerrada",
    ETIQUETA_DEVOLUCAO: "devolução encerrada",
    ETIQUETA_AG_CANCELAMENTO: "pedido saiu de Aguardando Cancelamento",
}


@dataclass(frozen=True)
class FatosEtiqueta:
    """O que está aberto na conversa AGORA — cada fato com o seu motivo.

    `motivo_*` é texto de operação (ids, situação do Bling, plataforma),
    nunca texto do comprador: vai para a linha do tempo. Quem preenche é
    `etiqueta_fatos.fatos_da_conversa`.
    """

    # Depois da compra (pedido ligado, ou canal que é sempre pós-venda).
    tem_pedido: bool = False
    motivo_pedido: str | None = None
    # Reclamação/mediação da plataforma aberta.
    reclamacao_aberta: bool = False
    motivo_reclamacao: str | None = None
    # Devolução aberta (plataforma ou Bling em Aguardando Devolução).
    devolucao_aberta: bool = False
    motivo_devolucao: str | None = None
    # Bling em Aguardando Cancelamento, FORA a trava do robô da Margem
    # (`etiqueta_fatos.ag_cancelamento_visivel`).
    ag_cancelamento: bool = False
    motivo_ag_cancelamento: str | None = None
    # O nº do pedido no Bling, quando achado (só informativo).
    numero_bling: str | None = None


class Calculo(NamedTuple):
    """O que o motor diz: a etiqueta, as outras abertas e o porquê."""

    etiqueta: str
    # Da mais urgente para a menos; nunca traz a de base nem a própria etiqueta.
    secundarias: list[str]
    motivo: str


def _ordem(etiqueta: str) -> int:
    try:
        return PRIORIDADE_ETIQUETAS.index(etiqueta)
    except ValueError:
        return len(PRIORIDADE_ETIQUETAS)


def _abertas(fatos: FatosEtiqueta) -> list[tuple[str, str]]:
    """As etiquetas URGENTES abertas, com o motivo, da mais urgente para a menos."""
    abertas: list[tuple[str, str]] = []
    if fatos.reclamacao_aberta:
        abertas.append(
            (ETIQUETA_RECLAMACAO, fatos.motivo_reclamacao or "reclamação aberta na plataforma")
        )
    if fatos.ag_cancelamento:
        abertas.append(
            (
                ETIQUETA_AG_CANCELAMENTO,
                fatos.motivo_ag_cancelamento or "pedido em Aguardando Cancelamento no Bling",
            )
        )
    if fatos.devolucao_aberta:
        abertas.append((ETIQUETA_DEVOLUCAO, fatos.motivo_devolucao or "devolução aberta"))
    return sorted(abertas, key=lambda par: _ordem(par[0]))


def calcular(fatos: FatosEtiqueta) -> Calculo:
    """A etiqueta que os fatos dão. PURA — a tabela de acontecimentos do RF1.

        sem pedido                         → PRÉ-VENDA
        pedido ligado                      → PÓS-VENDA
        reclamação/mediação aberta         → RECLAMAÇÃO
        devolução aberta                   → DEVOLUÇÃO
        Bling em Ag. cancelamento          → AG. CANCELAMENTO
        o que estava aberto acabou         → volta à base (pré/pós-venda)

    Várias abertas: a mais urgente vale, as outras vão para `secundarias`.
    """
    abertas = _abertas(fatos)
    if abertas:
        principal, motivo = abertas[0]
        return Calculo(principal, [e for e, _ in abertas[1:]], motivo)
    if fatos.tem_pedido:
        return Calculo(ETIQUETA_POS_VENDA, [], fatos.motivo_pedido or "pedido ligado")
    return Calculo(ETIQUETA_PRE_VENDA, [], fatos.motivo_pedido or "sem pedido ligado")


def secundarias_exibidas(calculo: Calculo, exibida: str | None) -> list[str]:
    """O indicador pequeno ao lado da etiqueta EXIBIDA.

    No automático é `calculo.secundarias`. Com troca à mão, a etiqueta que o
    motor daria também entra (a pessoa pôs Pós-venda com a reclamação ainda
    aberta: o indicador mostra a Reclamação). A base nunca entra.
    """
    abertas = [calculo.etiqueta, *calculo.secundarias]
    vistas: list[str] = []
    for e in sorted(abertas, key=_ordem):
        if e in ETIQUETAS_BASE or e == exibida or e in vistas:
            continue
        vistas.append(e)
    return vistas


def _agora() -> datetime:
    return datetime.now(UTC)


def _cortar(texto: str) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= MAX_MOTIVO else texto[: MAX_MOTIVO - 1] + "…"


def _texto_motivo(
    acontecimento: str | None, de: str | None, calculo: Calculo, *, era_manual: bool = False
) -> str:
    """'Reclamação 5582543195 aberta no ML (leitura das reclamações)'.

    O porquê vem dos FATOS (o motor sabe o que abriu); o acontecimento que
    disparou o recálculo vai entre parênteses. Na volta à base, o que conta
    é o que acabou ("reclamação encerrada"), não "pedido ligado". Saindo de
    uma troca à mão, nada "encerrou": a etiqueta de antes era da pessoa.
    """
    partes: list[str] = ["Volta ao automático"] if era_manual else []
    abertas = {calculo.etiqueta, *calculo.secundarias}
    saida = _SAIDA.get(de or "") if de not in abertas and not era_manual else None
    if saida:
        partes.append(saida[0].upper() + saida[1:])
    if not (saida and calculo.etiqueta in ETIQUETAS_BASE):
        motivo = calculo.motivo.strip()
        partes.append(motivo[0].upper() + motivo[1:] if motivo else motivo)
    texto = " · ".join(p for p in partes if p)
    acontecimento = (acontecimento or "").strip()
    if acontecimento:
        texto = f"{texto} ({acontecimento})" if texto else acontecimento
    return _cortar(texto)


def _gravar_secundarias(conversa: AtendimentoConversa, secundarias: list[str]) -> None:
    # Lista NOVA (o JSONB só vira UPDATE quando o atributo é reatribuído) e
    # só quando mudou: recálculo sem novidade não suja a linha.
    if list(conversa.etiquetas_secundarias or []) != secundarias:
        conversa.etiquetas_secundarias = list(secundarias)


def aplicar(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    calculo: Calculo,
    *,
    motivo: str | None,
    agora: datetime | None = None,
) -> bool:
    """Grava na conversa o que o motor calculou. Sem leitura de banco; não commita.

    Respeita a troca à mão enquanto o que o motor calcula não mudar. True =
    a etiqueta EXIBIDA mudou (e, se havia uma antes, o histórico ganhou a
    linha). A primeira classificação (de NULL para alguma) não vira linha:
    não é mudança.
    """
    agora = agora or _agora()
    anterior_auto = conversa.etiqueta_automatica
    conversa.etiqueta_automatica = calculo.etiqueta
    era_manual = bool(conversa.etiqueta_manual and conversa.etiqueta)
    if era_manual:
        if anterior_auto is None or anterior_auto == calculo.etiqueta:
            # Nada aconteceu desde a troca à mão: ela continua valendo, e o
            # indicador acompanha o que está aberto.
            _gravar_secundarias(conversa, secundarias_exibidas(calculo, conversa.etiqueta))
            return False
        # Aconteceu algo: o próximo acontecimento automático volta a valer.
        conversa.etiqueta_manual = False
    de = conversa.etiqueta
    _gravar_secundarias(conversa, list(calculo.secundarias))
    if de == calculo.etiqueta:
        return False
    conversa.etiqueta = calculo.etiqueta
    conversa.etiqueta_desde = agora
    if de is not None:
        session.add(
            AtendimentoEtiquetaHistorico(
                conversa_id=conversa.id,
                de=de,
                para=calculo.etiqueta,
                motivo=_texto_motivo(motivo, de, calculo, era_manual=era_manual),
                por_user_id=None,
                em=agora,
            )
        )
    return True


async def _calcular_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa
) -> Calculo | None:
    """Fatos do banco → cálculo. None = não deu para ler (a etiqueta fica como está)."""
    # Importado na hora: `etiqueta_fatos` importa `FatosEtiqueta` daqui.
    from app.services.atendimento import etiqueta_fatos

    # O flush FORA do savepoint: um erro do que quem chamou deixou pendente é
    # dele e tem de subir — não pode ser engolido como "fato não lido".
    await session.flush()
    try:
        async with session.begin_nested():
            fatos = await etiqueta_fatos.fatos_da_conversa(session, conversa)
    except Exception as e:  # noqa: BLE001 — um fato não lido nunca rebaixa a etiqueta
        logger.warning(
            "atendimento_etiqueta_fatos_falhou",
            conversa_id=str(conversa.id),
            err=type(e).__name__,
        )
        return None
    return calcular(fatos)


async def recalcular_etiqueta(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    *,
    motivo: str | None,
    agora: datetime | None = None,
) -> bool:
    """Recalcula a etiqueta da conversa pelos fatos de agora e grava. Não commita.

    `motivo` = o ACONTECIMENTO que disparou ("leitura das reclamações do ML",
    "pedido mudou de situação no Bling", "cron"): vai entre parênteses na
    linha do tempo, depois do porquê que o motor tira dos fatos. True = a
    etiqueta exibida mudou. Se os fatos não puderem ser lidos, nada muda.
    """
    calculo = await _calcular_da_conversa(session, conversa)
    if calculo is None:
        return False
    return aplicar(session, conversa, calculo, motivo=motivo, agora=agora)


async def calcular_em_lote(
    session: AsyncSession, conversas: Sequence[AtendimentoConversa]
) -> dict[UUID, Calculo] | None:
    """O que o motor diz para VÁRIAS conversas (os fatos em 3 ou 4 consultas). Não grava.

    None = os fatos não puderam ser lidos (erro de banco, engolido num
    SAVEPOINT): quem chama deixa as etiquetas como estão. É o que o
    preenchimento `--seco` usa para contar sem gravar.
    """
    from app.services.atendimento import etiqueta_fatos

    if not conversas:
        return {}
    await session.flush()
    try:
        async with session.begin_nested():
            fatos = await etiqueta_fatos.fatos_em_lote(session, conversas)
    except Exception as e:  # noqa: BLE001 — um fato não lido nunca rebaixa a etiqueta
        logger.warning(
            "atendimento_etiqueta_lote_falhou", conversas=len(conversas), err=type(e).__name__
        )
        return None
    return {cid: calcular(f) for cid, f in fatos.items()}


async def recalcular_em_lote(
    session: AsyncSession,
    conversas: Sequence[AtendimentoConversa],
    *,
    motivo: str | None,
    agora: datetime | None = None,
) -> int | None:
    """`recalcular_etiqueta` para várias conversas de uma vez (o cron). Não commita.

    Mesma regra, mesma troca à mão, mesmo histórico — só os fatos é que vêm
    em lote. Devolve quantas etiquetas EXIBIDAS mudaram; None = os fatos não
    puderam ser lidos e nada mudou.
    """
    calculos = await calcular_em_lote(session, conversas)
    if calculos is None:
        return None
    agora = agora or _agora()
    mudaram = 0
    for conversa in conversas:
        calculo = calculos.get(conversa.id)
        if calculo is not None and aplicar(session, conversa, calculo, motivo=motivo, agora=agora):
            mudaram += 1
    return mudaram


async def trocar_etiqueta_manual(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    etiqueta: str,
    *,
    user_id: UUID | None,
    motivo: str | None = None,
    agora: datetime | None = None,
) -> bool:
    """O atendente troca a etiqueta à mão (fica no histórico com o nome dele). Não commita.

    Vale até o próximo acontecimento automático (ver o topo do módulo).
    Escolher a mesma etiqueta que o motor dá = voltar ao automático. True = a
    etiqueta exibida mudou. `ValueError` para etiqueta desconhecida.
    """
    if etiqueta not in ETIQUETAS:
        raise ValueError(f"etiqueta desconhecida: {str(etiqueta)[:40]}")
    agora = agora or _agora()
    # O que o motor diz AGORA é a referência da troca: se isso mudar depois,
    # o automático volta a valer.
    calculo = await _calcular_da_conversa(session, conversa)
    if calculo is not None:
        conversa.etiqueta_automatica = calculo.etiqueta
    automatica = conversa.etiqueta_automatica
    manual = automatica is None or etiqueta != automatica
    de = conversa.etiqueta
    if calculo is not None:
        _gravar_secundarias(conversa, secundarias_exibidas(calculo, etiqueta))
    if de == etiqueta:
        conversa.etiqueta_manual = manual
        return False
    conversa.etiqueta = etiqueta
    conversa.etiqueta_manual = manual
    conversa.etiqueta_desde = agora
    texto = "Trocada à mão" if manual else "Trocada à mão (de volta ao automático)"
    extra = (motivo or "").strip()
    session.add(
        AtendimentoEtiquetaHistorico(
            conversa_id=conversa.id,
            de=de,
            para=etiqueta,
            motivo=_cortar(f"{texto}: {extra}" if extra else texto),
            por_user_id=user_id,
            em=agora,
        )
    )
    return True


async def historico_da_conversa(
    session: AsyncSession, conversa_id: UUID, *, limite: int = 100
) -> list[AtendimentoEtiquetaHistorico]:
    """As mudanças de etiqueta da conversa, da mais antiga para a mais nova (a linha do tempo).

    As `limite` MAIS RECENTES: numa conversa longa, a linha do tempo mostra
    o que mudou por último, não o começo da história.
    """
    recentes = list(
        (
            await session.execute(
                select(AtendimentoEtiquetaHistorico)
                .where(AtendimentoEtiquetaHistorico.conversa_id == conversa_id)
                .order_by(
                    AtendimentoEtiquetaHistorico.em.desc(),
                    AtendimentoEtiquetaHistorico.id.desc(),
                )
                .limit(limite)
            )
        )
        .scalars()
        .all()
    )
    return recentes[::-1]
