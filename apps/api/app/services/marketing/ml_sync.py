"""Pull live ML Product Ads numbers into the marketing_* tables.

Mirrors `shopee_sync.py` but for Mercado Livre. The shape divergence:

  • ML has no balance pot — `credit_balance` shows "today's remaining
    daily budget" (sum of active campaign budgets minus today's spend).
  • ML has no per-day shop-level rollup — we synthesise one MarketingMetric
    row at the window's end_date with the totals across campaigns.
  • ML has no hourly performance — heatmap stays empty for ML accounts.

Bling revenue still drives ACOS for the account-level numbers. ML's
own attributed-sales is kept on the campaign rows because Bling can't
slice by campaign.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import Integration
from app.models.marketing import MarketingAccount, MarketingCampaign, MarketingMetric
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketing.alerts import (
    notify_high_acos,
    record_sync_failure,
    record_sync_success,
)
from app.services.marketing.bling_revenue import get_bling_revenue
from app.services.ml_ads import (
    MLAdsClient,
    MLAdsError,
    MLAdsScopeError,
    MLCampaign,
)

logger = structlog.get_logger()

_DAILY_LOOKBACK_DAYS = 7


async def sync_ml_integration(
    session: AsyncSession,
    integration_id: UUID,
) -> dict[str, Any]:
    """Sync one ML Product Ads integration. Caller owns the transaction
    lifecycle but we commit at the end so the manual-trigger endpoint
    returns a fully-persisted state."""
    integration = await session.get(Integration, integration_id)
    if integration is None:
        return {"status": "error", "code": "integration_not_found"}
    if integration.platform.value != "ml":
        return {"status": "skipped", "reason": "not_ml", "platform": integration.platform.value}

    creds = decrypt_json(integration.credentials)

    async def _persist_refreshed_creds(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        expires_at = new_creds.get("expires_at")
        if expires_at:
            integration.token_expires_at = datetime.fromtimestamp(int(expires_at), tz=UTC)
        await session.flush()

    client = MLAdsClient(creds, on_token_refresh=_persist_refreshed_creds)

    today = datetime.now(UTC).date()
    # Janela de "amadurecimento" do faturamento: a receita de um dia sobe nos
    # dias seguintes conforme os pedidos avançam de situação. Reprocessamos só
    # o faturamento (não o gasto) dessa janela a cada run — é barato, é query
    # local. NÃO é backfill histórico: linhas inexistentes não são criadas.
    start_day = today - timedelta(days=_DAILY_LOOKBACK_DAYS - 1)

    try:
        # Gasto/impressões de HOJE (dia único). O antigo (start_day, today)
        # devolvia o SOMATÓRIO de 7 dias e gravava isso na linha de hoje —
        # inflava o gasto e quebrava o ACOS. Agora a linha do dia é do dia.
        campaigns, today_metric = await client.get_daily_performance(today, today)
        remaining_budget = await client.get_remaining_daily_budget(campaigns=campaigns)
        # Gasto POR DIA da janela. O ML finaliza o `cost` com ~3-4 dias de
        # atraso (dias recentes voltam 0 e amadurecem run a run), e o endpoint
        # só devolve o TOTAL da janela — por isso puxamos dia a dia. Usado
        # abaixo pra corrigir o gasto das linhas já gravadas; sem isso o gasto
        # ficava congelado no 1º valor (que era o somatório inflado da janela).
        spend_series = await client.get_daily_spend_series(start_day, today)
    except MLAdsScopeError as e:
        # Scope not granted = operator action needed = NOT a flapping bug.
        # Don't increment consecutive_errors (would spam Telegram every cron).
        integration.last_error = f"ml_scope_missing: {e.message}"[:500]
        await session.commit()
        logger.warning(
            "ml_ads_scope_missing",
            integration_id=str(integration_id),
            message=e.message,
        )
        return {"status": "skipped", "code": "scope_missing", "message": e.message}
    except MLAdsError as e:
        integration.last_error = f"{e.code}: {e.message}"[:500]
        # "no_advertiser" / 404 not_found = account literally doesn't
        # have Product Ads — same treatment as scope_missing.
        if e.code in {"no_advertiser", "not_found"} or e.status == 404:
            await session.commit()
            logger.info(
                "ml_ads_no_advertiser",
                integration_id=str(integration_id), code=e.code,
            )
            return {"status": "skipped", "code": e.code, "message": e.message}
        await record_sync_failure(session, integration, code=e.code, message=e.message)
        await session.commit()
        raise

    # ─── Faturamento (fonte: aba Faturamento = bling_orders local) ───────
    # revenue = faturamento faturável {6,15,83953} da loja, por dia BRT.
    bling = await get_bling_revenue(session, integration, start=start_day, end=today)
    by_day = bling.by_day if bling is not None else {}
    # Loja mapeada → usa o faturamento (0 se ainda não houve venda hoje).
    # Sem mapeamento de loja → cai no atribuído do próprio ML (só hoje).
    revenue_today = (
        by_day.get(today, 0.0) if bling is not None else today_metric.revenue
    )
    # ACOS = Gasto ÷ Faturamento × 100 (definição do operador).
    account_acos = (
        round(today_metric.spend / revenue_today * 100, 2)
        if revenue_today > 0
        else None
    )

    # ─── upsert MarketingAccount (tudo de HOJE) ──────────────────────────
    account = await _upsert_account(
        session,
        integration=integration,
        credit=remaining_budget,
        spend=today_metric.spend,
        revenue=revenue_today,
        impressions=today_metric.impressions,
    )

    # ─── linha diária de HOJE (sentinel intensity=0), tudo do mesmo dia ──
    await _upsert_daily_metric(
        session,
        account_id=account.id,
        day=today,
        spend=today_metric.spend,
        revenue=revenue_today,
        impressions=today_metric.impressions,
        clicks=today_metric.clicks,
        acos=account_acos,
    )

    # ─── amadurecimento: corrige GASTO (ML finaliza ~4d depois) e
    #     FATURAMENTO (pedidos amadurecem de situação {6,15,83953}) das
    #     linhas JÁ EXISTENTES da janela, recalculando o ACOS. NÃO cria
    #     linhas novas (nada de backfill histórico) — o dia que não existe
    #     é ignorado; a linha de hoje se auto-corrige nas runs seguintes. ─
    day = start_day
    while day < today:
        await _refresh_metric_day(
            session,
            account_id=account.id,
            day=day,
            spend=spend_series.get(day),
            revenue=by_day.get(day) if bling is not None else None,
        )
        day += timedelta(days=1)

    # ─── campaigns upsert ────────────────────────────────────────────────
    await _upsert_campaigns(
        session,
        account_id=account.id,
        credit_hint=remaining_budget,
        campaigns=campaigns,
    )

    integration.last_error = None
    integration.last_test_ok = True
    integration.last_test_at = datetime.now(UTC)
    await record_sync_success(session, integration)
    await session.commit()

    # ─── post-commit alerts ──────────────────────────────────────────────
    if account_acos is not None:
        await notify_high_acos(integration, account_acos, account.acos_target)

    return {
        "status": "ok",
        "account_id": str(account.id),
        "credit": remaining_budget,
        "spend": today_metric.spend,
        "revenue": revenue_today,
        "campaigns": len(campaigns),
        "bling_revenue": bling.total if bling else None,
    }


async def sync_all_ml_integrations(session: AsyncSession) -> list[dict[str, Any]]:
    """Iterate every ML integration opted into the marketing module."""
    rows = (
        await session.execute(
            select(Integration).where(
                and_(
                    Integration.status == "active",
                    Integration.platform == "ml",
                    Integration.ads_enabled.is_(True),
                )
            )
        )
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for integ in rows:
        try:
            r = await sync_ml_integration(session, integ.id)
            out.append({"integration_id": str(integ.id), **r})
        except Exception as e:  # noqa: BLE001
            logger.error("ml_ads_sync_failed", integration_id=str(integ.id), err=str(e)[:300])
            out.append({"integration_id": str(integ.id), "status": "error", "error": str(e)[:200]})
    return out


# ─── internals ────────────────────────────────────────────────────────────


async def _upsert_account(
    session: AsyncSession,
    *,
    integration: Integration,
    credit: float,
    spend: float,
    revenue: float,
    impressions: int,
) -> MarketingAccount:
    dept = (integration.department or "geral").lower()
    existing = (
        await session.execute(
            select(MarketingAccount).where(
                MarketingAccount.integration_id == integration.id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = MarketingAccount(
            integration_id=integration.id,
            name=integration.name,
            platform="ml",
            department=dept,
            acos_target=7.0,
            credit_balance=credit,
            agent_enabled=False,
            status="active",
            spend_today=spend,
            revenue_today=revenue,
            impressions_today=impressions,
        )
        session.add(existing)
        await session.flush()
    else:
        existing.platform = "ml"
        existing.department = dept
        existing.credit_balance = credit
        existing.spend_today = spend
        existing.revenue_today = revenue
        existing.impressions_today = impressions
        existing.status = "active"
    return existing


async def _upsert_daily_metric(
    session: AsyncSession,
    *,
    account_id: UUID,
    day: date,
    spend: float,
    revenue: float,
    impressions: int,
    clicks: int,
    acos: float | None,
) -> None:
    ts = datetime.combine(day, time(12, 0), tzinfo=UTC)
    existing = (
        await session.execute(
            select(MarketingMetric).where(
                and_(
                    MarketingMetric.account_id == account_id,
                    MarketingMetric.timestamp == ts,
                    MarketingMetric.intensity == 0,
                )
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            MarketingMetric(
                account_id=account_id, timestamp=ts,
                spend=spend, revenue=revenue,
                impressions=impressions, clicks=clicks,
                orders=0, acos=acos, intensity=0,
            )
        )
    else:
        existing.spend = spend
        existing.revenue = revenue
        existing.impressions = impressions
        existing.clicks = clicks
        existing.acos = acos


async def _refresh_metric_day(
    session: AsyncSession,
    *,
    account_id: UUID,
    day: date,
    spend: float | None = None,
    revenue: float | None = None,
) -> None:
    """Atualiza uma linha diária JÁ EXISTENTE: gasto (se `spend` != None)
    e/ou faturamento (se `revenue` != None), recalculando o ACOS =
    gasto/faturamento×100. NÃO cria linha nova — se o dia não existe, não
    faz nada (sem backfill histórico).

    ACOS fica None enquanto o gasto do dia ainda não foi finalizado pelo ML
    (gasto 0) — evita mostrar "0% de ACOS" num dia que teve venda mas cujo
    custo de anúncio ainda não amadureceu."""
    ts = datetime.combine(day, time(12, 0), tzinfo=UTC)
    row = (
        await session.execute(
            select(MarketingMetric).where(
                and_(
                    MarketingMetric.account_id == account_id,
                    MarketingMetric.timestamp == ts,
                    MarketingMetric.intensity == 0,
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return
    if spend is not None:
        row.spend = spend
    if revenue is not None:
        row.revenue = revenue
    row.acos = (
        round(row.spend / row.revenue * 100, 2)
        if row.revenue and row.revenue > 0 and row.spend
        else None
    )


def _map_ml_status(status: str) -> str:
    s = (status or "").lower()
    if s in ("active", "ongoing", "running"):
        return "active"
    if s == "paused":
        return "paused"
    if s in ("ended", "finished", "deleted"):
        return "off"
    return s or "active"


async def _upsert_campaigns(
    session: AsyncSession,
    *,
    account_id: UUID,
    credit_hint: float,
    campaigns: list[MLCampaign],
) -> None:
    existing = (
        await session.execute(
            select(MarketingCampaign).where(MarketingCampaign.account_id == account_id)
        )
    ).scalars().all()
    by_ext = {c.external_id: c for c in existing if c.external_id}
    for camp in campaigns:
        ext_id = str(camp.campaign_id)
        status = _map_ml_status(camp.status)
        row = by_ext.get(ext_id)
        if row is None:
            session.add(
                MarketingCampaign(
                    account_id=account_id,
                    name=camp.name or f"Campanha {camp.campaign_id}",
                    external_id=ext_id,
                    status=status,
                    credit=None,  # ML has no per-campaign credit
                    spend=camp.spend,
                    revenue=0.0,  # ML attributed sales — orchestrator could fill from camp.acos
                    impressions=camp.impressions,
                    acos=camp.acos,
                )
            )
        else:
            row.name = camp.name or row.name
            row.status = status
            row.spend = camp.spend
            row.impressions = camp.impressions
            row.acos = camp.acos
