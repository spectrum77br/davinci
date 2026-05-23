"""Polls marketplace APIs (Shopee/ML/Amazon) for shipped-status changes
on Bling orders that haven't yet transitioned to situacao=15 ("Em
andamento") in Bling.

Why this exists:
  Bling does NOT auto-update situacao when a carrier scans a package on
  the marketplace side. The marketplace knows (status becomes SHIPPED /
  shipped / Shipped), but Bling stays on whatever custom "Em aberto"
  state the shop configured (83965 in this account). The
  /controle-estoque page filters by `em_andamento_data` so these
  shipped-but-not-bumped orders never appear.

Strategy:
  1. Find candidates: situacao='83965' AND em_andamento_data IS NULL,
     created within last 7 days. DISTINCT by (bling_id, numeroloja,
     loja) — bling_orders has one row per item.
  2. Group by Bling store id (the `loja` column).
  3. Per store: resolve Store → Integration → marketplace client.
  4. Query the marketplace for each candidate's shipment status. Shopee
     supports up to 50 order_sns per call; ML/Amazon are one-at-a-time.
  5. For each shipped order: bump Bling situacao to 15 via PATCH, then
     stamp em_andamento_data on the local row(s).

Ordering of the two writes matters: Bling first, then local. If Bling
fails we don't touch the local DB — next 5-min tick will retry. If
local fails after Bling succeeds, the Bling webhook will eventually
re-fire `pedido.alteracao.situacao` and `upsert_order` will fill
em_andamento_data via the `_row_from_item` patch. Either way the row
ends up correct.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import BlingOrder, Integration, IntegrationPlatform
from app.models.company import Store
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.amazon import AmazonClient
from app.services.marketplaces.bling import BlingClient
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient

logger = structlog.get_logger()

# Candidate "open" situacao to sweep. The shop uses 83965 as their
# custom "Em aberto" — that's where orders sit between Bling import
# and marketplace shipment. Other 8xxx custom statuses exist but
# don't represent a still-shippable state for this account.
_OPEN_SITUACAO = "83965"
_SHIPPED_SITUACAO = 15  # Bling system situacao "Em andamento".
_CANDIDATE_WINDOW = timedelta(days=7)

# Marketplace status strings that mean "package on the way". Compared
# case-insensitively (we upper() the response before lookup).
_SHOPEE_SHIPPED = {"SHIPPED", "TO_RETURN", "COMPLETED"}
_ML_SHIPPED = {"shipped", "delivered"}
_AMAZON_SHIPPED = {"Shipped"}

# ML shipment substatuses where the seller has NOT yet handed the package.
# Any OTHER substatus under status=ready_to_ship means the package already
# left the seller's hands (dropped_off, in_packing_list, in_hub, first_mile,
# …). On the ML seller UI those all show up as "A caminho", so we treat
# them as shipped on our side too.
_ML_NOT_YET_SHIPPED_SUBSTATUS = {"pending", "printed", "ready_to_print"}


async def run_check_marketplace_shipped_orders() -> dict[str, int]:
    """One sweep. Returns counters for logging/observability."""
    summary = {
        "candidates": 0, "stores_checked": 0, "shipped_found": 0,
        "bling_updated": 0, "local_updated": 0, "errors": 0,
    }
    async with session_scope() as session:
        # One sweep = one DB transaction. The Bling PATCH calls happen
        # inside the transaction; failures roll back the local write,
        # keeping local and Bling in sync (worst case: Bling stamped,
        # local not — webhook will reconcile).
        candidates = await _load_candidates(session)
        summary["candidates"] = len(candidates)
        if not candidates:
            return summary

        # Group by `loja` (Bling store id) so we can resolve one
        # integration + open one client per group.
        by_loja: dict[str, list[BlingOrder]] = defaultdict(list)
        for o in candidates:
            if o.loja:
                by_loja[str(o.loja)].append(o)

        bling_integration = await _get_bling_integration(session)
        if bling_integration is None:
            logger.warning("shipment_check_no_bling_integration")
            return summary

        for loja, orders in by_loja.items():
            try:
                store, integration = await _resolve_store_and_integration(session, loja)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "shipment_check_resolve_failed",
                    loja=loja, err=str(e)[:200],
                )
                summary["errors"] += 1
                continue
            if integration is None or store is None:
                logger.info(
                    "shipment_check_no_integration",
                    loja=loja, candidates=len(orders),
                )
                continue
            summary["stores_checked"] += 1

            try:
                shipped_bling_ids = await _check_marketplace_shipped(
                    session, integration, orders,
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "shipment_check_marketplace_query_failed",
                    loja=loja, platform=integration.platform.value, err=str(e)[:200],
                )
                summary["errors"] += 1
                continue
            summary["shipped_found"] += len(shipped_bling_ids)

            if not shipped_bling_ids:
                continue

            bling_client = await _build_bling_client(session, bling_integration)
            for bling_id in shipped_bling_ids:
                try:
                    await bling_client.update_order_situacao(
                        int(bling_id), _SHIPPED_SITUACAO,
                    )
                    summary["bling_updated"] += 1
                except httpx.HTTPStatusError as e:
                    # Bling returns 4xx if the order is already in the
                    # target situacao — treat as success and still stamp
                    # local. Other 4xx/5xx are real errors.
                    if e.response.status_code in (409, 422, 400):
                        logger.info(
                            "shipment_check_bling_already_state",
                            bling_id=bling_id, status=e.response.status_code,
                        )
                    else:
                        logger.warning(
                            "shipment_check_bling_update_failed",
                            bling_id=bling_id, status=e.response.status_code,
                            body=e.response.text[:200],
                        )
                        summary["errors"] += 1
                        continue
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "shipment_check_bling_update_error",
                        bling_id=bling_id, err=str(e)[:200],
                    )
                    summary["errors"] += 1
                    continue

                # Local stamp — covers every row of this order (multi-item).
                today = datetime.now(UTC).date()
                result = await session.execute(
                    update(BlingOrder)
                    .where(BlingOrder.bling_id == int(bling_id))
                    .values(em_andamento_data=today, situacao=str(_SHIPPED_SITUACAO))
                )
                summary["local_updated"] += result.rowcount or 0

    logger.info("shipment_check_done", **summary)
    return summary


# ─── candidate loading ─────────────────────────────────────────────


async def _load_candidates(session: AsyncSession) -> list[BlingOrder]:
    """One row per (bling_id, numeroloja, loja) — we use item_index=0
    as the canonical row to avoid hitting the marketplace N times for
    one multi-item order."""
    cutoff = datetime.now(UTC) - _CANDIDATE_WINDOW
    rows = (
        await session.execute(
            select(BlingOrder)
            .where(
                and_(
                    BlingOrder.situacao == _OPEN_SITUACAO,
                    BlingOrder.em_andamento_data.is_(None),
                    BlingOrder.created_at >= cutoff,
                    BlingOrder.item_index == 0,  # one row per order
                    BlingOrder.numeroloja.isnot(None),
                    BlingOrder.loja.isnot(None),
                )
            )
            .limit(2000)  # safety: prevent runaway if backlog ever explodes
        )
    ).scalars().all()
    return list(rows)


# ─── store/integration resolution ──────────────────────────────────


async def _resolve_store_and_integration(
    session: AsyncSession, bling_loja_id: str,
) -> tuple[Store | None, Integration | None]:
    """Resolves a Bling store id (`bling_orders.loja`, stored as text)
    to a Store row and its marketplace Integration. Mirrors the
    pattern in marketplace_financials._resolve_store_and_integration."""
    try:
        loja_int = int(bling_loja_id)
    except (TypeError, ValueError):
        return None, None
    store = (
        await session.execute(
            select(Store).where(Store.bling_store_id == loja_int).limit(1)
        )
    ).scalar_one_or_none()
    if store is None:
        return None, None
    integration: Integration | None = None
    if store.integration_id is not None:
        integration = await session.get(Integration, store.integration_id)
    if integration is None:
        # Fallback: find by store + platform. The marketplace_financials
        # helper does the same. Use the store's marketplace enum directly.
        platform = _platform_for_marketplace(store.marketplace.value)
        if platform is not None:
            integration = (
                await session.execute(
                    select(Integration)
                    .where(Integration.store_id == store.id)
                    .where(Integration.platform == platform)
                    .limit(1)
                )
            ).scalar_one_or_none()
    return store, integration


def _platform_for_marketplace(marketplace_value: str) -> IntegrationPlatform | None:
    try:
        return IntegrationPlatform(marketplace_value)
    except ValueError:
        return None


async def _get_bling_integration(session: AsyncSession) -> Integration | None:
    return (
        await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .limit(1)
        )
    ).scalar_one_or_none()


async def _build_bling_client(
    session: AsyncSession, integration: Integration,
) -> BlingClient:
    creds = decrypt_json(integration.credentials)

    async def _persist(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.flush()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integration.id)


# ─── per-marketplace shipment checks ───────────────────────────────


async def _check_marketplace_shipped(
    session: AsyncSession,
    integration: Integration,
    orders: list[BlingOrder],
) -> set[int]:
    """Returns the set of bling_ids that the marketplace reports as
    shipped. Soft-fails per order — one bad fetch doesn't drop the rest."""
    creds = decrypt_json(integration.credentials)

    async def _persist(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.flush()

    platform = integration.platform
    shipped: set[int] = set()

    if platform == IntegrationPlatform.SHOPEE:
        client = ShopeeClient(creds, on_token_refresh=_persist)
        order_sns = [str(o.numeroloja) for o in orders if o.numeroloja]
        status_map = await client.get_order_status_map(order_sns)
        for o in orders:
            if not o.numeroloja or not o.bling_id:
                continue
            status = status_map.get(str(o.numeroloja))
            if status and status in _SHOPEE_SHIPPED:
                shipped.add(int(o.bling_id))
        logger.info(
            "shipment_check_shopee",
            orders=len(orders), found_in_response=len(status_map),
            shipped=len(shipped),
        )

    elif platform == IntegrationPlatform.ML:
        client = MercadoLivreClient(creds, on_token_refresh=_persist)
        for o in orders:
            if not o.numeroloja or not o.bling_id:
                continue
            try:
                data = await client.get_order(str(o.numeroloja))
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "shipment_check_ml_order_failed",
                    numeroloja=o.numeroloja, err=str(e)[:200],
                )
                continue

            # Pass 1 — order/shipping level says shipped/delivered outright.
            ship_status = ((data.get("shipping") or {}) or {}).get("status")
            top_status = data.get("status")
            if (ship_status and str(ship_status).lower() in _ML_SHIPPED) or (
                top_status and str(top_status).lower() in _ML_SHIPPED
            ):
                shipped.add(int(o.bling_id))
                continue

            # Pass 2 — order.status still "paid" but the shipment may
            # already be moving. ML's order endpoint lags the shipment
            # state: once the seller hands the package off and it gets
            # scanned anywhere downstream (agency drop-off, hub, packing
            # list, first mile), `/shipments/{id}` flips to
            # status=ready_to_ship with a substatus that means "no longer
            # with the seller", while the order resource still says
            # status=paid. The seller's job ends the moment the package
            # leaves their hands, so we whitelist by EXCLUSION: any
            # ready_to_ship substatus that isn't one of the three pre-
            # handoff states counts as shipped. (Previous version only
            # caught dropped_off and missed in_hub / in_packing_list /
            # first_mile.) Only fetch the shipment when pass 1 missed.
            shipment_id = (data.get("shipping") or {}).get("id")
            if shipment_id:
                try:
                    ship_data = await client.get_shipment(str(shipment_id))
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "shipment_check_ml_shipment_failed",
                        numeroloja=o.numeroloja, shipment_id=shipment_id,
                        err=str(e)[:200],
                    )
                    continue
                substatus = str(ship_data.get("substatus") or "").lower()
                ship_status2 = str(ship_data.get("status") or "").lower()
                if ship_status2 in _ML_SHIPPED:
                    # shipment-level shipped/delivered → done.
                    shipped.add(int(o.bling_id))
                elif (
                    ship_status2 == "ready_to_ship"
                    and substatus not in _ML_NOT_YET_SHIPPED_SUBSTATUS
                ):
                    # ready_to_ship + any "post-handoff" substatus → done.
                    shipped.add(int(o.bling_id))
        logger.info("shipment_check_ml", orders=len(orders), shipped=len(shipped))

    elif platform == IntegrationPlatform.AMAZON:
        client = AmazonClient(creds, on_token_refresh=_persist)
        for o in orders:
            if not o.numeroloja or not o.bling_id:
                continue
            status = await client.get_order_status(str(o.numeroloja))
            if status and status in _AMAZON_SHIPPED:
                shipped.add(int(o.bling_id))
        logger.info("shipment_check_amazon", orders=len(orders), shipped=len(shipped))

    else:
        logger.info(
            "shipment_check_platform_unsupported",
            platform=platform.value, orders=len(orders),
        )

    return shipped
