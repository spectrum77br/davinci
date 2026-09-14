"""Validate an Amazon dispatch before accepting Bling's situation 15."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IntegrationPlatform
from app.security.cipher import decrypt_json, encrypt_json
from app.services.amazon_shipment_status import amazon_shipment_confirmed
from app.services.marketplace_shipment_check import _resolve_store_and_integration
from app.services.marketplaces.amazon import AmazonClient

logger = structlog.get_logger()


async def amazon_bling_dispatch_blocked(
    session: AsyncSession,
    *,
    loja: str | None,
    order_id: str | None,
) -> bool:
    """Other marketplaces pass through; Amazon needs positive dispatch evidence.

    Missing credentials, shipment fields or a temporary API failure leave the
    order pending. The shipment sweep will retry it without creating an envio.
    """
    store, integration = await _resolve_store_and_integration(session, loja)
    if store is None or store.marketplace.value != "amazon":
        return False
    if not order_id or integration is None or integration.platform != IntegrationPlatform.AMAZON:
        return True

    async def persist(creds: dict) -> None:
        integration.credentials = encrypt_json(creds)
        if creds.get("expires_at"):
            integration.token_expires_at = datetime.fromtimestamp(int(creds["expires_at"]), tz=UTC)
        await session.flush()

    try:
        client = AmazonClient(decrypt_json(integration.credentials), on_token_refresh=persist)
        status = await client.get_order_status(order_id)
    except Exception as exc:  # noqa: BLE001 — no confirmation means pending
        logger.warning(
            "amazon_bling_dispatch_unavailable", order_id=order_id, error_type=type(exc).__name__
        )
        return True
    return not amazon_shipment_confirmed(status)
