"""Fetch and persist marketplace financial data for Bling-origin orders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
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
from app.services.refunds_freight_sync import upsert_freight_refund_for_bling_order
from app.services.verificar_margem import refresh_silent as _verificar_margem_refresh_silent

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

    # SKUs of every line of this Bling order — used to match ML pack
    # sub-orders so a consolidated (whole-pack) Bling order aggregates all
    # siblings, while a split (one-order-per-sub) Bling order does not.
    bling_skus = {
        str(r.item_codigo).strip().lower()
        for r in rows
        if r.item_codigo and str(r.item_codigo).strip()
    } or None
    snapshot = await _fetch_snapshot(
        session, integration, external_order_id, bling_skus=bling_skus
    )
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

    # Refund auto-row: now that financial events are persisted, the view
    # has fresh data — upsert any Frete refund triggered by this pedido.
    # Best-effort: a failure here must not break the financial sync.
    if snapshot.status not in {"error", "unsupported"} and order.numero:
        try:
            await upsert_freight_refund_for_bling_order(session, pedido_bling=order.numero)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "refunds_freight_upsert_failed",
                bling_order_id=bling_order_id,
                pedido_bling=order.numero,
                error=str(e)[:500],
            )

    # Bulk (webhook/retry) PULA o refresh pesado serializado pelo advisory lock —
    # a taxa real propaga via rebuild_all (load da página /margem 5min + cron
    # 30min), igual ao desacoplamento dos re-syncs (bling_orders.
    # _DEFER_MARGEM_REFRESH_EVENTS). Só o fetch MANUAL de 1 pedido refresca na
    # hora. Destrava a vazão da fila davinci_financials (estava presa no lock,
    # ~6/min; agora limitada só pela latência da API do marketplace).
    if trigger == "manual":
        await _verificar_margem_refresh_silent(session, bling_id=bling_order_id)

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


def _tiktok_unsettled_order_map(pages: list[dict]) -> dict[str, dict]:
    """order_id → linha ORDER agregada do unsettled (est_* somados).

    Só linhas do PEDIDO em si (order_id presente, sem adjustment_id) —
    ajustes avulsos têm dono (`adjustment_order_id`) mas não são o repasse
    base do pedido. Pedido com mais de uma linha (refund parcial) soma os
    est_settlement_amount (mesma regra de sinais do settlement real).
    """
    out: dict[str, dict] = {}
    for body in pages:
        data = body.get("data") if isinstance(body, dict) else None
        txs = data.get("transactions") if isinstance(data, dict) else None
        for t in txs if isinstance(txs, list) else []:
            if not isinstance(t, dict) or t.get("adjustment_id"):
                continue
            order_id = str(t.get("order_id") or "").strip()
            est = _money(t.get("est_settlement_amount"))
            if not order_id or est is None:
                continue
            slot = out.setdefault(
                order_id,
                {
                    "est_settlement_amount": Decimal("0"),
                    "currency": t.get("currency") or "BRL",
                    "unsettled_reason": t.get("unsettled_reason"),
                    "estimated_settlement": t.get("estimated_settlement"),
                },
            )
            slot["est_settlement_amount"] += est
    for slot in out.values():
        slot["est_settlement_amount"] = str(slot["est_settlement_amount"])
    return out


async def run_tiktok_unsettled_sweep(
    session: AsyncSession,
    *,
    client_factory=None,
    window_days: int = 60,
    max_pages: int = 10,
    recent_hours: int | None = None,
) -> dict[str, int]:
    """Preenche o líquido TikTok com a estimativa OFICIAL pré-liquidação.

    Reclamação do Eduardo (01-02/09): "sempre aparece já o valor do saldo
    na Central do Vendedor" — mas o statement real
    (get_order_settlements) só sai dias após a entrega, então a Margem
    ficava dias com o Saldo Plataforma em branco ("aguardando saldo da
    plataforma"). O unsettled (GET /finance/202507/orders/unsettled) traz o
    MESMO número que a Central mostra na hora (est_settlement_amount, por
    pedido, disponível já no dia da venda).

    Uma varredura paginada POR INTEGRAÇÃO (não por pedido — o endpoint é de
    janela, e chamada por pedido estouraria o rate limit compartilhado que o
    TikTok já acusou em 01/09, code 36009002): ~2 páginas cobrem a loja
    inteira. Grava net_amount + status='estimated' (o MESMO padrão do ML
    pré-billing, retryable) e o marcador raw.unsettled_estimate — que o
    guard do _persist_snapshot preserva quando um retry volta vazio. O
    settlement REAL substitui tudo quando postar (posted vence sempre).
    attempts/next_retry_at ficam intocados: são do ciclo do settlement real.

    recent_hours (a via expressa do worker, minuto a minuto — Eduardo 01/09:
    "tem que ficar o mais rapido possivel... nao pode mais ficar 10 min",
    a aprovação vai virar automática): considera SÓ candidatos criados nas
    últimas N horas e AINDA sem estimativa. Sem nenhum, retorna sem tocar a
    API (custo zero na maioria dos minutos); com algum, 1 página por loja
    afetada (sort DESC pega os mais novos) resolve. O TikTok libera a
    estimativa ~30-60min após a venda (medido no 293707) — passada a janela
    das N horas, o caso raro fica para a varredura completa de 10min.
    """
    stmt = (
        select(MarketplaceOrderFinancial)
        .where(MarketplaceOrderFinancial.platform == IntegrationPlatform.TIKTOK)
        .where(MarketplaceOrderFinancial.status.in_(RETRYABLE_STATUSES))
        .where(MarketplaceOrderFinancial.integration_id.is_not(None))
    )
    if recent_hours is not None:
        stmt = stmt.where(
            MarketplaceOrderFinancial.created_at
            >= datetime.now(UTC) - timedelta(hours=recent_hours)
        ).where(~MarketplaceOrderFinancial.raw.has_key("unsettled_estimate"))
    candidatos = (await session.execute(stmt)).scalars().all()
    if not candidatos:
        return {"integrations": 0, "candidates": 0, "updated": 0}

    por_integracao: dict[Any, list[MarketplaceOrderFinancial]] = {}
    for c in candidatos:
        por_integracao.setdefault(c.integration_id, []).append(c)

    now = datetime.now(UTC)
    updated = swept = 0
    for integration_id, rows in por_integracao.items():
        integ = await session.get(Integration, integration_id)
        if integ is None or integ.status != "active":
            continue
        if client_factory is not None:
            client = client_factory(integ)
        else:
            client = TikTokClient(decrypt_json(integ.credentials))

        pages: list[dict] = []
        page_token: str | None = None
        try:
            for _ in range(max_pages):
                body = await client.get_unsettled_orders(
                    search_time_ge=int(now.timestamp()) - window_days * 86400,
                    search_time_lt=int(now.timestamp()),
                    page_token=page_token,
                )
                code = body.get("code")
                if code not in (0, "0", None):
                    raise RuntimeError(
                        f"TikTok unsettled error: {body.get('message') or code}"
                    )
                pages.append(body)
                page_token = (body.get("data") or {}).get("next_page_token") or None
                if not page_token:
                    break
        except Exception as e:  # noqa: BLE001 — uma loja não derruba as demais
            logger.warning(
                "tiktok_unsettled_sweep_integration_failed",
                integration_id=str(integration_id),
                error=str(e)[:300],
            )
            continue

        swept += 1
        estimados = _tiktok_unsettled_order_map(pages)
        for financial in rows:
            est = estimados.get(str(financial.external_order_id))
            if est is None:
                continue
            net = _money(est.get("est_settlement_amount"))
            if net is None:
                continue
            if financial.status == "estimated" and financial.net_amount == net:
                continue  # nada novo — não suja updated_at
            financial.status = "estimated"
            financial.net_amount = net
            financial.currency = str(est.get("currency") or financial.currency or "BRL")
            financial.raw = {
                **(financial.raw or {}),
                "unsettled_estimate": {**est, "fetched_at": now.isoformat()},
            }
            financial.last_error = None
            financial.fetched_at = now
            updated += 1

    await session.commit()
    return {
        "integrations": swept,
        "candidates": len(candidatos),
        "updated": updated,
    }


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
    *,
    bling_skus: set[str] | None = None,
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
            return await _fetch_ml(client, external_order_id, bling_skus=bling_skus)
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

    # Estimativa do unsettled (TikTok, gravada pelo sweep) só cede lugar a
    # DADO REAL: um retry que voltou sem valor (settlement ainda não postado /
    # erro transiente) não pode apagar o número que a Margem já está usando —
    # senão a linha "pisca" (valor → branco → valor) a cada ciclo do retry.
    # Snapshot com net presente (posted/estimated do próprio fetch) SEMPRE
    # vence e derruba o marcador.
    estimate = (financial.raw or {}).get("unsettled_estimate")
    keep_estimate = (
        snapshot.net_amount is None
        and snapshot.status in {"pending", "error"}
        and isinstance(estimate, dict)
        and _money(estimate.get("est_settlement_amount")) is not None
    )

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
    if keep_estimate:
        financial.status = "estimated"
        financial.net_amount = _money(estimate.get("est_settlement_amount"))
        financial.raw = {**(snapshot.raw or {}), "unsettled_estimate": estimate}

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

    frete_anuncio = _shopee_frete_anuncio(income)
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
            # `frete_anuncio` is the per-unit fixed component of the Shopee BR
            # commission table — already embedded inside `service_fee`. Emitted as
            # an info-only event so it appears in event_totals / the view without
            # being summed into net_payout (which already accounts for service_fee).
            _event("frete_anuncio", frete_anuncio, currency=currency),
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


# Shopee BR commission-table fixed component ("taxa fixa do anúncio"), indexed by
# per-unit "valor do item". Already embedded in `service_fee` — exposed as a
# separate event for visibility/auditing only. Cumulative thresholds; first match wins.
_SHOPEE_FRETE_ANUNCIO_BUCKETS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("79.99"), Decimal("4")),
    (Decimal("99.99"), Decimal("16")),
    (Decimal("199.99"), Decimal("20")),
)
_SHOPEE_FRETE_ANUNCIO_DEFAULT = Decimal("26")  # R$200 ou mais


def _shopee_frete_anuncio(income: dict[str, Any]) -> Decimal | None:
    """Compute the 'frete anúncio' (taxa fixa do anúncio) per Shopee BR table.

    The fee is charged per unit sold: bucket by `selling_price / quantity_purchased`
    and multiply by `quantity_purchased`. Sum across items.
    """
    items = income.get("items") if isinstance(income, dict) else None
    if not isinstance(items, list) or not items:
        return None
    total = Decimal("0")
    found_any = False
    for it in items:
        if not isinstance(it, dict):
            continue
        qty = _money(it.get("quantity_purchased"))
        line_price = _money(it.get("selling_price")) or _money(it.get("discounted_price"))
        if qty is None or qty <= 0 or line_price is None or line_price <= 0:
            continue
        unit_price = line_price / qty
        fixed = _SHOPEE_FRETE_ANUNCIO_DEFAULT
        for threshold, value in _SHOPEE_FRETE_ANUNCIO_BUCKETS:
            if unit_price <= threshold:
                fixed = value
                break
        total += fixed * qty
        found_any = True
    return total if found_any else None


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


def _ml_order_item_skus(order: Any) -> set[str]:
    """Lowercased seller SKUs of an ML order's items (seller_sku or
    seller_custom_field). Used to match pack sub-orders to a Bling order."""
    skus: set[str] = set()
    items = order.get("order_items") if isinstance(order, dict) else None
    if not isinstance(items, list):
        return skus
    for entry in items:
        if not isinstance(entry, dict):
            continue
        item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
        for key in (item.get("seller_sku"), item.get("seller_custom_field")):
            value = _text_value(key)
            if value:
                skus.add(value.lower())
    return skus


def _ml_order_commission(order: Any) -> Decimal:
    """sum(sale_fee * quantity) over an ML order's items."""
    commission = Decimal("0")
    items = order.get("order_items") if isinstance(order, dict) else None
    if not isinstance(items, list):
        return commission
    for item in items:
        if not isinstance(item, dict):
            continue
        sale_fee = _money(item.get("sale_fee")) or Decimal("0")
        qty = _money(item.get("quantity")) or Decimal("1")
        commission += sale_fee * qty
    return commission


def _ml_seller_funded_discount(discounts: Any) -> Decimal | None:
    """Seller-funded discount from ML's `/orders/{id}/discounts` breakdown.

    Sums `items[].amounts.seller` across details, EXCLUDING every *offer* — a
    detail whose `supplier` carries an `offer_id` (regardless of funding_mode),
    or `funding_mode == "sale_fee"`. An offer/campaign price cut is already baked
    into `unit_price`/`total_amount` (and the commission base), so subtracting it
    again double-counts. ML's own repasse agrees: for offers the billing detail
    reports `sale_fee.discount == 0`. What remains is the seller's real
    out-of-pocket on *coupons* (no `offer_id`); ML-funded promo coupons
    contribute 0 (they report `seller: 0`).

    Examples: pedido 282077 → R$2,00 coupon share is kept (net 557,91); pedido
    285633 → R$30,00 seller-funded OFFER is skipped (net 155,47). Keying the
    exclusion only on `funding_mode == "sale_fee"` let an offer with
    `funding_mode == "seller"` slip through and double-counted it (155,47 → 125,47).

    Returns None when the breakdown is missing/empty so the caller can fall back.
    """
    if not isinstance(discounts, dict):
        return None
    details = discounts.get("details")
    if not isinstance(details, list) or not details:
        return None
    total = Decimal("0")
    for det in details:
        if not isinstance(det, dict):
            continue
        supplier = det.get("supplier") or {}
        # Offers (an `offer_id`, any funding_mode incl. sale_fee) are a campaign
        # price cut already reflected in unit_price — skip. Only non-offer
        # coupons reduce the repasse by the seller's funded share.
        if isinstance(supplier, dict) and (
            supplier.get("offer_id") or supplier.get("funding_mode") == "sale_fee"
        ):
            continue
        for it in det.get("items") or []:
            if not isinstance(it, dict):
                continue
            amounts = it.get("amounts") or {}
            if isinstance(amounts, dict):
                total += _money(amounts.get("seller")) or Decimal("0")
    return total


def _ml_billing_seller_discount(billing: Any) -> Decimal | None:
    """Real *seller-funded* discount from the ML billing detail (repasse).

    `payment.coupon_amount` is the buyer's checkout discount — but ML reimburses
    promotional ("MELI") coupons, crediting them back to the seller as
    "Recebimento pelo desconto da sua contraparte". So coupon_amount is NOT the
    seller's cost: deducting it understated the net by the whole coupon (seen on
    pedidos 282077/282055, R$57,76 / R$69,90 off). The authoritative amount the
    seller actually loses lives in the billing detail's `sale_fee.discount` and
    per-detail `discount_info.discount_amount` (both 0 for ML-funded coupons).

    Returns None when billing isn't posted yet (`estimated`) — caller then
    deducts nothing, i.e. assumes the coupon was ML-funded (the common case).
    """
    if not isinstance(billing, dict):
        return None
    results = billing.get("results")
    if not isinstance(results, list) or not results:
        return None
    total = Decimal("0")
    for res in results:
        if not isinstance(res, dict):
            continue
        sale_fee = res.get("sale_fee")
        if isinstance(sale_fee, dict):
            total += _money(sale_fee.get("discount")) or Decimal("0")
        for det in res.get("details") or []:
            if not isinstance(det, dict):
                continue
            di = det.get("discount_info")
            if isinstance(di, dict):
                total += _money(di.get("discount_amount")) or Decimal("0")
    return total


async def _fetch_ml(
    client: MercadoLivreClient,
    order_id: str,
    *,
    bling_skus: set[str] | None = None,
) -> FinancialSnapshot:
    try:
        order = await client.get_order(order_id)
    except httpx.HTTPStatusError as e:
        # Bling guarda o *pack_id* do ML em `numeroloja` para alguns pedidos, e
        # `/orders/{pack_id}` responde 404 (um pack não é um pedido). Resolve o
        # pack e re-ancora no primeiro pedido membro; a expansão de pack abaixo
        # agrega os irmãos restantes. Repropaga qualquer outro status (ex.: 403).
        if e.response.status_code != 404:
            raise
        pack = await client.get_pack(order_id)
        member_ids = [
            oid
            for o in (pack.get("orders") or [])
            if isinstance(o, dict) and (oid := _text_value(o.get("id")))
        ]
        if not member_ids:
            raise
        order_id = member_ids[0]
        order = await client.get_order(order_id)
    billing: dict[str, Any] | None = None
    billing_error: str | None = None
    try:
        billing = await client.get_billing_order_details(order_id)
    except Exception as e:  # noqa: BLE001
        billing_error = str(e)[:500]

    currency = _currency_from(order) or "BRL"

    # --- Pack expansion -------------------------------------------------
    # ML groups items bought together into a *pack*; each item can be a
    # separate order_id (sharing one shipment). Bling, however, may
    # consolidate the whole pack under a single order whose `numeroloja` is
    # just ONE of the sub-order ids. Fetching only that sub-order undercounts
    # gross/commission and wrecks the marketplace margin. So when the order
    # belongs to a pack we aggregate the sibling orders that correspond to
    # this Bling order's lines (matched by SKU, to avoid double counting when
    # Bling instead split the pack into multiple orders).
    matched_orders: list[dict[str, Any]] = [order] if isinstance(order, dict) else []
    pack_order_ids: list[str] = []
    pack_error: str | None = None
    primary_id = _text_value(order.get("id")) if isinstance(order, dict) else None
    pack_id = _text_value(order.get("pack_id")) if isinstance(order, dict) else None
    skus_filter = {s.lower() for s in bling_skus} if bling_skus else None
    if pack_id:
        try:
            pack = await client.get_pack(pack_id)
            pack_order_ids = [
                oid
                for o in (pack.get("orders") or [])
                if isinstance(o, dict) and (oid := _text_value(o.get("id")))
            ]
        except Exception as e:  # noqa: BLE001
            pack_error = str(e)[:300]
        for oid in pack_order_ids:
            if oid in {_text_value(order_id), primary_id}:
                continue
            try:
                sibling = await client.get_order(oid)
            except Exception:  # noqa: BLE001 — skip unreachable sibling, keep going
                continue
            if skus_filter is not None and not (_ml_order_item_skus(sibling) & skus_filter):
                continue
            matched_orders.append(sibling)

    # --- Aggregate money across matched orders --------------------------
    gross = _sum_money(o.get("total_amount") for o in matched_orders)
    commission = sum((_ml_order_commission(o) for o in matched_orders), Decimal("0"))
    # Seller-funded discount — NOT `payment.coupon_amount`. ML reimburses promo
    # coupons (they show as coupon_amount but are credited back as "Recebimento
    # pelo desconto da sua contraparte"), so deducting coupon_amount understated
    # the net by the whole coupon (pedidos 282077/282055: R$57,76 / R$69,90 off).
    # `/orders/{id}/discounts` splits each discount by who funded it; we deduct
    # only the seller's share (excl. sale_fee-funded offers already in the
    # price). Gated on the `order_has_discount` tag to avoid an extra ML call on
    # the (common) no-discount orders. Falls back to the billing detail's
    # seller charge if the breakdown call fails.
    order_discounts: dict[str, Any] | None = None
    discount: Decimal | None = None
    has_discount = isinstance(order, dict) and "order_has_discount" in (
        order.get("tags") or []
    )
    if has_discount:
        try:
            order_discounts = await client.get_order_discounts(order_id)
            discount = _ml_seller_funded_discount(order_discounts)
        except Exception as e:  # noqa: BLE001 — fall back to billing on any failure
            logger.warning(
                "ml_order_discounts_failed", order_id=order_id, error=str(e)[:300]
            )
            discount = _ml_billing_seller_discount(billing)

    payments = order.get("payments") if isinstance(order, dict) else []
    if not isinstance(payments, list):
        payments = []
    payment = payments[0] if payments else {}
    payment_shipping_cost = (
        _money(payment.get("shipping_cost")) if isinstance(payment, dict) else None
    )
    # Freight is shipment-level: every sub-order of a pack/carrinho shares
    # one shipping_id and /shipments/costs returns the WHOLE shipment cost.
    # `_fetch_ml_freight_reconciliations` prorates it by this order's share
    # of the shipment (`_ml_order_freight_share`), so multi-order shipments
    # don't dump the full freight on a single order
    # (`_ml_actual_freight_total` still dedups by shipping_id within the
    # order).
    freights = await _fetch_ml_freight_reconciliations(client, order, order_id, currency)
    freight_actual_total = _ml_actual_freight_total(freights)
    freight = freight_actual_total or payment_shipping_cost
    net = None
    if gross is not None:
        net = (
            gross
            - commission
            - (freight or Decimal("0"))
            - (discount or Decimal("0"))
        )

    has_billing = bool(
        isinstance(billing, dict)
        and isinstance(billing.get("results"), list)
        and billing["results"]
    )
    status = "posted" if has_billing else "estimated"
    # `frete_anuncio` for ML = sum of `freight_promised_amount` across items
    # (= list_cost * (1 - discount.rate) from /shipping_options/free, per item).
    # Same event name as Shopee so the view exposes both uniformly.
    frete_anuncio_total = _ml_frete_anuncio_total(freights)
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
            _event("frete_anuncio", frete_anuncio_total, currency=currency),
            _event(
                "discount",
                discount,
                negative=True,
                currency=currency,
                raw={"source": "orders/{id}/discounts amounts.seller (excl. offers)"},
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
        discount_amount=discount,
        net_amount=net,
        raw={
            "order": order,
            "billing": billing,
            "billing_error": billing_error,
            "order_discounts": order_discounts,
            "payment_shipping_cost": str(payment_shipping_cost)
            if payment_shipping_cost is not None
            else None,
            "pack_id": pack_id,
            "pack_order_ids": pack_order_ids or None,
            "pack_matched_order_ids": [
                _text_value(o.get("id")) for o in matched_orders
            ]
            if pack_id
            else None,
            "pack_error": pack_error,
            "sibling_orders": [o for o in matched_orders[1:]] or None,
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
        # Envio de carrinho: um shipping_id pode agrupar VARIOS pedidos do
        # mesmo comprador, e /shipments/costs traz o custo do pacote
        # INTEIRO. Atribuir tudo a um pedido só inflava o frete real (ex.:
        # envio 47116394474: 30 un / 29 pedidos, custo 228,00 = 30 x 7,60;
        # o pedido 2000016540781180, com 1 un, ficava com 228 e gerava
        # refund Logistica falso de 220,40). Rateia pela participação do
        # pedido no pacote (peso x qty; fallback qty).
        if freight_actual is not None:
            share = _ml_order_freight_share(shipment_items_payload, order_id)
            if share is not None and share < 1:
                freight_actual = _money_from_decimal(freight_actual * share)
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
                promised = _ml_promised_line_total(promised, quantity)
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


def _ml_order_freight_share(
    shipment_items_payload: dict[str, Any] | list[Any] | None,
    order_id: str | None,
) -> Decimal | None:
    """Participação do pedido no custo do envio (0 < share <= 1).

    Pondera por peso x quantidade quando todos os itens têm peso; senão
    por quantidade. Retorna None se o payload não permitir calcular
    (sem itens, pedido ausente do envio, totais zerados) — nesse caso o
    chamador mantém o custo cheio (comportamento antigo).
    """
    if not isinstance(shipment_items_payload, list) or not order_id:
        return None
    total = Decimal("0")
    mine = Decimal("0")
    weights: list[Decimal | None] = []
    rows: list[tuple[str | None, Decimal, Decimal | None]] = []
    for it in shipment_items_payload:
        if not isinstance(it, dict):
            continue
        qty = _decimal_value(it.get("quantity")) or Decimal("1")
        dims = it.get("dimensions") if isinstance(it.get("dimensions"), dict) else {}
        weight = _decimal_value(dims.get("weight"))
        weights.append(weight)
        rows.append((_text_value(it.get("order_id")), qty, weight))
    if not rows:
        return None
    use_weight = all(w is not None and w > 0 for w in weights)
    for it_order_id, qty, weight in rows:
        value = qty * weight if use_weight else qty
        total += value
        if it_order_id == str(order_id):
            mine += value
    if total <= 0 or mine <= 0:
        return None
    return mine / total


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
    # `list_cost` já vem com o desconto aplicado pelo ML: o payload traz
    # discount.promoted_amount = preço cheio e list_cost = valor final que o
    # vendedor paga (confirmado contra senders[0].cost e a tela da venda).
    # Aplicar (1 - rate) aqui descontava DUAS vezes (ex.: pedido
    # 2000016853024850 -> 11,82 em vez de 23,65). O rate fica armazenado
    # apenas como informativo.
    promised = _money_from_decimal(list_cost)
    return list_cost, rate, promised


def _ml_promised_line_total(
    promised: Decimal | None, quantity: Decimal | None
) -> Decimal | None:
    """Frete prometido TOTAL da linha = cotação unitária x quantidade.

    A cotação de /shipping_options/free é por unidade, mas o ML cobra o
    frete grátis por unidade vendida (ex.: pedido 2000016849422942,
    qty=2: cobrado 13,50 = 6,75 x 2 — confirmado em 81/113 pedidos ML de
    linha única com qty>1). Sem multiplicar, todo pedido multi-unidade
    parecia ter prejuízo de frete e gerava refund Logistica falso.
    `freight_list_cost_amount` segue unitário (cotação crua).
    """
    if promised is None:
        return None
    if quantity is None or quantity <= 0:
        return promised
    return _money_from_decimal(promised * quantity)


def _ml_frete_anuncio_total(
    freights: list[FreightReconciliationDraft],
) -> Decimal | None:
    """Sum `freight_promised_amount` across ML freight rows.

    `freight_promised_amount` per row = list_cost from
    /shipping_options/free (já líquido de desconto) x quantidade da
    linha — the freight the seller "would pay" for the line according
    to the listing's quote. Returns None if no item has a quote.
    """
    total = Decimal("0")
    seen = False
    for freight in freights:
        if freight.freight_promised_amount is None:
            continue
        total += freight.freight_promised_amount
        seen = True
    return total if seen else None


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

    # Frete plataforma (Amazon MFN): a etiqueta comprada via Amazon chega nos
    # breakdowns como MFNPostageFee (valor negativo = débito do seller).
    # Consolida num evento 'freight' — a view de margens soma
    # event_type='freight' em evento_freight e a página Margem exibe como
    # "Frete Plataforma" (mesmo caminho da Shopee, ver _FRETE_PLATAFORMA_SQL).
    postage = sum(
        (e.amount for e in events if e.event_type == "MFNPostageFee"),
        Decimal("0"),
    )
    freight_event = _event(
        "freight", postage, currency=currency, raw={"source": "MFNPostageFee"}
    )
    if freight_event is not None:
        events.append(freight_event)

    status = "posted" if transactions else "pending"
    return FinancialSnapshot(
        status=status,
        currency=currency,
        net_amount=net if seen_net else None,
        raw={"transactions": body},
        events=events,
        error=None if transactions else "Amazon finance transaction not posted yet",
    )


# TikTok finance responses that mean "we can't get this via API (yet)" rather
# than a transient failure. Treated as `unsupported` (NOT in RETRYABLE_STATUSES)
# so the order isn't re-queued forever:
#   - 401 / 105005: the app lacks the Finance access scope (needs Partner Center
#     grant + re-OAuth so the new token carries the scope).
#   - 410 / 36009034: the legacy V1 endpoint was retired (defensive — we already
#     call V2, but keep it in case TikTok deprecates more).
# The day the Finance scope is granted, the very same code path starts returning
# real settlement data with no further changes here.
_TIKTOK_BLOCKED_CODES = {401, 403, 410, 105005, 36009034}


def _tiktok_finance_blocked(code: Any, message: str | None) -> bool:
    try:
        if int(code) in _TIKTOK_BLOCKED_CODES:
            return True
    except (TypeError, ValueError):
        pass
    msg = (message or "").lower()
    return any(
        kw in msg
        for kw in ("access denied", "not authorized", "access scope", "deprecat")
    )


async def _fetch_tiktok(client: TikTokClient, order_id: str) -> FinancialSnapshot:
    body = await client.get_order_settlements(order_id)
    code = body.get("code") if isinstance(body, dict) else None
    message = body.get("message") if isinstance(body, dict) else None

    if _tiktok_finance_blocked(code, message):
        return FinancialSnapshot(
            status="unsupported",
            raw={"settlements": body},
            error=f"TikTok finance unavailable (scope/endpoint): {message or code}",
        )

    if code not in (None, 0, "0"):
        raise RuntimeError(f"TikTok finance error: {message or code}")

    data = body.get("data") if isinstance(body, dict) else None
    # v202309 nests one entry per (sub-)transaction under
    # data.statement_transactions[]; a refunded order yields more than one, so
    # every money field is SUMMED across them (refunds net out). Verified
    # against live SETTLED orders: settlement_amount = revenue_amount +
    # fee_amount + shipping_cost_amount (all signed — fees/shipping/refunds are
    # negative, revenue/subsidies positive). E.g. 2561 + (-207.66) + (-6.1) =
    # 2347.24 on a R$2560.80 sale; a fully-refunded order settles negative.
    txs = data.get("statement_transactions") if isinstance(data, dict) else None
    txs = [t for t in txs if isinstance(t, dict)] if isinstance(txs, list) else []
    currency = _currency_from(data) or "BRL"

    def _agg(*keys: str) -> Decimal | None:
        """Sum one or more signed money fields across all transactions."""
        total = Decimal("0")
        seen = False
        for t in txs:
            for k in keys:
                amt = _money(t.get(k))
                if amt is not None:
                    total += amt
                    seen = True
        return total if seen else None

    net = _agg("settlement_amount")
    if net is None:
        # Defensive: unexpected shape / single top-level object.
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

    # All TikTok amounts are pre-signed, so events are emitted as-is (no
    # `negative=`). These feed the display-only evento_* columns of
    # vw_conciliacao_margens; they are NOT summed into the margem.
    revenue = _agg("revenue_amount")
    gross = _agg("gross_sales_amount") or revenue or _agg("customer_payment_amount")
    fee_total = _agg("fee_amount")  # aggregate platform fees (negative)
    commission = _agg("platform_commission_amount")
    transaction_fee = _agg("transaction_fee_amount", "referral_fee_amount")
    shipping = _agg("shipping_cost_amount")  # net shipping borne by seller (neg)
    subsidy = _agg(
        "platform_shipping_fee_discount_amount", "shipping_cost_discount_amount"
    )  # shipping subsidy (positive)
    discount = _agg("platform_discount_amount", "seller_discount_amount")
    refund = _agg("customer_refund_amount")
    tax = _agg(
        "iva_vat_amount", "sales_tax_amount", "isr_income_tax_amount", "pit_amount"
    )

    events = _compact_events(
        [
            _event("sale", revenue, currency=currency),
            _event("commission_fee", commission, currency=currency),
            _event("transaction_fee", transaction_fee, currency=currency),
            _event("freight", shipping, currency=currency),
            _event("shipping_rebate", subsidy, currency=currency),
            _event("discount", discount, currency=currency),
            _event("refund", refund, currency=currency),
            _event("tax", tax, currency=currency),
            _event("net_payout", net, currency=currency),
        ]
    )

    # Settled => net amount present => final ("posted"). Order not yet settled
    # => pending => retried later until TikTok posts the statement. The margem
    # uses net_amount with priority (vw_conciliacao_margens); the structured
    # magnitudes below are positive, display-only, and chosen to reconcile as
    # gross_amount - fee_amount - freight_amount - refund_amount ≈ net_amount.
    # rebate / discount / tax are surfaced via EVENTS only: the shipping subsidy
    # is already baked into the net shipping_cost_amount and platform_discount is
    # platform-funded (it does not reduce the seller payout), so populating them
    # here would double-count in that fallback.
    has_settlement = net is not None
    return FinancialSnapshot(
        status="posted" if has_settlement else "pending",
        currency=currency,
        gross_amount=gross,
        fee_amount=abs(fee_total) if fee_total is not None else None,
        freight_amount=abs(shipping) if shipping is not None else None,
        refund_amount=abs(refund) if refund is not None else None,
        net_amount=net,
        raw={"settlements": body},
        events=events,
        error=None if has_settlement else "TikTok settlement not available yet",
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
