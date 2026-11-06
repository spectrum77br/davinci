"""Atendimento do Painel de Garantia (04/11/2026) — o que está EM ATENDIMENTO.

Pedido do Eduardo (04/11): "vamos dividir o painel garantia em 3 partes: …
atendimento - só o que realmente está em atendimento no momento". A garantia
está em atendimento quando:
  (a) PEDIU a garantia (`garantia_pedido.filtro_pedido`) e não foi descartada;
  (b) o desfecho ainda não é um fim — fim = perdido, consertado, devolvido
      com a condição lançada, ou o reembolso pago ao cliente (o Reembolso
      "Cliente" ou a plataforma pagou, sem o aparelho na tela Devoluções);
  (c) alguma coisa está em aberto. Vale o 1º motivo, na ordem de
      MOTIVOS_DO_ATENDIMENTO:
        1. no técnico — a devolução em Manutenção, sem a volta (vencido: o
           prazo de 30 dias do técnico já passou, o mesmo do selo da lista);
        2. chegou sem a condição — a devolução lançada sem a condição;
        3. voltando — a devolução aceita na plataforma, o aparelho ainda não
           lançado na tela Devoluções;
        4. a plataforma decidindo — reclamação ou mediação aberta do pedido;
        5. o pedido de garantia sem resposta — a loja ainda não respondeu o
           pedido (a mensagem do pedido ou o formulário do SAC do site).
           Eduardo (04/11): "atendimento é tudo o que está fazendo realmente
           algum caso de garantia" — a conversa comum esperando a loja (um
           "obrigado", uma foto) não entra.
Termina sozinho: o aparelho consertado ou perdido, a condição lançada, o
reembolso pago, a reclamação encerrada, o pedido respondido.

Garantia × SAC (06/11/2026, especificação "Painel de Garantia: alterações"): "a
aba lista SOMENTE os casos com Garantia acionada. Isso substitui a regra atual
(quem pediu garantia e ainda tem algo em aberto)". A base passa a ser o caso
do SAC confirmado e ainda não encerrado (`garantia_sac`); o motivo acima vira
só o SELO do caso — o caso fica na aba até a pessoa encerrar ou até terminar
sozinho (`garantia_sac.encerrar_terminados`, que grava o encerramento). "Os
casos abertos pela regra antiga (54 em Celular e 8 em Eletro em 05/11) não são
migrados ... e saem da aba. Os dados não são apagados." O pedido sem resposta
do caso é o formulário do SAC sem resposta da loja na conversa dele.

O desfecho do aparelho é o da lista (`garantia_resumo.desfechos`: a tela
Devoluções, o Reembolso e os casos da plataforma) — lido aqui e reaproveitado
no selo dela (`LeituraDoAtendimento`). Só leitura.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.atendimento import AtendimentoConversa, AtendimentoMensagem
from app.models.garantia import Garantia, GarantiaSac
from app.services import garantia_pedido, garantia_resumo, garantia_sac
from app.services.atendimento.constantes import AUTOR_LOJA, TIPO_NOTA

# A ordem de quem vale quando há mais de um motivo em aberto.
MOTIVOS_DO_ATENDIMENTO = (
    "no_tecnico",
    "sem_condicao",
    "voltando",
    "plataforma_decidindo",
    "pedido_sem_resposta",
)
ROTULOS = {
    "no_tecnico": "No técnico",
    "sem_condicao": "Chegou — falta a condição",
    "voltando": "Voltando",
    "plataforma_decidindo": "Plataforma decidindo",
    "pedido_sem_resposta": "Pedido de garantia sem resposta",
}
ROTULO_NO_TECNICO_VENCIDO = "No técnico · vencido"
_LOTE = 1000
_SEM_DATA = datetime.max.replace(tzinfo=UTC)


@dataclass(frozen=True)
class EmAtendimento:
    """Por que a garantia está em atendimento agora."""

    motivo: str
    rotulo: str
    desde: datetime | None = None  # quando o motivo começou
    vencido: bool = False


@dataclass(frozen=True)
class CasoAberto:
    """O caso do SAC confirmado e ainda aberto de uma garantia (o mais antigo,
    se ela tiver mais de um)."""

    sac_id: int
    recebido_em: datetime
    confirmado_em: datetime
    conversa_id: UUID


@dataclass(frozen=True)
class LeituraDoAtendimento:
    """O que `em_atendimento` leu: os casos abertos (`casos` — a aba
    Atendimento e a contagem dela), o selo de cada um (`em_aberto`, o motivo
    em aberto, quando há) e o desfecho do aparelho das garantias lidas
    (`lidas` = as dos casos, entre os filtros; `desfechos` = só as que têm
    alguma coisa). A lista reaproveita os desfechos no selo do aparelho: a
    tela Devoluções, o Reembolso e os casos da plataforma são lidos uma vez só
    por lista."""

    em_aberto: dict[int, EmAtendimento]
    desfechos: dict[int, garantia_resumo.Desfecho]
    lidas: frozenset[int]
    casos: dict[int, CasoAberto] = field(default_factory=dict)


def terminou(f: garantia_resumo.Desfecho) -> bool:
    """O desfecho já é um fim: perdido, consertado, devolvido com a condição
    lançada ou o reembolso pago ao cliente (o selo "Reembolso ao cliente": o
    dinheiro voltou e nada do aparelho na tela Devoluções). O aparelho que
    está no técnico ou chegou sem a condição continua em atendimento mesmo com
    o cliente reembolsado — o técnico e a condição ainda são da loja. PURA."""
    return f.situacao in ("perdido", "consertado", "reembolso") or (
        f.situacao == "devolvido" and bool(f.condicao)
    )


def motivo_do_atendimento(
    f: garantia_resumo.Desfecho | None, pedido_sem_resposta_desde: datetime | None = None
) -> EmAtendimento | None:
    """O motivo em aberto (o 1º, na ordem de MOTIVOS_DO_ATENDIMENTO) da garantia
    que pediu: `f` = o desfecho do aparelho (None = nada na tela Devoluções,
    no Reembolso nem na plataforma); `pedido_sem_resposta_desde` = quando o
    cliente pediu, se a loja ainda não respondeu (`pedido_sem_resposta`).
    None = não está em atendimento. PURA."""
    if f is not None and terminou(f):
        return None
    abertos: dict[str, tuple[datetime | None, bool]] = {}
    if f is not None:
        if f.situacao == "no_tecnico":
            abertos["no_tecnico"] = (f.desde, f.vencido)
        elif f.situacao == "devolvido":
            abertos["sem_condicao"] = (f.desde, False)
        elif f.situacao == "a_caminho":
            abertos["voltando"] = (f.desde, False)
        if f.decidindo_desde is not None:
            abertos["plataforma_decidindo"] = (f.decidindo_desde, False)
    if pedido_sem_resposta_desde is not None:
        abertos["pedido_sem_resposta"] = (pedido_sem_resposta_desde, False)
    for motivo in MOTIVOS_DO_ATENDIMENTO:
        if motivo in abertos:
            desde, vencido = abertos[motivo]
            rotulo = ROTULO_NO_TECNICO_VENCIDO if vencido else ROTULOS[motivo]
            return EmAtendimento(motivo, rotulo, desde, vencido)
    return None


def urgencia(a: EmAtendimento, garantia_id: int = 0) -> tuple:
    """A ordem da aba Atendimento: o vencido primeiro, depois o mais antigo."""
    desde = a.desde
    if desde is not None and desde.tzinfo is None:
        desde = desde.replace(tzinfo=UTC)
    return (not a.vencido, desde is None, desde or _SEM_DATA, garantia_id)


def _uuid(ref: str | None) -> UUID | None:
    try:
        return UUID(str(ref))
    except (TypeError, ValueError):
        return None


async def pedido_sem_resposta(session: AsyncSession, registros) -> dict[int, datetime]:
    """garantia → quando o cliente PEDIU a garantia, se a loja ainda não
    respondeu depois disso. O pedido por mensagem guarda a mensagem
    (`solicitacao_ref`); o do SAC do site, o protocolo (a conversa
    `mail-protocolo:<protocolo>`, desde `solicitada_em`). Resposta = mensagem
    da loja (nota interna não conta) na mesma conversa, depois do pedido."""
    m0, m1 = aliased(AtendimentoMensagem), aliased(AtendimentoMensagem)
    q0 = func.coalesce(m0.enviada_em, m0.created_at)
    q1 = func.coalesce(m1.enviada_em, m1.created_at)
    da_loja = (m1.autor == AUTOR_LOJA, m1.tipo.is_distinct_from(TIPO_NOTA))
    saida: dict[int, datetime] = {}

    por_mensagem: dict[UUID, int] = {}
    por_protocolo: dict[str, tuple[int, datetime]] = {}
    for r in registros:
        if r.solicitacao_origem == garantia_pedido.ORIGEM_MENSAGEM:
            if (mid := _uuid(r.solicitacao_ref)) is not None:
                por_mensagem[mid] = r.id
        elif (
            r.solicitacao_origem == garantia_pedido.ORIGEM_SAC
            and r.solicitacao_ref
            and r.solicitada_em is not None
        ):
            externo = garantia_sac.PREFIXO_PROTOCOLO + r.solicitacao_ref
            por_protocolo[externo] = (r.id, r.solicitada_em)

    mids = list(por_mensagem)
    for i in range(0, len(mids), _LOTE):
        respondeu = (
            select(m1.id).where(m1.conversa_id == m0.conversa_id, *da_loja, q1 > q0).exists()
        )
        q = select(m0.id, q0).where(m0.id.in_(mids[i : i + _LOTE]), ~respondeu)
        for mid, quando in (await session.execute(q)).all():
            saida[por_mensagem[mid]] = quando

    if por_protocolo:
        c = AtendimentoConversa
        q = (
            select(c.externo_id, func.max(q1))
            .select_from(c)
            .outerjoin(m1, (m1.conversa_id == c.id) & da_loja[0] & da_loja[1])
            .where(c.externo_id.in_(list(por_protocolo)))
            .group_by(c.externo_id)
        )
        for externo, ultima_da_loja in (await session.execute(q)).all():
            gid, pediu_em = por_protocolo[externo]
            if ultima_da_loja is None or ultima_da_loja <= pediu_em:
                saida[gid] = pediu_em
    return saida


async def casos_abertos(session: AsyncSession, filtros: list) -> dict[int, CasoAberto]:
    """garantia → o caso do SAC confirmado e não encerrado (o mais antigo — a
    mesma escolha da linha e do Resumo do SAC, `garantia_sac.ordem_dos_casos`),
    entre as garantias vivas dos `filtros` (o escopo da pessoa)."""
    s, g = GarantiaSac, Garantia
    linhas = (
        await session.execute(
            select(s.garantia_id, s.id, s.recebido_em, s.confirmado_em, s.conversa_id)
            .join(g, g.id == s.garantia_id)
            .where(
                *filtros,
                g.descartada_em.is_(None),
                s.confirmado_em.is_not(None),
                s.encerrado_em.is_(None),
            )
            .order_by(*garantia_sac.ordem_dos_casos(s))
        )
    ).all()
    saida: dict[int, CasoAberto] = {}
    for gid, sid, recebido, confirmado, conversa in linhas:
        saida.setdefault(gid, CasoAberto(sid, recebido, confirmado, conversa))
    return saida


async def sac_sem_resposta(
    session: AsyncSession, casos: dict[int, CasoAberto]
) -> dict[int, datetime]:
    """garantia → quando o formulário chegou, se a loja ainda não respondeu na
    conversa do SAC depois dele (nota interna não conta)."""
    if not casos:
        return {}
    m = AtendimentoMensagem
    quando = func.coalesce(m.enviada_em, m.created_at)
    ultima = dict(
        (
            await session.execute(
                select(m.conversa_id, func.max(quando))
                .where(
                    m.conversa_id.in_({c.conversa_id for c in casos.values()}),
                    m.autor == AUTOR_LOJA,
                    m.tipo.is_distinct_from(TIPO_NOTA),
                )
                .group_by(m.conversa_id)
            )
        ).all()
    )
    saida: dict[int, datetime] = {}
    for gid, c in casos.items():
        respondeu = ultima.get(c.conversa_id)
        if respondeu is None or respondeu <= c.recebido_em:
            saida[gid] = c.recebido_em
    return saida


async def em_atendimento(
    session: AsyncSession,
    filtros: list,
    dia: date,
    *,
    tabela: dict[str, Any] | None = None,
) -> LeituraDoAtendimento:
    """Os casos em atendimento agora entre as `filtros` (o escopo da pessoa):
    as garantias com o SAC confirmado e não encerrado — com o selo (o motivo em
    aberto, quando há) e o desfecho de cada uma, que a lista reaproveita
    (`LeituraDoAtendimento`). `tabela` = a Tabela de Preços já lida
    (`garantia_resumo.mapa_das_linhas`)."""
    casos = await casos_abertos(session, filtros)
    if not casos:
        return LeituraDoAtendimento({}, {}, frozenset(), {})
    g = Garantia
    registros = (
        await session.execute(
            select(g.id, g.pedido_bling, g.pedido_marketplace, g.itens)
            .where(g.id.in_(list(casos)))
            .order_by(g.id)
        )
    ).all()
    desfechos = await garantia_resumo.desfechos(session, registros, dia, tabela=tabela)
    sem_resposta = await sac_sem_resposta(session, casos)
    em_aberto: dict[int, EmAtendimento] = {}
    for r in registros:
        if (a := motivo_do_atendimento(desfechos.get(r.id), sem_resposta.get(r.id))) is not None:
            em_aberto[r.id] = a
    return LeituraDoAtendimento(em_aberto, desfechos, frozenset(r.id for r in registros), casos)
