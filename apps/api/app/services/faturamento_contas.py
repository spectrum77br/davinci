"""As contas de cada loja da aba Faturamento (M-22, 24/11/2026).

O Faturamento é por LOJA (store_info). As colunas novas vêm de outras contas,
ligadas à loja assim:

1. A integração da loja (`integracoes_das_lojas`): o FK `store_info.
   integration_id`; sem FK, a integração da mesma plataforma com o mesmo nome
   (a régua da cerca de equipe: `_chave_da_conta`, sem acento, espaço nem o
   nome da plataforma na frente), quando só UMA loja do cadastro tem esse
   nome e a integração não é o FK de outra loja.
2. Contas da Tabela de Preços (`contas_preco_das_lojas`) — Pendências (a
   Margem saiu daqui na M-37, 24/11/2026: agora vem da Conciliação, em
   faturamento_margem, ligada à loja pelo pedido do Bling): a conta de kit
   cujo `store_info_id` é a loja; sem `store_info_id`, a que usa a integração
   da loja (a mesma régua da cerca da Tabela, `_team_scope_accounts`). A
   coluna de catálogo vai com a conta de kit dela.
3. Contas de Ads do Marketing (`contas_ads_das_lojas`) — Investimento, o
   gasto por dia (`marketing_gasto_dia`): a conta de Ads (não arquivada) cuja
   integração é a da loja; sem integração (Shopee pelo AdsPower) ou com
   integração que não é de loja nenhuma, pelo nome + plataforma da conta de
   Ads, com a mesma régua do 1.
4. Contas da Conferência (`contas_conferencia_das_lojas`) — Investimento
   (M-36, 24/11/2026): pela CHAVE INTERNA, nunca pelo nome da tela (item 7 do
   pedido): a loja do Bling da conta (`bling_loja_id` = `store_info.
   bling_store_id`); sem ela, na Shopee a `conta_key` (o account_name da loja
   Shopee em minúsculas, a régua da própria Conferência em conferencia_shopee/
   classificacao) e no ML/Amazon a integração da conta (pela regra 1).

Uma conta vai para UMA loja só (a primeira regra que acha); se a regra acha
duas lojas, a conta fica de fora (melhor "—" que número na loja errada).

Investimento da loja (M-36, `investimento_das_lojas`) = afiliados + Ads, cada
pedaço de uma fonte: o gasto POR DIA do Marketing (marketing_gasto_dia —
hoje o Ads do ML, "mais atual", item 3) quando a loja tem dia guardado no
período; senão as coletas semanais da Conferência (faturamento_conferencia —
a Shopee, afiliados + Ads). Pedaço sem dado em nenhuma = None; os dois None =
"—" na tela, nunca zero.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.team_scope import _chave_da_conta
from app.models import Integration, MarketingAccount, MarketingGastoDia, PricingAccount, StoreInfo
from app.models.conferencia_shopee import ConferenciaShopeeConta
from app.models.pricing import CANAL_CATALOGO
from app.services import faturamento_conferencia as conferencia_svc
from app.services.faturamento_conferencia import ContaConferencia

logger = structlog.get_logger()

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


@dataclass
class Investimento:
    """Gasto da loja no período (M-22; M-36 em 24/11/2026). `total` =
    afiliados + Ads; None = nenhum dos dois tem dado (a tela mostra "—").
    `afiliados` e `ads`: cada pedaço (None = sem dado; a Margem da M-37 usa
    SÓ o `ads`). `desde`: o primeiro dia a partir do qual TODAS as contas da
    loja com histórico (nas fontes usadas) têm dado; `parcial`: o período
    começa antes dele. `ate`: o último dia coberto pelas semanas fechadas da
    Conferência quando é antes do fim do período (a semana corrente entra
    quando fechar); None = cobre até o fim ou não usou a Conferência."""

    total: Decimal | None
    desde: date | None
    parcial: bool
    ads: Decimal | None = None
    afiliados: Decimal | None = None
    ate: date | None = None


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


def _minusculo(valor: str | None) -> str:
    return (valor or "").strip().lower()


async def contas_conferencia_das_lojas(
    session: AsyncSession, integ_por_loja: dict[UUID, set[UUID]]
) -> dict[UUID, list[ContaConferencia]]:
    """Loja → as contas da Conferência (Shopee, ML e Amazon) dela (regra 4).
    Conta inativa entra: as coletas antigas dela continuam valendo para os
    períodos em que ela coletava."""
    loja_da_integ = _loja_da_integracao(integ_por_loja)
    por_bling: dict[str, set[UUID]] = defaultdict(set)
    # conta_key → (lojas Shopee ativas, arquivadas) com esse account_name.
    shopee_ativas: dict[str, set[UUID]] = defaultdict(set)
    shopee_arquivadas: dict[str, set[UUID]] = defaultdict(set)
    for loja_id, plataforma, nome, bling, arquivada_em in (
        await session.execute(
            select(
                StoreInfo.id,
                StoreInfo.platform,
                StoreInfo.account_name,
                StoreInfo.bling_store_id,
                StoreInfo.archived_at,
            )
        )
    ).all():
        if (bling or "").strip():
            por_bling[bling.strip()].add(loja_id)
        if _minusculo(plataforma) == "shopee" and _minusculo(nome):
            alvo = shopee_ativas if arquivada_em is None else shopee_arquivadas
            alvo[_minusculo(nome)].add(loja_id)
    out: dict[UUID, list[ContaConferencia]] = defaultdict(list)
    for conta_id, plataforma, conta_key, integ_id, bling in (
        await session.execute(
            select(
                ConferenciaShopeeConta.id,
                ConferenciaShopeeConta.plataforma,
                ConferenciaShopeeConta.conta_key,
                ConferenciaShopeeConta.integration_id,
                ConferenciaShopeeConta.bling_loja_id,
            )
        )
    ).all():
        plataforma = _minusculo(plataforma) or "shopee"
        # A 1ª chave que acha loja decide (achou duas: a conta fica de fora).
        candidatas: set[UUID] = set()
        if (bling or "").strip():
            candidatas = por_bling.get(bling.strip(), set())
        if not candidatas and plataforma == "shopee" and _minusculo(conta_key):
            chave = _minusculo(conta_key)
            # A loja ativa com esse nome; só arquivada, a arquivada (o período
            # antigo dela ainda aparece no Faturamento).
            candidatas = shopee_ativas.get(chave) or shopee_arquivadas.get(chave) or set()
        if not candidatas and integ_id is not None and integ_id in loja_da_integ:
            dona = loja_da_integ[integ_id]
            if dona is None:
                continue  # duas lojas reclamam a integração: fica de fora
            candidatas = {dona}
        loja = _loja_unica(candidatas)
        if loja is not None:
            out[loja].append(ContaConferencia(id=conta_id, plataforma=plataforma))
    return dict(out)


async def _gasto_por_dia(
    session: AsyncSession,
    contas_ads: dict[UUID, list[UUID]],
    inicio: date,
    fim: date,
) -> tuple[dict[UUID, tuple[Decimal | None, Decimal | None]], dict[UUID, date]]:
    """Por conta de Ads: (Ads, afiliados) guardados por dia em [inicio, fim]
    (None = nenhum dia com aquele número) e o 1º dia guardado de cada uma."""
    todas = sorted({c for contas in contas_ads.values() for c in contas})
    if not todas:
        return {}, {}
    try:
        # SAVEPOINT: sem a migration 0507 (código no ar antes dela — o deploy
        # não roda migration) a tabela não existe; aí só o Ads por dia fica
        # sem dado e a aba (pedidos, faturamento, Conferência) abre normal.
        async with session.begin_nested():
            no_periodo = {
                conta: (
                    Decimal(ads) if n_ads else None,
                    Decimal(afiliados) if n_afiliados else None,
                )
                for conta, ads, n_ads, afiliados, n_afiliados in (
                    await session.execute(
                        select(
                            MarketingGastoDia.marketing_account_id,
                            func.sum(MarketingGastoDia.gasto_ads),
                            func.count(MarketingGastoDia.gasto_ads),
                            func.sum(MarketingGastoDia.gasto_afiliados),
                            func.count(MarketingGastoDia.gasto_afiliados),
                        )
                        .where(
                            MarketingGastoDia.marketing_account_id.in_(todas),
                            MarketingGastoDia.dia >= inicio,
                            MarketingGastoDia.dia <= fim,
                        )
                        .group_by(MarketingGastoDia.marketing_account_id)
                    )
                ).all()
            }
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
        return {}, {}
    return no_periodo, primeiro_dia


def _soma(valores: list[Decimal | None]) -> Decimal | None:
    achados = [v for v in valores if v is not None]
    return sum(achados, Decimal("0")) if achados else None


def _centavos(v: Decimal | None) -> Decimal | None:
    return None if v is None else v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def investimento_das_lojas(
    session: AsyncSession,
    contas_ads: dict[UUID, list[UUID]],
    inicio: date,
    fim: date,
    contas_conferencia: dict[UUID, list[ContaConferencia]] | None = None,
) -> dict[UUID, Investimento]:
    """Loja → afiliados + Ads nos dias [inicio, fim] (M-36; regra no topo do
    módulo). Cada pedaço: o guardado por dia no Marketing quando a loja tem
    algum dia com aquele número no período; senão o da Conferência."""
    por_dia, primeiro_dia = await _gasto_por_dia(session, contas_ads, inicio, fim)
    conferencia = (
        await conferencia_svc.gasto_da_conferencia(session, contas_conferencia, inicio, fim)
        if contas_conferencia
        else {}
    )
    out: dict[UUID, Investimento] = {}
    for loja in set(contas_ads) | set(conferencia):
        contas = contas_ads.get(loja, [])
        com_dado = [por_dia[c] for c in contas if c in por_dia]
        dia_ads = _soma([ads for ads, _ in com_dado])
        dia_afiliados = _soma([afiliados for _, afiliados in com_dado])
        conf = conferencia.get(loja)
        ads, afiliados = _centavos(dia_ads), _centavos(dia_afiliados)
        usou_conferencia = False
        if ads is None and conf is not None and conf.ads is not None:
            ads, usou_conferencia = conf.ads, True
        if afiliados is None and conf is not None and conf.afiliados is not None:
            afiliados, usou_conferencia = conf.afiliados, True
        if ads is None and afiliados is None:
            out[loja] = Investimento(total=None, desde=None, parcial=False)
            continue
        desdes: list[date] = []
        if dia_ads is not None or dia_afiliados is not None:
            desdes += [primeiro_dia[c] for c in contas if c in primeiro_dia]
        if usou_conferencia and conf is not None and conf.desde is not None:
            desdes.append(conf.desde)
        desde = max(desdes) if desdes else None
        ate = conf.ate if usou_conferencia and conf is not None else None
        out[loja] = Investimento(
            total=(ads or Decimal("0")) + (afiliados or Decimal("0")),
            desde=desde,
            parcial=desde is not None and inicio < desde,
            ads=ads,
            afiliados=afiliados,
            ate=ate if ate is not None and ate < fim else None,
        )
    return out
