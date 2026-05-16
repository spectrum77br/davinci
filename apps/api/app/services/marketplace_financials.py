"""Fetch and persist marketplace financial data for Bling-origin orders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Integration,
    IntegrationPlatform,
    MarketplaceFinancialEvent,
    MarketplaceOrderFinancial,
    MarketplaceOrderFreightReconciliation,
    Store,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.amazon import AmazonClient
from app.services.marketplaces.factory import client_for
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient
from app.services.marketplaces.tiktok import TikTokClient

logger = structlog.get_logger()

RETRYABLE_STATUSES = {"pending", "estimated", "error"}
SUPPORTED_PLATFORMS = {
    IntegrationPlatform.SHOPEE,
    IntegrationPlatform.ML,
    IntegrationPlatform.AMAZON,
    IntegrationPlatform.TIKTOK,
}


@dataclass
class FinancialEventDraft:
    event_type: str
    amount: Decimal
    currency: str = "BRL"
    posted_at: datetime | None = None
    settlement_id: str | None = None
    status: str = "posted"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class FreightReconciliationDraft:
    item_index: int
    status: str
    currency: str = "BRL"
    seller_id: str | None = None
    shipping_id: str | None = None
    pack_id: str | None = None
    shipping_status: str | None = None
    marketplace_item_id: str | None = None
    marketplace_variation_id: str | None = None
    sku: str | None = None
    title: str | None = None
    quantity: Decimal | None = None
    freight_actual_amount: Decimal | None = None
    freight_promised_amount: Decimal | None = None
    freight_list_cost_amount: Decimal | None = None
    freight_discount_rate: Decimal | None = None
    freight_diff_amount: Decimal | None = None
    freight_diff_pct: Decimal | None = None
    dimension_width: Decimal | None = None
    dimension_length: Decimal | None = None
    dimension_height: Decimal | None = None
    dimension_weight: Decimal | None = None
    dimensions_text: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    fetched_at: datetime | None = None
    error: str | None = None


@dataclass
class FinancialSnapshot:
    status: str
    currency: str = "BRL"
    gross_amount: Decimal | None = None
    fee_amount: Decimal | None = None
    freight_amount: Decimal | None = None
    rebate_amount: Decimal | None = None
    discount_amount: Decimal | None = None
    refund_amount: Decimal | None = None
    tax_amount: Decimal | None = None
    adjustment_amount: Decimal | None = None
    net_amount: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    events: list[FinancialEventDraft] = field(default_factory=list)
    freights: list[FreightReconciliationDraft] = field(default_factory=list)
    freight_reconciliation_checked: bool = False
    error: str | None = None


async def run_sync_marketplace_financials_for_bling_order(
    session: AsyncSession,
    *,
    bling_order_id: int,
    trigger: str = "manual",
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(BlingOrder)
            .where(BlingOrder.bling_id == bling_order_id)
            .order_by(BlingOrder.item_index.asc().nullsfirst(), BlingOrder.created_at.asc())
        )
    ).scalars().all()
    if not rows:
        logger.info("marketplace_financials_no_bling_order", bling_order_id=bling_order_id)
        return {"ok": False, "skipped": "bling_order_not_found"}

    order = rows[0]
    if str(order.situacao or "").lower() == "excluido":
        return {"ok": True, "skipped": "deleted_order"}

    external_order_id = _external_order_id(order)
    if not external_order_id:
        return {"ok": True, "skipped": "missing_external_order_id"}

    store, integration = await _resolve_store_and_integration(session, order)
    if integration is None or integration.platform == IntegrationPlatform.BLING:
        return {
            "ok": True,
            "skipped": "marketplace_integration_not_found",
            "store_id": str(store.id) if store else None,
        }

    snapshot = await _fetch_snapshot(session, integration, external_order_id)
    financial = await _persist_snapshot(
        session,
        snapshot,
        platform=integration.platform,
        integration=integration,
        store=store,
        bling_id=bling_order_id,
        pedido_bling=order.numero,
        external_order_id=external_order_id,
    )
    logger.info(
        "marketplace_financials_synced",
        trigger=trigger,
        bling_order_id=bling_order_id,
        platform=integration.platform.value,
        external_order_id=external_order_id,
        status=snapshot.status,
        financial_id=str(financial.id),
    )
    return {
        "ok": snapshot.status not in {"error", "unsupported"},
        "status": snapshot.status,
        "financial_id": str(financial.id),
        "events": len(snapshot.events),
        "freights": len(snapshot.freights),
    }


async def run_due_marketplace_financial_retries(
    session: AsyncSession,
    *,
    limit: int = 100,
    max_attempts: int = 8,
) -> dict[str, int]:
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(MarketplaceOrderFinancial.bling_id)
            .where(MarketplaceOrderFinancial.bling_id.is_not(None))
            .where(MarketplaceOrderFinancial.status.in_(RETRYABLE_STATUSES))
            .where(MarketplaceOrderFinancial.next_retry_at.is_not(None))
            .where(MarketplaceOrderFinancial.next_retry_at <= now)
            .where(MarketplaceOrderFinancial.attempts < max_attempts)
            .order_by(MarketplaceOrderFinancial.next_retry_at.asc())
            .limit(limit)
        )
    ).scalars().all()

    ok = error = 0
    for bling_id in rows:
        try:
            result = await run_sync_marketplace_financials_for_bling_order(
                session,
                bling_order_id=int(bling_id),
                trigger="retry",
            )
            if result.get("ok"):
                ok += 1
            else:
                error += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "marketplace_financials_retry_failed",
                bling_id=bling_id,
                error=str(e)[:500],
            )
            error += 1
    return {"queued": len(rows), "ok": ok, "error": error}


async def _resolve_store_and_integration(
    session: AsyncSession,
    order: BlingOrder,
) -> tuple[Store | None, Integration | None]:
    store = await session.get(Store, order.store_id) if order.store_id else None
    if store is None:
        loja_id = _int(order.loja)
        if loja_id is not None:
            store = (
                await session.execute(
                    select(Store).where(Store.bling_store_id == loja_id).limit(1)
                )
            ).scalar_one_or_none()

    integration: Integration | None = None
    if store is not None and store.integration_id is not None:
        integration = await session.get(Integration, store.integration_id)
    if integration is None and store is not None:
        platform = _integration_platform(str(store.marketplace.value))
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


async def _fetch_snapshot(
    session: AsyncSession,
    integration: Integration,
    external_order_id: str,
) -> FinancialSnapshot:
    if integration.platform not in SUPPORTED_PLATFORMS:
        return FinancialSnapshot(
            status="unsupported",
            raw={"reason": "platform_financial_adapter_not_implemented"},
            error=f"financial adapter not implemented for {integration.platform.value}",
        )

    creds = decrypt_json(integration.credentials)

    async def _persist_refresh(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at") or new_creds.get("token_expires_at")
        if exp:
            try:
                integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
            except (TypeError, ValueError):
                pass
        await session.flush()

    try:
        client = client_for(
            integration.platform,
            creds,
            on_token_refresh=_persist_refresh,
            integration_id=integration.id,
        )
        if integration.platform == IntegrationPlatform.SHOPEE and isinstance(client, ShopeeClient):
            return await _fetch_shopee(client, external_order_id)
        if integration.platform == IntegrationPlatform.ML and isinstance(
            client, MercadoLivreClient
        ):
            return await _fetch_ml(client, external_order_id)
        if integration.platform == IntegrationPlatform.AMAZON and isinstance(client, AmazonClient):
            return await _fetch_amazon(client, external_order_id)
        if integration.platform == IntegrationPlatform.TIKTOK and isinstance(client, TikTokClient):
            return await _fetch_tiktok(client, external_order_id)
        return FinancialSnapshot(
            status="unsupported",
            raw={"reason": "client_type_not_supported"},
            error=f"financial client not supported for {integration.platform.value}",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "marketplace_financials_fetch_error",
            integration_id=str(integration.id),
            platform=integration.platform.value,
            external_order_id=external_order_id,
            error=str(e)[:500],
        )
        return FinancialSnapshot(
            status="error",
            raw={"error": str(e)[:1000]},
            error=str(e)[:1000],
        )


async def _persist_snapshot(
    session: AsyncSession,
    snapshot: FinancialSnapshot,
    *,
    platform: IntegrationPlatform,
    integration: Integration,
    store: Store | None,
    bling_id: int,
    pedido_bling: str | None,
    external_order_id: str,
) -> MarketplaceOrderFinancial:
    financial = (
        await session.execute(
            select(MarketplaceOrderFinancial)
            .where(MarketplaceOrderFinancial.platform == platform)
            .where(MarketplaceOrderFinancial.integration_id == integration.id)
            .where(MarketplaceOrderFinancial.external_order_id == external_order_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if financial is None:
        financial = MarketplaceOrderFinancial(
            platform=platform,
            integration_id=integration.id,
            store_id=store.id if store else None,
            bling_id=bling_id,
            pedido_bling=pedido_bling,
            external_order_id=external_order_id,
        )
        session.add(financial)
        await session.flush()

    attempts = int(financial.attempts or 0) + 1
    now = datetime.now(UTC)
    financial.store_id = store.id if store else financial.store_id
    financial.bling_id = bling_id
    financial.pedido_bling = pedido_bling
    financial.status = snapshot.status
    financial.currency = snapshot.currency or financial.currency or "BRL"
    financial.gross_amount = snapshot.gross_amount
    financial.fee_amount = snapshot.fee_amount
    financial.freight_amount = snapshot.freight_amount
    financial.rebate_amount = snapshot.rebate_amount
    financial.discount_amount = snapshot.discount_amount
    financial.refund_amount = snapshot.refund_amount
    financial.tax_amount = snapshot.tax_amount
    financial.adjustment_amount = snapshot.adjustment_amount
    financial.net_amount = snapshot.net_amount
    financial.raw = snapshot.raw or {}
    financial.fetched_at = now
    financial.attempts = attempts
    financial.last_error = snapshot.error
    financial.next_retry_at = _next_retry_at(snapshot.status, attempts, now)

    await session.execute(
        delete(MarketplaceFinancialEvent).where(
            MarketplaceFinancialEvent.order_financial_id == financial.id
        )
    )
    for event in snapshot.events:
        session.add(
            MarketplaceFinancialEvent(
                order_financial_id=financial.id,
                platform=platform,
                integration_id=integration.id,
                store_id=store.id if store else None,
                bling_id=bling_id,
                external_order_id=external_order_id,
                event_type=event.event_type,
                amount=event.amount,
                currency=event.currency or snapshot.currency or "BRL",
                posted_at=event.posted_at,
                settlement_id=event.settlement_id,
                status=event.status,
                raw=event.raw or {},
            )
        )
    if snapshot.freight_reconciliation_checked:
        await session.execute(
            delete(MarketplaceOrderFreightReconciliation).where(
                MarketplaceOrderFreightReconciliation.order_financial_id == financial.id
            )
        )
        for freight in snapshot.freights:
            session.add(
                MarketplaceOrderFreightReconciliation(
                    order_financial_id=financial.id,
                    platform=platform,
                    integration_id=integration.id,
                    store_id=store.id if store else None,
                    bling_id=bling_id,
                    external_order_id=external_order_id,
                    item_index=freight.item_index,
                    status=freight.status,
                    currency=freight.currency or snapshot.currency or "BRL",
                    seller_id=freight.seller_id,
                    shipping_id=freight.shipping_id,
                    pack_id=freight.pack_id,
                    shipping_status=freight.shipping_status,
                    marketplace_item_id=freight.marketplace_item_id,
                    marketplace_variation_id=freight.marketplace_variation_id,
                    sku=freight.sku,
                    title=freight.title,
                    quantity=freight.quantity,
                    freight_actual_amount=freight.freight_actual_amount,
                    freight_promised_amount=freight.freight_promised_amount,
                    freight_list_cost_amount=freight.freight_list_cost_amount,
                    freight_discount_rate=freight.freight_discount_rate,
                    freight_diff_amount=freight.freight_diff_amount,
                    freight_diff_pct=freight.freight_diff_pct,
                    dimension_width=freight.dimension_width,
                    dimension_length=freight.dimension_length,
                    dimension_height=freight.dimension_height,
                    dimension_weight=freight.dimension_weight,
                    dimensions_text=freight.dimensions_text,
                    raw=freight.raw or {},
                    fetched_at=freight.fetched_at or now,
                    last_error=freight.error,
                )
            )
    await session.flush()
    return financial


async def _fetch_shopee(client: ShopeeClient, order_sn: str) -> FinancialSnapshot:
    escrow = await client.get_escrow_detail(order_sn)
    income = escrow.get("order_income") if isinstance(escrow, dict) else {}
    if not isinstance(income, dict) or not income:
        return FinancialSnapshot(
            status="pending",
            raw={"escrow": escrow},
            error="Shopee escrow detail not available yet",
        )

    currency = _currency_from(income) or "BRL"
    commission = _money(income.get("commission_fee"))
    service = _money(income.get("service_fee"))
    transaction = _money(income.get("seller_transaction_fee"))
    if transaction is None:
        transaction = _money(income.get("transaction_fee"))
    campaign = _money(income.get("campaign_fee"))
    freight = _money(income.get("final_shipping_fee"))
    if freight is None:
        actual = _money(income.get("actual_shipping_fee"))
        rebate = _money(income.get("shopee_shipping_rebate")) or Decimal("0")
        freight = actual - rebate if actual is not None else None
    rebate = _money(income.get("shopee_shipping_rebate"))
    discounts = _sum_money(
        income.get(k)
        for k in (
            "seller_discount",
            "voucher_from_seller",
            "seller_coin_cash_back",
        )
    )
    refund = _sum_money(
        income.get(k)
        for k in ("seller_return_refund", "refund_amount_to_buyer")
    )
    tax = _sum_money(
        income.get(k)
        for k in ("escrow_tax", "final_product_vat_tax", "final_shipping_vat_tax")
    )
    adjustment = _money(income.get("drc_adjustable_refund"))
    gross = (
        _money(income.get("buyer_total_amount"))
        or _money(income.get("original_price"))
        or _money(income.get("cost_of_goods_sold"))
    )
    net = _money(income.get("escrow_amount"))

    events = _compact_events(
        [
            _event(
                "sale",
                gross,
                currency=currency,
                raw={"source": "buyer_total_amount/original_price"},
            ),
            _event("commission_fee", commission, negative=True, currency=currency),
            _event("service_fee", service, negative=True, currency=currency),
            _event("transaction_fee", transaction, negative=True, currency=currency),
            _event("campaign_fee", campaign, negative=True, currency=currency),
            _event("freight", freight, negative=True, currency=currency),
            _event("shipping_rebate", rebate, currency=currency),
            _event("discount", discounts, negative=True, currency=currency),
            _event("refund", refund, negative=True, currency=currency),
            _event("tax", tax, negative=True, currency=currency),
            _event("adjustment", adjustment, negative=True, currency=currency),
            _event("net_payout", net, currency=currency),
        ]
    )
    status = "posted" if net is not None else "pending"
    freights = _shopee_freight_drafts(income, currency)
    return FinancialSnapshot(
        status=status,
        currency=currency,
        gross_amount=gross,
        fee_amount=_abs_sum_money([commission, service, transaction, campaign]),
        freight_amount=abs(freight) if freight is not None else None,
        rebate_amount=rebate,
        discount_amount=discounts,
        refund_amount=refund,
        tax_amount=tax,
        adjustment_amount=adjustment,
        net_amount=net,
        raw={"escrow": escrow},
        events=events,
        freights=freights,
        freight_reconciliation_checked=True,
        error=None if status == "posted" else "Shopee net payout not available yet",
    )


def _shopee_freight_drafts(
    income: dict[str, Any],
    currency: str,
) -> list[FreightReconciliationDraft]:
    """Build one freight reconciliation row per Shopee order.

    Shopee returns shipping data per order (not per shipment) inside
    `order_income`. We map:
      - freight_actual_amount  = actual_shipping_fee (gross cost Shopee charged the seller)
      - freight_promised_amount = buyer_paid_shipping_fee (what buyer paid; may be null)
      - freight_list_cost_amount = forced_shipping_fee (Shopee's "official" cost)
      - freight_diff_amount = actual − buyer_paid (positive = seller out of pocket)
    """
    actual = _money(income.get("actual_shipping_fee"))
    if actual is None:
        return []
    buyer_paid = (
        _money(income.get("buyer_paid_shipping_fee"))
        or _money(income.get("final_shipping_fee"))
    )
    forced = _money(income.get("forced_shipping_fee"))
    rebate = _money(income.get("shopee_shipping_rebate"))
    diff: Decimal | None = None
    diff_pct: Decimal | None = None
    if buyer_paid is not None:
        diff = actual - buyer_paid
        if buyer_paid != 0:
            diff_pct = (diff / buyer_paid) * Decimal(100)
    return [
        FreightReconciliationDraft(
            item_index=0,
            status="posted",
            currency=currency,
            freight_actual_amount=actual,
            freight_promised_amount=buyer_paid,
            freight_list_cost_amount=forced,
            freight_diff_amount=diff,
            freight_diff_pct=diff_pct,
            raw={
                "source": "shopee_escrow_detail",
                "actual_shipping_fee": str(actual),
                "buyer_paid_shipping_fee": str(buyer_paid) if buyer_paid is not None else None,
                "forced_shipping_fee": str(forced) if forced is not None else None,
                "shopee_shipping_rebate": str(rebate) if rebate is not None else None,
            },
        )
    ]


async def _fetch_ml(client: MercadoLivreClient, order_id: str) -> FinancialSnapshot:
    order = await client.get_order(order_id)
    billing: dict[str, Any] | None = None
    billing_error: str | None = None
    try:
        billing = await client.get_billing_order_details(order_id)
    except Exception as e:  # noqa: BLE001
        billing_error = str(e)[:500]

    currency = _currency_from(order) or "BRL"
    items = order.get("order_items") if isinstance(order, dict) else []
    if not isinstance(items, list):
        items = []
    commission = Decimal("0")
    for item in items:
        if not isinstance(item, dict):
            continue
        sale_fee = _money(item.get("sale_fee")) or Decimal("0")
        qty = _money(item.get("quantity")) or Decimal("1")
        commission += sale_fee * qty
    gross = _money(order.get("total_amount")) if isinstance(order, dict) else None
    payments = order.get("payments") if isinstance(order, dict) else []
    payment = payments[0] if isinstance(payments, list) and payments else {}
    payment_shipping_cost = (
        _money(payment.get("shipping_cost")) if isinstance(payment, dict) else None
    )
    freights = await _fetch_ml_freight_reconciliations(client, order, order_id, currency)
    freight_actual_total = _ml_actual_freight_total(freights)
    freight = freight_actual_total or payment_shipping_cost
    net = None
    if gross is not None:
        net = gross - commission - (freight or Decimal("0"))

    has_billing = bool(
        isinstance(billing, dict)
        and isinstance(billing.get("results"), list)
        and billing["results"]
    )
    status = "posted" if has_billing else "estimated"
    events = _compact_events(
        [
            _event("sale", gross, currency=currency),
            _event("commission_fee", commission, negative=True, currency=currency),
            _event(
                "freight",
                freight,
                negative=True,
                currency=currency,
                raw={
                    "source": (
                        "shipments_costs"
                        if freight_actual_total is not None
                        else "payment_shipping_cost"
                    )
                },
            ),
            _event("net_estimated", net, currency=currency, status=status),
        ]
    )
    return FinancialSnapshot(
        status=status,
        currency=currency,
        gross_amount=gross,
        fee_amount=abs(commission),
        freight_amount=abs(freight) if freight is not None else None,
        net_amount=net,
        raw={
            "order": order,
            "billing": billing,
            "billing_error": billing_error,
            "payment_shipping_cost": str(payment_shipping_cost)
            if payment_shipping_cost is not None
            else None,
        },
        events=events,
        freights=freights,
        freight_reconciliation_checked=True,
        error=None if has_billing else billing_error or "ML billing detail not posted yet",
    )


async def _fetch_ml_freight_reconciliations(
    client: MercadoLivreClient,
    order: dict[str, Any],
    order_id: str,
    currency: str,
) -> list[FreightReconciliationDraft]:
    if not isinstance(order, dict):
        return [
            FreightReconciliationDraft(
                item_index=0,
                status="error",
                currency=currency,
                raw={"order_id": order_id},
                error="ML order payload is not a dict",
            )
        ]

    shipping = order.get("shipping") if isinstance(order.get("shipping"), dict) else {}
    seller = order.get("seller") if isinstance(order.get("seller"), dict) else {}
    shipping_id = _text_value(shipping.get("id"))
    seller_id = _text_value(
        client.creds.get("user_id") or seller.get("id") or order.get("seller_id")
    )
    pack_id = _text_value(order.get("pack_id"))
    shipping_status = _text_value(shipping.get("status"))

    order_items = order.get("order_items")
    items = order_items if isinstance(order_items, list) and order_items else [{}]

    costs_payload: dict[str, Any] | None = None
    costs_error: str | None = None
    freight_actual = None
    shipment_items_payload: dict[str, Any] | list[Any] | None = None
    shipment_items_error: str | None = None
    dimensions_by_item: dict[str, dict[str, Decimal | str | None]] = {}

    if shipping_id:
        try:
            costs_payload = await client.get_shipment_costs(shipping_id)
            freight_actual = _ml_sender_cost(costs_payload)
        except Exception as e:  # noqa: BLE001
            costs_error = f"shipment_costs: {str(e)[:300]}"
        try:
            shipment_items_payload = await client.get_shipment_items(shipping_id)
            dimensions_by_item = _ml_dimensions_by_item(shipment_items_payload)
        except Exception as e:  # noqa: BLE001
            shipment_items_error = f"shipment_items: {str(e)[:300]}"
    else:
        costs_error = "missing_shipping_id"

    rows: list[FreightReconciliationDraft] = []
    for item_index, item in enumerate(items):
        item_payload = item if isinstance(item, dict) else {}
        marketplace_item = (
            item_payload.get("item") if isinstance(item_payload.get("item"), dict) else {}
        )
        item_id = _text_value(
            marketplace_item.get("id")
            or item_payload.get("item_id")
            or item_payload.get("item")
        )
        variation_id = _text_value(
            marketplace_item.get("variation_id") or item_payload.get("variation_id")
        )
        sku = _text_value(
            marketplace_item.get("seller_sku")
            or marketplace_item.get("seller_custom_field")
            or item_payload.get("seller_sku")
            or item_payload.get("seller_custom_field")
        )
        title = _text_value(marketplace_item.get("title") or item_payload.get("title"))
        quantity = _quantize_decimal(_decimal_value(item_payload.get("quantity")), "0.0001")

        quote_payload: dict[str, Any] | None = None
        quote_error: str | None = None
        list_cost = rate = promised = None
        if seller_id and item_id:
            try:
                quote_payload = await client.get_free_shipping_options(seller_id, item_id)
                list_cost, rate, promised = _ml_free_shipping_quote(quote_payload)
            except Exception as e:  # noqa: BLE001
                quote_error = f"shipping_options_free: {str(e)[:300]}"
        elif not seller_id:
            quote_error = "missing_seller_id"
        else:
            quote_error = "missing_marketplace_item_id"

        dimensions = _ml_dimensions_for_item(dimensions_by_item, item_id, item_index)
        diff = _money_from_decimal(freight_actual - promised) if (
            freight_actual is not None and promised is not None
        ) else None
        diff_pct = (
            _quantize_decimal((diff / promised) * Decimal("100"), "0.0001")
            if diff is not None and promised not in (None, Decimal("0"))
            else None
        )
        status_errors = [error for error in (costs_error, quote_error) if error]
        all_errors = [
            error
            for error in (costs_error, shipment_items_error, quote_error)
            if error
        ]
        rows.append(
            FreightReconciliationDraft(
                item_index=item_index,
                status=_ml_freight_status(status_errors, freight_actual, promised),
                currency=currency,
                seller_id=seller_id,
                shipping_id=shipping_id,
                pack_id=pack_id,
                shipping_status=shipping_status,
                marketplace_item_id=item_id,
                marketplace_variation_id=variation_id,
                sku=sku,
                title=title,
                quantity=quantity,
                freight_actual_amount=freight_actual,
                freight_promised_amount=promised,
                freight_list_cost_amount=list_cost,
                freight_discount_rate=rate,
                freight_diff_amount=diff,
                freight_diff_pct=diff_pct,
                dimension_width=dimensions.get("width"),
                dimension_length=dimensions.get("length"),
                dimension_height=dimensions.get("height"),
                dimension_weight=dimensions.get("weight"),
                dimensions_text=_text_value(dimensions.get("text")),
                raw={
                    "order_id": order_id,
                    "shipment_costs": costs_payload if item_index == 0 else None,
                    "shipment_items": shipment_items_payload if item_index == 0 else None,
                    "shipping_options_free": quote_payload,
                    "errors": all_errors,
                },
                error="; ".join(all_errors) if all_errors else None,
            )
        )
    return rows


def _ml_sender_cost(payload: dict[str, Any] | None) -> Decimal | None:
    senders = payload.get("senders") if isinstance(payload, dict) else None
    if not isinstance(senders, list) or not senders:
        return None
    sender = senders[0]
    if not isinstance(sender, dict):
        return None
    return _money(sender.get("cost"))


def _ml_free_shipping_quote(
    payload: dict[str, Any] | None,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    coverage = payload.get("coverage") if isinstance(payload, dict) else None
    all_country = coverage.get("all_country") if isinstance(coverage, dict) else None
    if not isinstance(all_country, dict):
        return None, None, None

    list_cost = _money(all_country.get("list_cost") or all_country.get("cost"))
    discount = all_country.get("discount")
    if isinstance(discount, dict):
        rate = _rate_value(discount.get("rate") or discount.get("value"))
    else:
        rate = _rate_value(
            all_country.get("discount_rate")
            or all_country.get("discountRate")
            or discount
        )
    if list_cost is None:
        return None, rate, None
    effective_rate = rate if rate is not None else Decimal("0")
    promised = _money_from_decimal(list_cost * (Decimal("1") - effective_rate))
    return list_cost, rate, promised


def _ml_actual_freight_total(freights: list[FreightReconciliationDraft]) -> Decimal | None:
    total = Decimal("0")
    seen = False
    seen_shipments: set[str] = set()
    for freight in freights:
        if freight.freight_actual_amount is None:
            continue
        key = freight.shipping_id or f"item:{freight.item_index}"
        if key in seen_shipments:
            continue
        seen_shipments.add(key)
        total += freight.freight_actual_amount
        seen = True
    return total if seen else None


def _ml_freight_status(
    errors: list[str],
    actual: Decimal | None,
    promised: Decimal | None,
) -> str:
    if any(error == "missing_shipping_id" for error in errors):
        return "missing_shipping"
    if any(error in {"missing_seller_id", "missing_marketplace_item_id"} for error in errors):
        return "estimated" if actual is not None else "pending"
    if errors:
        return "error"
    if actual is not None and promised is not None:
        return "posted"
    if actual is not None or promised is not None:
        return "estimated"
    return "pending"


def _ml_dimensions_by_item(
    payload: dict[str, Any] | list[Any] | None,
) -> dict[str, dict[str, Decimal | str | None]]:
    entries = payload if isinstance(payload, list) else _list_from(payload, "items")
    if not entries and isinstance(payload, dict):
        entries = [payload]
    dimensions_by_item: dict[str, dict[str, Decimal | str | None]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        dimensions = _ml_dimensions_from(entry)
        if not dimensions:
            continue
        dimensions_by_item[f"index:{index}"] = dimensions
        entry_item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
        item_id = _text_value(entry.get("item_id") or entry.get("id") or entry_item.get("id"))
        if item_id:
            dimensions_by_item[item_id] = dimensions
    return dimensions_by_item


def _ml_dimensions_for_item(
    dimensions_by_item: dict[str, dict[str, Decimal | str | None]],
    item_id: str | None,
    item_index: int,
) -> dict[str, Decimal | str | None]:
    if item_id and item_id in dimensions_by_item:
        return dimensions_by_item[item_id]
    return dimensions_by_item.get(f"index:{item_index}", {})


def _ml_dimensions_from(raw: Any) -> dict[str, Decimal | str | None]:
    dimensions = _find_dimensions_dict(raw)
    if not dimensions:
        return {}

    width = _quantize_decimal(
        _decimal_value(dimensions.get("width") or dimensions.get("width_cm")),
        "0.0001",
    )
    length = _quantize_decimal(
        _decimal_value(
            dimensions.get("length")
            or dimensions.get("depth")
            or dimensions.get("length_cm")
        ),
        "0.0001",
    )
    height = _quantize_decimal(
        _decimal_value(dimensions.get("height") or dimensions.get("height_cm")),
        "0.0001",
    )
    weight = _quantize_decimal(
        _decimal_value(
            dimensions.get("weight")
            or dimensions.get("gross_weight")
            or dimensions.get("weight_g")
        ),
        "0.0001",
    )
    text = _text_value(
        dimensions.get("dimensions")
        or dimensions.get("dimensions_text")
        or dimensions.get("size")
    )
    if not text and width is not None and length is not None and height is not None:
        text = f"{width} x {length} x {height}"
    return {
        "width": width,
        "length": length,
        "height": height,
        "weight": weight,
        "text": text,
    }


def _find_dimensions_dict(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        if any(key in raw for key in ("width", "length", "height")):
            return raw
        if isinstance(raw.get("dimensions"), str):
            return raw
        for key in (
            "dimensions",
            "dimension",
            "dimensions_source",
            "measured",
            "package",
            "shipping",
        ):
            found = _find_dimensions_dict(raw.get(key))
            if found:
                return found
        for value in raw.values():
            found = _find_dimensions_dict(value)
            if found:
                return found
    if isinstance(raw, list):
        for value in raw:
            found = _find_dimensions_dict(value)
            if found:
                return found
    return None


async def _fetch_amazon(client: AmazonClient, order_id: str) -> FinancialSnapshot:
    body = await client.get_finance_transactions_for_order(order_id)
    transactions = _list_from(body, "transactions")
    currency = _currency_from(body) or "BRL"
    events: list[FinancialEventDraft] = []
    net = Decimal("0")
    seen_net = False
    for tx in transactions:
        if not isinstance(tx, dict):
            continue
        amount, cur = _currency_amount(tx.get("totalAmount"))
        currency = cur or currency
        if amount is not None:
            net += amount
            seen_net = True
            events.append(
                FinancialEventDraft(
                    event_type=str(tx.get("transactionType") or "transaction"),
                    amount=amount,
                    currency=currency,
                    raw={"source": "transaction.totalAmount"},
                )
            )
        for item in tx.get("items") or []:
            if isinstance(item, dict):
                events.extend(_events_from_breakdowns(item.get("breakdowns"), currency))

    status = "posted" if transactions else "pending"
    return FinancialSnapshot(
        status=status,
        currency=currency,
        net_amount=net if seen_net else None,
        raw={"transactions": body},
        events=events,
        error=None if transactions else "Amazon finance transaction not posted yet",
    )


async def _fetch_tiktok(client: TikTokClient, order_id: str) -> FinancialSnapshot:
    body = await client.get_order_settlements(order_id)
    code = body.get("code") if isinstance(body, dict) else None
    if code not in (None, 0, "0"):
        raise RuntimeError(f"TikTok finance error: {body.get('message') or code}")
    data = body.get("data") if isinstance(body, dict) else None
    net = _find_first_money(
        data,
        {
            "settlement_amount",
            "settlementAmount",
            "actual_settlement_amount",
            "total_settlement_amount",
            "settled_amount",
        },
    )
    currency = _currency_from(data) or "BRL"
    has_data = bool(data)
    return FinancialSnapshot(
        status="posted" if has_data else "pending",
        currency=currency,
        net_amount=net,
        raw={"settlements": body},
        events=_compact_events([_event("net_payout", net, currency=currency)]),
        error=None if has_data else "TikTok settlement not available yet",
    )


def _external_order_id(order: BlingOrder) -> str | None:
    for raw in (order.numeroloja, order.numero_documento):
        value = (str(raw).strip() if raw is not None else "")
        if value:
            return value
    return None


def _integration_platform(raw: str) -> IntegrationPlatform | None:
    try:
        return IntegrationPlatform(raw)
    except ValueError:
        return None


def _next_retry_at(status: str, attempts: int, now: datetime) -> datetime | None:
    if status not in RETRYABLE_STATUSES:
        return None
    if attempts >= 8:
        return None
    if attempts <= 1:
        return now + timedelta(minutes=30)
    if attempts == 2:
        return now + timedelta(hours=6)
    if attempts <= 4:
        return now + timedelta(days=1)
    return now + timedelta(days=3)


def _money(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        return _money(raw.get("amount") or raw.get("currencyAmount") or raw.get("value"))
    try:
        return Decimal(str(raw)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _money_from_decimal(value: Decimal | None) -> Decimal | None:
    return _quantize_decimal(value, "0.01")


def _quantize_decimal(value: Decimal | None, quantum: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return value.quantize(Decimal(quantum))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _decimal_value(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        for key in ("rate", "value", "amount", "currencyAmount"):
            found = _decimal_value(raw.get(key))
            if found is not None:
                return found
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _rate_value(raw: Any) -> Decimal | None:
    value = _decimal_value(raw)
    if value is None:
        return None
    if abs(value) > 1:
        value = value / Decimal("100")
    return _quantize_decimal(value, "0.000001")


def _text_value(raw: Any) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def _sum_money(values: Any) -> Decimal | None:
    total = Decimal("0")
    seen = False
    for raw in values:
        value = _money(raw)
        if value is None:
            continue
        total += value
        seen = True
    return total if seen else None


def _abs_sum_money(values: list[Decimal | None]) -> Decimal | None:
    total = Decimal("0")
    seen = False
    for value in values:
        if value is None:
            continue
        total += abs(value)
        seen = True
    return total if seen else None


def _event(
    event_type: str,
    amount: Decimal | None,
    *,
    negative: bool = False,
    currency: str = "BRL",
    status: str = "posted",
    raw: dict[str, Any] | None = None,
) -> FinancialEventDraft | None:
    if amount is None or amount == 0:
        return None
    signed = -abs(amount) if negative else amount
    return FinancialEventDraft(
        event_type=event_type,
        amount=signed,
        currency=currency,
        status=status,
        raw=raw or {},
    )


def _compact_events(events: list[FinancialEventDraft | None]) -> list[FinancialEventDraft]:
    return [event for event in events if event is not None]


def _int(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _currency_from(raw: Any) -> str | None:
    if isinstance(raw, dict):
        for key in ("currency", "currency_id", "currencyCode", "currency_code"):
            value = raw.get(key)
            if value:
                return str(value)
        for value in raw.values():
            found = _currency_from(value)
            if found:
                return found
    if isinstance(raw, list):
        for value in raw:
            found = _currency_from(value)
            if found:
                return found
    return None


def _currency_amount(raw: Any) -> tuple[Decimal | None, str | None]:
    if not isinstance(raw, dict):
        return _money(raw), None
    amount = _money(raw.get("currencyAmount") or raw.get("amount") or raw.get("value"))
    currency = _currency_from(raw)
    return amount, currency


def _list_from(raw: Any, key: str) -> list[Any]:
    if isinstance(raw, dict):
        value = raw.get(key)
        if isinstance(value, list):
            return value
        for child in raw.values():
            found = _list_from(child, key)
            if found:
                return found
    return []


def _events_from_breakdowns(raw: Any, currency: str) -> list[FinancialEventDraft]:
    events: list[FinancialEventDraft] = []
    if not isinstance(raw, list):
        return events
    for item in raw:
        if not isinstance(item, dict):
            continue
        amount, cur = _currency_amount(item.get("breakdownAmount"))
        if amount is not None:
            events.append(
                FinancialEventDraft(
                    event_type=str(item.get("breakdownType") or "breakdown"),
                    amount=amount,
                    currency=cur or currency,
                    raw={"source": "amazon_breakdown"},
                )
            )
        events.extend(_events_from_breakdowns(item.get("breakdowns"), cur or currency))
    return events


def _find_first_money(raw: Any, keys: set[str]) -> Decimal | None:
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in keys:
                found = _money(value)
                if found is not None:
                    return found
        for value in raw.values():
            found = _find_first_money(value, keys)
            if found is not None:
                return found
    if isinstance(raw, list):
        for value in raw:
            found = _find_first_money(value, keys)
            if found is not None:
                return found
    return None
