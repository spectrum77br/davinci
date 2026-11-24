"""Investimento da aba Faturamento lido das coletas da Conferência (M-36,
24/11/2026, prévia aprovada pelo dono em 24/11).

Pedido M-36: a coluna Investimento = afiliados + Ads de cada conta, com os
MESMOS números do Marketing › Conferência (a linha Investimento de lá). Aqui
fica a leitura das coletas; quem liga a conta da Conferência à loja é
`faturamento_contas.contas_conferencia_das_lojas` e quem junta com o Ads por
dia do Marketing é `faturamento_contas.investimento_das_lojas`.

Regras (os números do pedido entre parênteses):

1. Números: os `dados` crus da coleta, semana a semana — afiliados =
   `afiliados.comissao`, Ads = `ads.gasto` —, o TOTAL da conta. Na tela da
   Conferência a conta de Celular aparece em duas linhas (Celular e Eletro)
   que somam exatamente esse total (calculo.montar_relatorio: Celular = total
   − eletro), então somar as duas linhas = ler o total (item 2).
2. Métrica com estado na plataforma (plataformas.ESTADOS: afiliados do ML "não
   coletado", Ads da Amazon "aguardando acesso"…) não tem número, como na
   tela. Quando o estado sair, o número entra sozinho, sem pedido novo (item 4).
3. Quais coletas: só rodada `semanal` que não foi cancelada (a `parcial` do
   "Gerar agora" e as canceladas ficam de fora) e toda coleta já encerrada da
   conta (fora da fila: nem `pendente` nem `coletando`). Só `ok`/`parcial` com
   `dados` têm número (as mesmas que a Conferência mostra).
4. Cada DIA de cada conta conta uma vez só, da rodada mais recente em que a
   conta tem coleta encerrada (item 5: a semana aparece em até 8 rodadas —
   S1..S4, segunda e quarta). Por dia e não por semana porque até 30/10/2026
   as semanas eram segunda–domingo e depois viraram domingo–sábado: as duas
   cobrem os mesmos dias e somar semana a semana contaria em dobro.
   Correção de 24/11/2026 (M-36, revisão): a rodada mais recente manda MESMO
   sem número. Coleta que falhou nela (deslogada, bloqueada, perfil Firefox,
   expirada…), seção que veio null ou semana que não veio nos `dados` deixam
   aquela semana sem número ("—", como a tela da Conferência mostra) e NÃO
   voltam para a rodada anterior: senão o total da Shopee deixa de ser igual
   ao da tela (item 1) e a conta deslogada aparece com número velho (item 4,
   o exemplo do victor mei). Rodada em que a conta não tem coleta nenhuma, ou
   em que a coleta ainda está na fila, não decide nada (vale a anterior).
5. Período (item 5): semana inteira dentro do período entra inteira; semana
   que cruza a borda entra pelos dias. Com o dia a dia da coleta (campo `dias`
   de cada semana, item 6 — [{"dia", "afiliados", "ads"}], null = a Shopee
   não devolveu o dia) quando ele tem número em TODOS os dias da semana que
   caem no período: período exato. Sem isso (semanas antigas, dia faltando): o
   valor da semana × dias dentro ÷ dias da semana. A semana corrente não está
   em rodada nenhuma (S1 é sempre a semana fechada): entra quando fechar.
6. Sem coleta com número no período: None ("—" na tela), nunca zero.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import case, column, func, literal, select, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conferencia_shopee import ConferenciaShopeeColeta, ConferenciaShopeeExecucao
from app.services.conferencia_shopee import fila, plataformas

# Os dois pedaços do Investimento: a chave no `dias` (item 6), a métrica da
# Conferência (para o estado da plataforma) e onde está o total da semana.
COMPONENTES = ("afiliados", "ads")
_METRICA = {"afiliados": "invest_afiliados", "ads": "invest_ads"}
_TOTAL_DA_SEMANA = {"afiliados": ("afiliados", "comissao"), "ads": ("ads", "gasto")}
# As coletas com números (calculo.STATUS_COM_DADOS).
_STATUS_COM_NUMERO = ("ok", "parcial")
# Coleta ainda na fila (pendente/coletando): a rodada não decidiu nada dela.
_NA_FILA = fila.NA_FILA
_CENTAVO = Decimal("0.01")


@dataclass(frozen=True)
class ContaConferencia:
    """Uma conta da Conferência (`conferencia_shopee_conta`) ligada a uma loja."""

    id: UUID
    plataforma: str


@dataclass
class GastoConferencia:
    """O que a Conferência tem da loja no período. `afiliados`/`ads` None =
    nenhuma coleta com número no período. `desde`: o 1º dia que TODAS as
    contas da loja com coleta têm (o período começar antes = parcial).
    `ate`: o último dia coberto (o da conta mais atrasada)."""

    afiliados: Decimal | None
    ads: Decimal | None
    desde: date | None
    ate: date | None


# ───────────────────────────────────────────────────────────── números


def _valor(bruto: Any) -> Decimal | None:
    """Número do JSON da coleta → Decimal; o resto (texto torto, bool, NaN) →
    None. Float pela forma curta (5711.94 e não 5711.9399…), como o
    calculo.meio_para_cima."""
    if bruto is None or isinstance(bruto, bool):
        return None
    if isinstance(bruto, int):
        return Decimal(bruto)
    try:
        f = float(bruto)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    try:
        return Decimal(repr(f))
    except InvalidOperation:
        return None


def _data(bruto: Any) -> date | None:
    if isinstance(bruto, str) and len(bruto) >= 10:
        try:
            return date.fromisoformat(bruto[:10])
        except ValueError:
            return None
    return None


def _dias_entre(inicio: date, fim: date) -> list[date]:
    return [inicio + timedelta(days=i) for i in range((fim - inicio).days + 1)]


def _dia_a_dia(dias: Any, componente: str) -> dict[date, Decimal | None]:
    """O `dias` de uma semana (item 6) → dia → valor do componente (None = a
    Shopee não devolveu o dia). Item torto é ignorado."""
    out: dict[date, Decimal | None] = {}
    if not isinstance(dias, list):
        return out
    for item in dias:
        if not isinstance(item, Mapping):
            continue
        dia = _data(item.get("dia"))
        if dia is not None:
            out[dia] = _valor(item.get(componente))
    return out


@dataclass
class _Semana:
    """Uma semana dos `dados` de uma coleta."""

    inicio: date
    fim: date
    totais: dict[str, Decimal | None]
    dias: Any = None


def valor_nos_dias(
    semana: _Semana, componente: str, dias: Sequence[date], periodo: Sequence[date]
) -> Decimal | None:
    """Quanto a semana vale nos `dias` (os dias dela que esta leitura vai
    cobrir, todos dentro do período). `periodo` = os dias da semana que caem
    no período: o dia a dia (item 6) só vale com número em TODOS eles; senão
    o total da semana × dias ÷ dias da semana. Sem número: None."""
    if not dias:
        return None
    por_dia = _dia_a_dia(semana.dias, componente)
    if periodo and all(por_dia.get(d) is not None for d in periodo):
        return sum((por_dia[d] for d in dias), Decimal("0"))
    total = semana.totais.get(componente)
    if total is None:
        return None
    n = (semana.fim - semana.inicio).days + 1
    if len(dias) == n:
        return total
    return total * len(dias) / n


# ───────────────────────────────────────────────────────────── leitura


@dataclass
class _Candidata:
    coleta_id: UUID
    dias: frozenset[date]  # os dias do período que as semanas da rodada cobrem
    # As semanas (início, fim) da rodada: semana dos `dados` com outras datas
    # a tela da Conferência não mostra (calculo._semana_dos_dados), aqui também.
    semanas: frozenset[tuple[date, date]]
    # ok/parcial com `dados`; falso = falhou (deslogada…): decide os dias
    # da rodada sem número ("—").
    com_numero: bool


@dataclass
class _ContaNoPeriodo:
    componentes: tuple[str, ...]
    candidatas: list[_Candidata] = field(default_factory=list)
    proxima: int = 0
    # componente → dias ainda sem número / soma já achada
    pendentes: dict[str, set[date]] = field(default_factory=dict)
    soma: dict[str, Decimal] = field(default_factory=dict)
    cobertos: set[date] = field(default_factory=set)


def _semanas_da_execucao(bruto: Any) -> list[tuple[date, date]]:
    out = []
    for s in bruto if isinstance(bruto, list) else []:
        if not isinstance(s, Mapping):
            continue
        ini, fim = _data(s.get("inicio")), _data(s.get("fim"))
        if ini is not None and fim is not None and ini <= fim:
            out.append((ini, fim))
    return out


async def _semanas_das_coletas(
    session: AsyncSession, ids: Iterable[UUID]
) -> list[tuple[UUID, _Semana]]:
    """As semanas guardadas nas coletas — só as datas, os dois totais e o dia a
    dia, cortados no banco: as listas de itens (o grosso do JSON) não vêm."""
    semanas = ConferenciaShopeeColeta.dados["semanas"]
    lista = case(
        (func.jsonb_typeof(semanas) == "array", semanas),
        else_=literal([], type_=JSONB),
    )
    s = func.jsonb_array_elements(lista).table_valued(column("value", JSONB)).lateral("s")
    v = s.c.value
    linhas = (
        await session.execute(
            select(
                ConferenciaShopeeColeta.id,
                v["inicio"].astext,
                v["fim"].astext,
                v[_TOTAL_DA_SEMANA["afiliados"][0]][_TOTAL_DA_SEMANA["afiliados"][1]],
                v[_TOTAL_DA_SEMANA["ads"][0]][_TOTAL_DA_SEMANA["ads"][1]],
                v["dias"],
            )
            .select_from(ConferenciaShopeeColeta)
            .join(s, true())
            .where(
                ConferenciaShopeeColeta.id.in_(list(ids)),
                func.jsonb_typeof(v) == "object",
            )
        )
    ).all()
    out = []
    for coleta_id, ini, fim, afiliados, ads, dias in linhas:
        d_ini, d_fim = _data(ini), _data(fim)
        if d_ini is None or d_fim is None or d_ini > d_fim:
            continue
        out.append(
            (
                coleta_id,
                _Semana(
                    inicio=d_ini,
                    fim=d_fim,
                    totais={"afiliados": _valor(afiliados), "ads": _valor(ads)},
                    dias=dias,
                ),
            )
        )
    return out


async def _primeiro_dia_das_contas(
    session: AsyncSession, contas: Sequence[UUID]
) -> dict[UUID, date]:
    """Conta → o 1º dia coberto pela rodada semanal mais antiga em que ela tem
    coleta com número (para o "parcial": o período começar antes disso)."""
    if not contas:
        return {}
    linhas = (
        await session.execute(
            select(ConferenciaShopeeColeta.conta_id, ConferenciaShopeeExecucao.semanas)
            .join(
                ConferenciaShopeeExecucao,
                ConferenciaShopeeExecucao.id == ConferenciaShopeeColeta.execucao_id,
            )
            .where(
                ConferenciaShopeeColeta.conta_id.in_(list(contas)),
                ConferenciaShopeeColeta.status.in_(_STATUS_COM_NUMERO),
                ConferenciaShopeeColeta.dados.is_not(None),
                ConferenciaShopeeExecucao.tipo == "semanal",
                ConferenciaShopeeExecucao.status != "cancelado",
            )
            .distinct(ConferenciaShopeeColeta.conta_id)
            .order_by(ConferenciaShopeeColeta.conta_id, ConferenciaShopeeExecucao.criado_em)
        )
    ).all()
    out: dict[UUID, date] = {}
    for conta_id, semanas in linhas:
        inicios = [ini for ini, _ in _semanas_da_execucao(semanas)]
        if inicios:
            out[conta_id] = min(inicios)
    return out


async def gasto_da_conferencia(
    session: AsyncSession,
    contas_por_loja: Mapping[UUID, Sequence[ContaConferencia]],
    inicio: date,
    fim: date,
) -> dict[UUID, GastoConferencia]:
    """Loja → afiliados e Ads da Conferência nos dias [inicio, fim] (regras no
    topo do módulo). Só aparece a loja com alguma conta; sem número no período
    os dois ficam None."""
    contas: dict[UUID, _ContaNoPeriodo] = {}
    for lista in contas_por_loja.values():
        for c in lista:
            estados = plataformas.estados(c.plataforma)
            comps = tuple(k for k in COMPONENTES if _METRICA[k] not in estados)
            if comps:
                contas[c.id] = _ContaNoPeriodo(componentes=comps)
    if not contas or inicio > fim:
        return {}

    # As rodadas que podem cobrir o período. Toda semana de uma rodada fecha
    # antes de ela ser criada: rodada criada antes do início não cobre nada.
    execucoes = (
        await session.execute(
            select(
                ConferenciaShopeeExecucao.id,
                ConferenciaShopeeExecucao.criado_em,
                ConferenciaShopeeExecucao.semanas,
            ).where(
                ConferenciaShopeeExecucao.tipo == "semanal",
                ConferenciaShopeeExecucao.status != "cancelado",
                ConferenciaShopeeExecucao.criado_em
                >= datetime.combine(inicio, time.min, tzinfo=UTC) - timedelta(days=1),
            )
        )
    ).all()
    dias_da_execucao: dict[UUID, frozenset[date]] = {}
    semanas_da_execucao: dict[UUID, frozenset[tuple[date, date]]] = {}
    criada_em: dict[UUID, datetime] = {}
    for ex_id, criado_em, semanas in execucoes:
        datas = _semanas_da_execucao(semanas)
        dias = frozenset(d for ini, f in datas for d in _dias_entre(max(ini, inicio), min(f, fim)))
        if dias:
            dias_da_execucao[ex_id] = dias
            semanas_da_execucao[ex_id] = frozenset(datas)
            criada_em[ex_id] = criado_em
    if dias_da_execucao:
        # TODA coleta encerrada da conta, com ou sem número (correção de
        # 24/11/2026, M-36): a que falhou na rodada mais recente também decide
        # os dias dela — sem número —, senão a rodada antiga preencheria a
        # semana que a tela da Conferência mostra "—" (regra 4).
        coletas = (
            await session.execute(
                select(
                    ConferenciaShopeeColeta.id,
                    ConferenciaShopeeColeta.execucao_id,
                    ConferenciaShopeeColeta.conta_id,
                    ConferenciaShopeeColeta.concluido_em,
                    ConferenciaShopeeColeta.status,
                    # `dados` objeto (o null do JSON não é número), como a
                    # tela lê (calculo.montar_relatorio).
                    func.jsonb_typeof(ConferenciaShopeeColeta.dados),
                ).where(
                    ConferenciaShopeeColeta.execucao_id.in_(list(dias_da_execucao)),
                    ConferenciaShopeeColeta.conta_id.in_(list(contas)),
                    ConferenciaShopeeColeta.status.not_in(_NA_FILA),
                )
            )
        ).all()
        _nunca = datetime.min.replace(tzinfo=UTC)
        # A mais recente primeiro: a rodada criada por último; na mesma rodada,
        # a coleta que terminou por último.
        for coleta_id, ex_id, conta_id, _concluido_em, status, tipo_dados in sorted(
            coletas,
            key=lambda r: (criada_em[r[1]], r[3] or _nunca, str(r[0])),
            reverse=True,
        ):
            contas[conta_id].candidatas.append(
                _Candidata(
                    coleta_id,
                    dias_da_execucao[ex_id],
                    semanas_da_execucao[ex_id],
                    com_numero=status in _STATUS_COM_NUMERO and tipo_dados == "object",
                )
            )
    for conta in contas.values():
        todos = set().union(*(c.dias for c in conta.candidatas)) if conta.candidatas else set()
        conta.pendentes = {k: set(todos) for k in conta.componentes}

    def decide(conta: _ContaNoPeriodo, dias: Iterable[date]) -> None:
        """Os `dias` ficam decididos pela rodada (com ou sem número): nenhuma
        rodada mais antiga mexe neles."""
        for pendentes in conta.pendentes.values():
            pendentes.difference_update(dias)

    # Rodada a rodada, da mais recente para trás: cada vez UMA coleta por conta
    # (a mais recente que ainda tem algum dia por decidir), e só as semanas
    # dela, cortadas no banco. Quase sempre a 1ª rodada já decide tudo.
    while True:
        lote: dict[UUID, tuple[UUID, _Candidata]] = {}
        for conta_id, conta in contas.items():
            while conta.proxima < len(conta.candidatas):
                faltam = set().union(*conta.pendentes.values()) if conta.pendentes else set()
                cand = conta.candidatas[conta.proxima]
                conta.proxima += 1
                if not cand.dias & faltam:
                    continue
                if not cand.com_numero:
                    # Falhou nesta rodada (deslogada, bloqueada, expirada…): as
                    # semanas dela ficam "—", como na tela; nada a ler no banco.
                    decide(conta, cand.dias)
                    continue
                lote[cand.coleta_id] = (conta_id, cand)
                break
        if not lote:
            break
        for coleta_id, semana in await _semanas_das_coletas(session, lote):
            conta_id, cand = lote[coleta_id]
            if (semana.inicio, semana.fim) not in cand.semanas:
                continue
            conta = contas[conta_id]
            no_periodo = [d for d in _dias_entre(semana.inicio, semana.fim) if inicio <= d <= fim]
            for comp in conta.componentes:
                pendentes = conta.pendentes[comp]
                usar = [d for d in no_periodo if d in pendentes]
                valor = valor_nos_dias(semana, comp, usar, no_periodo)
                if valor is not None:
                    conta.soma[comp] = conta.soma.get(comp, Decimal("0")) + valor
                    conta.cobertos.update(usar)
                # Seção que veio null (valor None) também fica decidida: a
                # semana mostra "—" na tela e a rodada antiga não a preenche.
                pendentes.difference_update(usar)
        # Semana da rodada que nem veio nos `dados` da coleta: "—" na tela,
        # decidida do mesmo jeito.
        for conta_id, cand in lote.values():
            decide(contas[conta_id], cand.dias)

    com_numero = {cid for cid, c in contas.items() if c.soma}
    primeiro_dia = await _primeiro_dia_das_contas(session, list(contas))
    out: dict[UUID, GastoConferencia] = {}
    for loja, lista in contas_por_loja.items():
        ids = [c.id for c in lista if c.id in contas]
        if not ids:
            continue
        com = [cid for cid in ids if cid in com_numero]
        if not com:
            out[loja] = GastoConferencia(afiliados=None, ads=None, desde=None, ate=None)
            continue
        partes: dict[str, Decimal | None] = {}
        for comp in COMPONENTES:
            achados = [contas[cid].soma[comp] for cid in com if comp in contas[cid].soma]
            partes[comp] = (
                sum(achados, Decimal("0")).quantize(_CENTAVO, rounding=ROUND_HALF_UP)
                if achados
                else None
            )
        desdes = [primeiro_dia[cid] for cid in ids if cid in primeiro_dia]
        out[loja] = GastoConferencia(
            afiliados=partes["afiliados"],
            ads=partes["ads"],
            desde=max(desdes) if desdes else None,
            ate=min(max(contas[cid].cobertos) for cid in com),
        )
    return out
