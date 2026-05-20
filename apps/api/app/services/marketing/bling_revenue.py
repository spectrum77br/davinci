"""Bling-as-revenue-source for the Marketing module.

Why: the platforms' GMV (Shopee `direct_gmv` + `broad_gmv`, ML's spend
metrics, Amazon `sales14d`) is "attributed sales" — orders the ad click
*may* have driven, with a 14-day attribution window. The authoritative
"faturamento" number for accounting is what actually shipped with NF
emitida, which lives in Bling under `pedidos/vendas` with `situacao=9`
("Atendido"). This module fetches the real number and provides per-day
aggregates the orchestrator can join against ad spend to compute the
"true" ACOS.

Loja routing:
  1. If `Integration.bling_loja_id` is set, use it (explicit override).
  2. Else if the Integration has a Store, use `Store.bling_store_id`.
  3. Else return None — caller treats absence as "skip Bling for this
     integration" and falls back to platform GMV.

There is exactly one Bling Integration per company (the existing
bling_orders.py pattern hard-codes `.limit(1)`); we follow that — every
marketing integration shares the same Bling client and filters by
idLoja for routing.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Store
from app.models.enums import IntegrationPlatform
from app.models.integration import Integration
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()

# Optional Bling situação filter. The default-9 ("Atendido / NF emitida")
# turned out to be wrong for prod's Bling — that account uses custom
# situação IDs (15, 83953-83965) and never set anything to 9 in 30 days.
# Leaving this None means: don't filter server-side; the client-side
# `_EXCLUDED_SITUACOES` set drops cancelled + draft pedidos. Set to a
# specific int (e.g. 9 or 15) here to re-enable strict filtering once
# the per-shop "faturado" id is known.
BLING_SITUACAO_FATURADO: int | None = None

# Situações we always exclude even when no positive filter is set:
#   12 = Cancelado (refund/cancel, no revenue)
#    6 = Em digitação (draft, not confirmed yet)
_EXCLUDED_SITUACOES: frozenset[int] = frozenset({12, 6})


@dataclass(slots=True)
class BlingRevenue:
    """Per-day revenue in BRL, keyed by emission date. `total` is the
    sum; convenient when the caller only needs the window total."""

    total: float
    by_day: dict[date, float]
    order_count: int


async def _resolve_bling_client(session: AsyncSession) -> BlingClient | None:
    """Singleton Bling integration → BlingClient. Returns None if Bling
    isn't connected (so the marketing sync silently falls back to
    platform GMV instead of erroring)."""
    integ = (
        await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        return None
    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.flush()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)


async def resolve_bling_loja_id(
    session: AsyncSession, integration: Integration
) -> int | None:
    """Returns the Bling idLoja for this integration, or None if no
    mapping exists. Caller decides whether absence is an error or just
    means "skip Bling for this integration"."""
    if integration.bling_loja_id is not None:
        return int(integration.bling_loja_id)
    if integration.store_id is None:
        return None
    store = await session.get(Store, integration.store_id)
    if store is None or store.bling_store_id is None:
        return None
    return int(store.bling_store_id)


def _parse_bling_date(s: str | None) -> date | None:
    """Bling returns dataEmissao as YYYY-MM-DD (sometimes with timestamp
    suffix). We tolerate both shapes — datetime.fromisoformat handles
    the truncation cleanly when we only need the date part."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None


def _pedido_total(pedido: dict) -> float:
    """`totalVenda` is the canonical revenue field; `total` and
    `totalProdutos` are also returned by Bling but exclude (or include)
    fees inconsistently across versions. Prefer totalVenda; fall back
    to total when missing for safety."""
    v = pedido.get("totalVenda")
    if v is None:
        v = pedido.get("total") or 0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


async def get_bling_revenue(
    session: AsyncSession,
    integration: Integration,
    *,
    start: date,
    end: date,
) -> BlingRevenue | None:
    """Pull all `situacao=9` (NF emitida) pedidos for this integration's
    Bling loja in [start, end]. Aggregates by `dataEmissao` so the
    orchestrator can join against per-day ad spend.

    Returns None when:
      - Bling isn't connected, or
      - the integration has no Bling loja mapping (no override, no
        Store.bling_store_id).
    The caller treats None as "use platform GMV" — that keeps the sync
    working in dev/test where Bling isn't wired up yet."""
    loja_id = await resolve_bling_loja_id(session, integration)
    if loja_id is None:
        return None
    client = await _resolve_bling_client(session)
    if client is None:
        return None

    by_day: dict[date, float] = defaultdict(float)
    total = 0.0
    count = 0
    try:
        async for pedido in client.iter_pedidos_vendas(
            data_inicial=start.isoformat(),
            data_final=end.isoformat(),
            id_situacao=BLING_SITUACAO_FATURADO,  # None → no server-side filter
            id_loja=loja_id,
        ):
            # Exclude cancelled / draft pedidos client-side so revenue
            # doesn't get inflated by orders that never shipped.
            sit_id = (pedido.get("situacao") or {}).get("id")
            try:
                sit_int = int(sit_id) if sit_id is not None else None
            except (TypeError, ValueError):
                sit_int = None
            if sit_int in _EXCLUDED_SITUACOES:
                continue
            d = _parse_bling_date(pedido.get("dataEmissao") or pedido.get("data"))
            v = _pedido_total(pedido)
            if d is not None:
                by_day[d] += v
            total += v
            count += 1
    except Exception as e:  # noqa: BLE001
        # Soft-fail: log and return None so the sync keeps going with
        # platform GMV. The orchestrator surfaces this via the response
        # payload so the operator notices.
        logger.warning(
            "bling_revenue_fetch_failed",
            integration_id=str(integration.id),
            loja_id=loja_id,
            err=str(e)[:200],
        )
        return None
    finally:
        await session.commit()

    return BlingRevenue(total=round(total, 2), by_day=dict(by_day), order_count=count)


async def get_bling_revenue_by_integration_id(
    session: AsyncSession,
    integration_id: UUID,
    *,
    start: date,
    end: date,
) -> BlingRevenue | None:
    """Convenience for callers that only have the integration id (the
    manual-trigger router endpoint, the worker cron). Loads the
    Integration row then delegates."""
    integ = await session.get(Integration, integration_id)
    if integ is None:
        return None
    return await get_bling_revenue(session, integ, start=start, end=end)
