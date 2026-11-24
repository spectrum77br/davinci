"""As contas de cada loja da aba Faturamento (M-22, 24/11/2026).

O Faturamento é por LOJA (store_info). As colunas novas vêm de outras contas,
ligadas à loja assim:

1. A integração da loja (`integracoes_das_lojas`): o FK `store_info.
   integration_id`; sem FK, a integração da mesma plataforma com o mesmo nome
   (a régua da cerca de equipe: `_chave_da_conta`, sem acento, espaço nem o
   nome da plataforma na frente), quando só UMA loja do cadastro tem esse
   nome e a integração não é o FK de outra loja.
2. Contas da Tabela de Preços (`contas_preco_das_lojas`) — Pendências e
   Margem: a conta de kit cujo `store_info_id` é a loja; sem `store_info_id`,
   a que usa a integração da loja (a mesma régua da cerca da Tabela,
   `_team_scope_accounts`). A coluna de catálogo vai com a conta de kit dela.
3. Contas de Ads do Marketing (`contas_ads_das_lojas`) — Investimento: a
   conta de Ads (não arquivada) cuja integração é a da loja; sem integração
   (Shopee pelo AdsPower) ou com integração que não é de loja nenhuma, pelo
   nome + plataforma da conta de Ads, com a mesma régua do 1.

Uma conta vai para UMA loja só (a primeira regra que acha); se a regra acha
duas lojas, a conta fica de fora (melhor "—" que número na loja errada).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.team_scope import _chave_da_conta
from app.models import Integration, MarketingAccount, MarketingGastoDia, PricingAccount, StoreInfo
from app.models.pricing import CANAL_CATALOGO

logger = structlog.get_logger()

_MARGENS = ("margin1", "margin2", "margin3", "margin4", "margin5")


async def integracoes_das_lojas(session: AsyncSession) -> dict[UUID, set[UUID]]:
    """Loja (todas do cadastro) → as integrações dela (regra 1 do módulo)."""
    lojas = (
        await session.execute(
            select(
                StoreInfo.id, StoreInfo.integration_id, StoreInfo.platform, StoreInfo.account_name
            )
        )
    ).all()
    out: dict[UUID, set[UUID]] = defaultdict(set)
    fk_de_alguem: set[UUID] = set()
    lojas_por_chave: dict[tuple[str, str], list[UUID]] = defaultdict(list)
    for loja_id, integ_id, plataforma, nome in lojas:
        if integ_id is not None:
            out[loja_id].add(integ_id)
            fk_de_alguem.add(integ_id)
            continue
        chave = _chave_da_conta(plataforma, nome)
        if chave:
            lojas_por_chave[chave].append(loja_id)
    if lojas_por_chave:
        for integ_id, nome, plataforma in (
            await session.execute(select(Integration.id, Integration.name, Integration.platform))
        ).all():
            if integ_id in fk_de_alguem:
                continue
            achadas = lojas_por_chave.get(_chave_da_conta(plataforma, nome) or ("", ""), [])
            if len(achadas) == 1:
                out[achadas[0]].add(integ_id)
    return dict(out)


def _loja_unica(lojas: set[UUID]) -> UUID | None:
    return next(iter(lojas)) if len(lojas) == 1 else None


def _loja_da_integracao(integ_por_loja: dict[UUID, set[UUID]]) -> dict[UUID, UUID | None]:
    """Integração → a loja dela (None quando duas lojas a reclamam)."""
    lojas: dict[UUID, set[UUID]] = defaultdict(set)
    for loja_id, integs in integ_por_loja.items():
        for integ_id in integs:
            lojas[integ_id].add(loja_id)
    return {integ_id: _loja_unica(donas) for integ_id, donas in lojas.items()}


async def contas_preco_das_lojas(
    session: AsyncSession, integ_por_loja: dict[UUID, set[UUID]]
) -> dict[UUID, list[PricingAccount]]:
    """Loja → as contas da Tabela de Preços dela, kit e catálogo (regra 2)."""
    contas = (await session.execute(select(PricingAccount))).scalars().all()
    loja_da_integ = _loja_da_integracao(integ_por_loja)
    loja_da_conta: dict[UUID, UUID] = {}
    for c in contas:
        if c.canal == CANAL_CATALOGO:
            continue
        loja = c.store_info_id
        if loja is None and c.integration_id is not None:
            loja = loja_da_integ.get(c.integration_id)
        if loja is not None:
            loja_da_conta[c.id] = loja
    out: dict[UUID, list[PricingAccount]] = defaultdict(list)
    for c in contas:
        base = c.conta_base_id if c.canal == CANAL_CATALOGO else c.id
        loja = loja_da_conta.get(base) if base is not None else None
        if loja is not None:
            out[loja].append(c)
    return dict(out)


def faixa_de_margem(contas: Sequence[PricingAccount]) -> tuple[Decimal, Decimal] | None:
    """A menor e a maior margem (margin1..5) de TODAS as contas de preço da
    loja, as da aba Contas: as de kit e a margem própria da coluna de catálogo
    (botão "% margens"; NULL nela = usa a da conta de kit, que já entra).
    Sem nenhuma margem preenchida: None."""
    valores = [
        v for c in contas for v in (getattr(c, campo) for campo in _MARGENS) if v is not None
    ]
    if not valores:
        return None
    return min(valores), max(valores)


@dataclass
class Investimento:
    """Gasto da loja no período. `total` None = nenhum dia guardado no
    período (a tela mostra "—"). `desde`: o primeiro dia a partir do qual
    TODAS as contas de Ads da loja com histórico têm o gasto guardado;
    `parcial`: o período começa antes dele."""

    total: Decimal | None
    desde: date | None
    parcial: bool


async def contas_ads_das_lojas(
    session: AsyncSession, integ_por_loja: dict[UUID, set[UUID]]
) -> dict[UUID, list[UUID]]:
    """Loja → as contas de Ads (Marketing) dela, não arquivadas (regra 3)."""
    loja_da_integ = _loja_da_integracao(integ_por_loja)
    lojas = (
        await session.execute(select(StoreInfo.id, StoreInfo.platform, StoreInfo.account_name))
    ).all()
    lojas_por_chave: dict[tuple[str, str], set[UUID]] = defaultdict(set)
    for loja_id, plataforma, nome in lojas:
        chave = _chave_da_conta(plataforma, nome)
        if chave:
            lojas_por_chave[chave].add(loja_id)
    out: dict[UUID, list[UUID]] = defaultdict(list)
    for conta_id, integ_id, plataforma, nome in (
        await session.execute(
            select(
                MarketingAccount.id,
                MarketingAccount.integration_id,
                MarketingAccount.platform,
                MarketingAccount.name,
            ).where(MarketingAccount.arquivada_em.is_(None))
        )
    ).all():
        if integ_id is not None and integ_id in loja_da_integ:
            # None = duas lojas reclamam a integração: fica de fora.
            loja = loja_da_integ[integ_id]
        else:
            # Sem integração (Shopee pelo AdsPower) ou integração sem loja:
            # pelo nome + plataforma da própria conta de Ads.
            loja = _loja_unica(
                lojas_por_chave.get(_chave_da_conta(plataforma, nome) or ("", ""), set())
            )
        if loja is not None:
            out[loja].append(conta_id)
    return dict(out)


async def investimento_das_lojas(
    session: AsyncSession,
    contas_ads: dict[UUID, list[UUID]],
    inicio: date,
    fim: date,
) -> dict[UUID, Investimento]:
    """Loja → o gasto guardado (Ads + afiliados) nos dias [inicio, fim]."""
    todas = sorted({c for contas in contas_ads.values() for c in contas})
    if not todas:
        return {}
    soma = func.coalesce(MarketingGastoDia.gasto_ads, 0) + func.coalesce(
        MarketingGastoDia.gasto_afiliados, 0
    )
    try:
        # SAVEPOINT: sem a migration 0506 (código no ar antes dela — o deploy
        # não roda migration) a tabela não existe; aí só o Investimento fica
        # "—" e a aba (pedidos, faturamento) abre normal.
        async with session.begin_nested():
            no_periodo = dict(
                (
                    await session.execute(
                        select(MarketingGastoDia.marketing_account_id, func.sum(soma))
                        .where(
                            MarketingGastoDia.marketing_account_id.in_(todas),
                            MarketingGastoDia.dia >= inicio,
                            MarketingGastoDia.dia <= fim,
                        )
                        .group_by(MarketingGastoDia.marketing_account_id)
                    )
                ).all()
            )
            primeiro_dia = dict(
                (
                    await session.execute(
                        select(
                            MarketingGastoDia.marketing_account_id,
                            func.min(MarketingGastoDia.dia),
                        )
                        .where(MarketingGastoDia.marketing_account_id.in_(todas))
                        .group_by(MarketingGastoDia.marketing_account_id)
                    )
                ).all()
            )
    except ProgrammingError as e:
        logger.warning("faturamento_investimento_sem_historico", erro=str(e)[:300])
        return {}
    out: dict[UUID, Investimento] = {}
    for loja, contas in contas_ads.items():
        com_dado = [c for c in contas if c in no_periodo]
        if not com_dado:
            out[loja] = Investimento(total=None, desde=None, parcial=False)
            continue
        total = sum((Decimal(no_periodo[c]) for c in com_dado), Decimal("0"))
        desde = max(primeiro_dia[c] for c in contas if c in primeiro_dia)
        out[loja] = Investimento(total=total, desde=desde, parcial=inicio < desde)
    return out
