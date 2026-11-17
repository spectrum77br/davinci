"""Opt-in STO/PRI outbox; accepted imports need a status check and OF22 readback.

SUBMITTING is committed before HTTP. An interrupted/ambiguous POST never
replays automatically. The worker is the only place that submits offers.
Integration locks use PostgreSQL NO KEY UPDATE: serialize enqueues/setting
changes while allowing callers to hold FK KEY SHARE locks in an outer session.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import func, select, update

from app import db
from app.models import Integration, IntegrationPlatform, Listing, Product, ProductLink, SyncLog
from app.models.carrefour_offer_write import CarrefourOfferBatch, CarrefourOfferWrite
from app.models.enums import LinkSyncStatus, SyncLogAction
from app.security.cipher import decrypt_json
from app.services.marketplaces.base import SyncResult, SyncStatus
from app.services.marketplaces.carrefour import CarrefourClient, CarrefourError

STOCK_SCOPE = "_carrefour_stock_write_enabled"
PRICE_SCOPE = "_carrefour_price_write_enabled"
ACTIVE_STATES = ("preparing", "submitting", "submitted", "uncertain")
BATCH_SIZE = 50
INTERVAL = timedelta(minutes=5)
STALE_FENCE = timedelta(minutes=15)


def _now():
    return datetime.now(UTC)


def _price(value):
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number > Decimal("999999999999.99"):
            return None
        rounded = number.quantize(Decimal("0.01"))
        return rounded if rounded == number else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _offer_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    value = str(value).strip()
    if not value.isascii() or not value.isdecimal() or len(value) > 19:
        return None
    return str(int(value)) if 0 < int(value) <= 9223372036854775807 else None


def _enabled(integration, creds, *, qty=None, price=None):
    return bool(
        integration.platform == IntegrationPlatform.CARREFOUR
        and integration.archived_at is None
        and integration.status == "active"
        and (qty is None or (creds.get(STOCK_SCOPE) is True and not integration.vacation_mode))
        and (price is None or creds.get(PRICE_SCOPE) is True)
    )


def _review(code, detail, **payload):
    return SyncResult(
        status=SyncStatus.REQUIRES_REVIEW, error_code=code, error_detail=detail, payload=payload
    )


def import_outcome(data, *, expected_count=None):
    state = data.get("status")
    if state == "FAILED":
        return "rejected"
    if state != "COMPLETE":
        return "pending" if state in {"WAITING", "RUNNING", "QUEUED", "PROCESSING"} else "review"
    errors, pending = data.get("lines_in_error"), data.get("lines_in_pending")
    if type(errors) is not int or type(pending) is not int:
        return "review"
    if errors != 0 or pending != 0 or data.get("has_error_report") is True:
        return "rejected"
    if expected_count is not None:
        succeeded = data.get("lines_in_success")
        if type(succeeded) is not int or succeeded != expected_count:
            return "review"
    return "complete"


def target_matches(offer, item):
    if _offer_id(offer.get("offer_id")) != item["offer_id"] or offer.get("shop_sku") != item["sku"]:
        return False
    qty, price = item.get("stock"), item.get("price")
    if qty is not None and (type(offer.get("quantity")) is not int or offer["quantity"] != qty):
        return False
    return price is None or _price(offer.get("price")) == _price(price)


async def _source_valid(session, link):
    product = (
        await session.execute(
            select(Product.sku, Product.situacao).where(Product.id == link.product_id)
        )
    ).first()
    if product is None or product.situacao in {"E", "I"}:
        return False
    if link.external_sku and link.external_sku != product.sku:
        return False
    source = await session.scalar(
        select(ProductLink.id)
        .join(Integration, Integration.id == ProductLink.integration_id)
        .where(
            ProductLink.product_id == link.product_id,
            ProductLink.user_id == link.user_id,
            ProductLink.platform == IntegrationPlatform.BLING,
            ProductLink.morto_desde.is_(None),
            Integration.platform == IntegrationPlatform.BLING,
            Integration.archived_at.is_(None),
            Integration.status == "active",
        )
        .limit(1)
    )
    return source is not None


async def _refresh_stock_source(link_id):
    from app.services.carrefour_stock_source import refresh_carrefour_stock_target

    return await refresh_carrefour_stock_target(link_id)


async def enqueue_offer_write(*, integration_id, link, qty=None, price=None):
    if integration_id is None or getattr(link, "integration_id", None) != integration_id:
        return _review("carrefour_link_mismatch", "O vínculo não pertence a esta integração.")
    if qty is None and price is None:
        return _review("carrefour_invalid_update", "Informe estoque ou preço para enviar.")
    if qty is not None and (type(qty) is not int or not 0 <= qty <= 2147483647):
        return _review("carrefour_invalid_stock", "O estoque deve ser um inteiro não negativo.")
    amount = _price(price) if price is not None else None
    if price is not None and amount is None:
        return _review(
            "carrefour_invalid_price", "O preço deve ser positivo e ter até dois decimais."
        )
    async with db.SessionLocal() as session:
        integration = await session.scalar(
            select(Integration)
            .where(
                Integration.id == integration_id,
                Integration.platform == IntegrationPlatform.CARREFOUR,
            )
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if integration is None:
            return _review(
                "carrefour_integration_missing", "A integração Carrefour não foi encontrada."
            )
        creds = decrypt_json(integration.credentials)
        if not _enabled(integration, creds, qty=qty, price=amount):
            return _review("carrefour_write_not_enabled", "O envio está pausado nesta integração.")
        saved = await session.scalar(
            select(ProductLink).where(
                ProductLink.id == link.id,
                ProductLink.integration_id == integration.id,
                ProductLink.platform == IntegrationPlatform.CARREFOUR,
            )
        )
        if saved is None or saved.external_id != link.external_id:
            return _review("carrefour_link_mismatch", "O vínculo mudou. Recarregue o produto.")
        if not await _source_valid(session, saved):
            return _review(
                "carrefour_bling_source_required",
                "O produto precisa ter vínculo ativo e SKU confirmado no Bling.",
            )
        offer_id = _offer_id(saved.external_id)
        product_sku = await session.scalar(
            select(Product.sku).where(Product.id == saved.product_id)
        )
        sku = saved.external_sku or product_sku
        if not offer_id or not isinstance(sku, str) or not sku.strip():
            return _review("carrefour_link_invalid", "O anúncio precisa ter ID e SKU válidos.")
        if saved.external_sku and product_sku and saved.external_sku != product_sku:
            return _review("carrefour_sku_mismatch", "O SKU do vínculo difere do produto.")
        uncertain = await session.scalar(
            select(CarrefourOfferBatch.id)
            .where(
                CarrefourOfferBatch.integration_id == integration.id,
                CarrefourOfferBatch.state == "uncertain",
            )
            .limit(1)
        )
        if uncertain is not None:
            return _review(
                "carrefour_submission_unknown",
                "Uma carga anterior precisa de conferência no Carrefour.",
            )
        row = await session.scalar(
            select(CarrefourOfferWrite)
            .where(
                CarrefourOfferWrite.integration_id == integration.id,
                CarrefourOfferWrite.offer_id == offer_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            row = CarrefourOfferWrite(
                integration_id=integration.id,
                link_id=saved.id,
                offer_id=offer_id,
                sku=sku,
                revision=0,
                confirmed_revision=0,
                status="queued",
            )
            session.add(row)
        elif row.sku != sku or row.link_id != saved.id:
            return _review("carrefour_target_changed", "O vínculo da oferta mudou; revise a fila.")
        if row.status == "review":
            return _review(
                "carrefour_write_requires_review",
                "O envio anterior precisa de conferência; não será reenviado automaticamente.",
            )
        changed = (
            (qty is not None and row.requested_stock != qty)
            or (amount is not None and row.requested_price != amount)
            or row.status == "complete"
        )
        if qty is not None:
            row.requested_stock = qty
        if amount is not None:
            row.requested_price = amount
        if changed or row.revision == 0:
            row.revision += 1
        if row.status != "submitted":
            row.status = "queued"
        row.last_error = None
        await session.commit()
        return _review(
            "carrefour_write_pending",
            "Envio na fila do Carrefour; aguardando processamento e conferência.",
            request_id=str(row.id),
            revision=row.revision,
            pending=True,
        )


async def _claim(integration_id):
    async with db.SessionLocal() as session:
        integration = await session.scalar(
            select(Integration)
            .where(
                Integration.id == integration_id,
                Integration.platform == IntegrationPlatform.CARREFOUR,
            )
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if integration is None:
            return None, False
        active = await session.scalar(
            select(CarrefourOfferBatch).where(
                CarrefourOfferBatch.integration_id == integration.id,
                CarrefourOfferBatch.state.in_(ACTIVE_STATES),
            )
        )
        if active is not None:
            return active.id, False
        creds = decrypt_json(integration.credentials)
        if not _enabled(integration, creds) or not (
            creds.get(STOCK_SCOPE) is True or creds.get(PRICE_SCOPE) is True
        ):
            return None, False
        last = await session.scalar(
            select(func.coalesce(CarrefourOfferBatch.submitted_at, CarrefourOfferBatch.created_at))
            .where(
                CarrefourOfferBatch.integration_id == integration.id,
            )
            .order_by(CarrefourOfferBatch.created_at.desc())
            .limit(1)
        )
        if last is not None and _now() - last < INTERVAL:
            return None, False
        stock_on = creds.get(STOCK_SCOPE) is True and not integration.vacation_mode
        price_on = creds.get(PRICE_SCOPE) is True
        # One kind per import: partial CSV APIs never rewrite the other field.
        # Alternate when both are available so a stock stream cannot starve prices.
        previous_kind = await session.scalar(
            select(CarrefourOfferBatch.kind)
            .where(CarrefourOfferBatch.integration_id == integration.id)
            .order_by(CarrefourOfferBatch.created_at.desc())
            .limit(1)
        )
        preferred = ["price", "stock"] if previous_kind == "stock" else ["stock", "price"]
        kind = None
        for candidate in preferred:
            if not (stock_on if candidate == "stock" else price_on):
                continue
            field = (
                CarrefourOfferWrite.requested_stock
                if candidate == "stock"
                else CarrefourOfferWrite.requested_price
            )
            has_pending = await session.scalar(
                select(CarrefourOfferWrite.id)
                .where(
                    CarrefourOfferWrite.integration_id == integration.id,
                    CarrefourOfferWrite.status == "queued",
                    field.is_not(None),
                )
                .limit(1)
            )
            if has_pending is not None:
                kind = candidate
                break
        if kind is None:
            return None, False
        eligible = (
            CarrefourOfferWrite.requested_stock
            if kind == "stock"
            else CarrefourOfferWrite.requested_price
        ).is_not(None)
        rows = (
            await session.scalars(
                select(CarrefourOfferWrite)
                .where(
                    CarrefourOfferWrite.integration_id == integration.id,
                    CarrefourOfferWrite.status == "queued",
                    eligible,
                )
                .order_by(CarrefourOfferWrite.updated_at)
                .limit(BATCH_SIZE)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
        items = []
        for row in rows:
            stock = row.requested_stock if kind == "stock" else None
            price = row.requested_price if kind == "price" else None
            if stock is None and price is None:
                continue
            items.append(
                {
                    "request_id": str(row.id),
                    "revision": row.revision,
                    "link_id": str(row.link_id),
                    "offer_id": row.offer_id,
                    "sku": row.sku,
                    "stock": stock,
                    "price": str(price) if price is not None else None,
                }
            )
            row.status = "submitted"
        if not items:
            return None, False
        batch = CarrefourOfferBatch(
            integration_id=integration.id,
            shop_id=str(creds.get("shop_id")),
            kind=kind,
            state="preparing",
            items=items,
        )
        session.add(batch)
        await session.commit()
        return batch.id, True


async def _mark_rejected(session, batch, code, *, uncertain=False):
    batch.state = "uncertain" if uncertain else "rejected"
    batch.last_error = code
    for item in batch.items:
        row = await session.scalar(
            select(CarrefourOfferWrite)
            .where(CarrefourOfferWrite.id == UUID(item["request_id"]))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is not None:
            row.status, row.last_error = "review", code
        link = await session.scalar(
            select(ProductLink)
            .where(ProductLink.id == UUID(item["link_id"]))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if link is not None and link.integration_id == batch.integration_id:
            link.last_sync_status = LinkSyncStatus.REQUIRES_REVIEW
            link.last_error = "Envio ao Carrefour exige conferência: " + code
            session.add(
                SyncLog(
                    user_id=link.user_id,
                    product_id=link.product_id,
                    product_link_id=link.id,
                    integration_id=link.integration_id,
                    store_id=link.store_id,
                    platform=IntegrationPlatform.CARREFOUR,
                    action=(
                        SyncLogAction.UPDATE_STOCK
                        if batch.kind == "stock"
                        else SyncLogAction.UPDATE_PRICE
                    ),
                    status=LinkSyncStatus.REQUIRES_REVIEW,
                    error_code=code,
                    error_detail="Confira o processamento do envio no Carrefour antes de reenviar.",
                    payload={"batch_id": str(batch.id), "import_id": batch.import_id},
                )
            )


async def _reject(batch_id, code, *, uncertain=False):
    async with db.SessionLocal() as session:
        initial = await session.get(CarrefourOfferBatch, batch_id)
        if initial is None:
            return
        await session.scalar(
            select(Integration)
            .where(Integration.id == initial.integration_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        batch = await session.scalar(
            select(CarrefourOfferBatch)
            .where(CarrefourOfferBatch.id == batch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if batch is not None and batch.state not in {"complete", "rejected"}:
            await _mark_rejected(session, batch, code, uncertain=uncertain)
            await session.commit()


async def _finish_item(session, batch, item):
    row = await session.scalar(
        select(CarrefourOfferWrite)
        .where(CarrefourOfferWrite.id == UUID(item["request_id"]))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    link = await session.scalar(
        select(ProductLink)
        .where(ProductLink.id == UUID(item["link_id"]))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or link is None or link.integration_id != batch.integration_id:
        return
    if _offer_id(link.external_id) != item["offer_id"] or (
        link.external_sku and link.external_sku != item["sku"]
    ):
        row.status, row.last_error = "review", "carrefour_target_changed"
        return
    before = link.stock
    if item.get("stock") is not None:
        link.stock = item.get("observed_stock", item["stock"])
    if item.get("price") is not None:
        link.price = Decimal(item["price"])
    link.last_sync_at, link.last_sync_status, link.last_error = _now(), LinkSyncStatus.OK, None
    link.morto_desde, link.morto_motivo = None, None
    values = {}
    if item.get("stock") is not None:
        values["stock"] = item.get("observed_stock", item["stock"])
    if item.get("price") is not None:
        values["price"] = int(Decimal(item["price"]) * 100)
    if values:
        await session.execute(
            update(Listing)
            .where(
                Listing.integration_id == batch.integration_id,
                Listing.external_id == item["offer_id"],
                Listing.sku == item["sku"],
            )
            .values(**values)
        )
    # A newer price-only intent must never resend stock already confirmed.
    # Conversely, a changed stock intent must survive this older confirmation.
    if item.get("stock") is not None and row.requested_stock == item["stock"]:
        row.requested_stock = None
    if item.get("price") is not None and row.requested_price == Decimal(item["price"]):
        row.requested_price = None
    if row.requested_stock is None and row.requested_price is None:
        row.confirmed_revision = max(row.confirmed_revision, row.revision)
        row.status = "complete"
    else:
        row.status = "queued"
        link.last_sync_status = LinkSyncStatus.REQUIRES_REVIEW
    row.last_error = None
    for kind in ("stock", "price"):
        if item.get(kind) is None:
            continue
        session.add(
            SyncLog(
                user_id=link.user_id,
                product_id=link.product_id,
                product_link_id=link.id,
                integration_id=link.integration_id,
                store_id=link.store_id,
                platform=IntegrationPlatform.CARREFOUR,
                action=SyncLogAction.UPDATE_STOCK
                if kind == "stock"
                else SyncLogAction.UPDATE_PRICE,
                status=LinkSyncStatus.OK,
                qty_before=before if kind == "stock" else None,
                qty_after=item.get("observed_stock", item["stock"]) if kind == "stock" else None,
                error_code="carrefour_write_confirmed",
                payload={
                    "batch_id": str(batch.id),
                    "import_id": batch.import_id,
                    "request_id": item["request_id"],
                    "revision": item["revision"],
                    "price": item.get("price"),
                    "confirmed_by": "OF22",
                },
            )
        )


async def _targets_current(session, batch, *, check_fresh=False):
    for item in batch.items:
        row = await session.scalar(
            select(CarrefourOfferWrite)
            .where(CarrefourOfferWrite.id == UUID(item["request_id"]))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        link = await session.scalar(
            select(ProductLink)
            .where(ProductLink.id == UUID(item["link_id"]))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or link is None:
            return "invalid"
        if row.revision != item["revision"]:
            return "superseded"
        if not await _source_valid(session, link):
            return "invalid"
        if check_fresh and batch.kind == "stock":
            stamp = item.get("source_checked_at")
            if stamp is None or _now() - datetime.fromisoformat(stamp) > INTERVAL:
                return "superseded"
        sku = link.external_sku or await session.scalar(
            select(Product.sku).where(Product.id == link.product_id)
        )
        if (
            link.integration_id != batch.integration_id
            or _offer_id(link.external_id) != item["offer_id"]
            or sku != item["sku"]
        ):
            return "invalid"
    return "current"


async def _superseded(session, batch):
    batch.state, batch.last_error = "rejected", "carrefour_request_superseded"
    for item in batch.items:
        row = await session.get(CarrefourOfferWrite, UUID(item["request_id"]))
        if row is not None:
            row.status, row.last_error = "queued", None


async def _refresh_batch_stock(batch_id, integration_id, items):
    refreshed = []
    for item in items:
        qty = await _refresh_stock_source(UUID(item["link_id"]))
        if type(qty) is not int or not 0 <= qty <= 2147483647:
            await _reject(batch_id, "carrefour_bling_refresh_failed")
            return None
        refreshed.append({**item, "stock": qty, "source_checked_at": _now().isoformat()})
    async with db.SessionLocal() as session:
        await session.scalar(
            select(Integration)
            .where(Integration.id == integration_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        batch = await session.scalar(
            select(CarrefourOfferBatch)
            .where(CarrefourOfferBatch.id == batch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if batch is None or batch.state != "preparing":
            return None
        target_state = await _targets_current(session, batch)
        if target_state != "current":
            if target_state == "superseded":
                await _superseded(session, batch)
            else:
                await _mark_rejected(session, batch, "carrefour_target_or_source_changed")
            await session.commit()
            return None
        for item in refreshed:
            row = await session.get(CarrefourOfferWrite, UUID(item["request_id"]))
            if row.requested_stock != item["stock"]:
                row.requested_stock = item["stock"]
                row.revision += 1
            item["revision"] = row.revision
        batch.items = refreshed
        await session.commit()
    return refreshed


async def _prepare(batch_id):
    from app.services.marketplaces.carrefour import build_price_update, build_stock_update

    async with db.SessionLocal() as session:
        batch = await session.get(CarrefourOfferBatch, batch_id)
        if batch is None or batch.state != "preparing":
            return
        integration = await session.get(Integration, batch.integration_id)
        if integration is None:
            return
        client = CarrefourClient(
            decrypt_json(integration.credentials), integration_id=integration.id
        )
        original_items = list(batch.items)
    if batch.kind == "stock":
        original_items = await _refresh_batch_stock(batch_id, integration.id, original_items)
        if original_items is None:
            return
    checked = await client.test_connection()
    if not checked.ok:
        await _reject(batch_id, "carrefour_account_check_failed")
        return
    prepared, noops = [], []
    try:
        for item in original_items:
            offer = await client.get_offer(item["offer_id"])
            if (
                _offer_id(offer.get("offer_id")) != item["offer_id"]
                or offer.get("shop_sku") != item["sku"]
            ):
                raise CarrefourError("offer_mismatch", "A oferta consultada difere do vínculo.")
            if batch.kind == "price":
                payload = build_price_update(offer, price=item["price"], expected_sku=item["sku"])
            else:
                payload = build_stock_update(offer, qty=item["stock"], expected_sku=item["sku"])
            if target_matches(offer, item):
                noops.append(item)
            else:
                prepared.append({**item, "offer": payload})
    except CarrefourError as exc:
        await _reject(batch_id, "carrefour_prepare_" + exc.code)
        return
    async with db.SessionLocal() as session:
        integration = await session.scalar(
            select(Integration)
            .where(Integration.id == batch.integration_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        current = await session.scalar(
            select(CarrefourOfferBatch)
            .where(CarrefourOfferBatch.id == batch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if current is None or current.state != "preparing":
            return
        creds = decrypt_json(integration.credentials)
        same_key = all(creds.get(k) == client.creds.get(k) for k in ("api_key", "shop_id"))
        if not same_key or not all(
            _enabled(integration, creds, qty=x.get("stock"), price=x.get("price"))
            for x in original_items
        ):
            await _mark_rejected(session, current, "carrefour_permission_or_credentials_changed")
            await session.commit()
            return
        target_state = await _targets_current(session, current)
        if target_state != "current":
            if target_state == "superseded":
                await _superseded(session, current)
            else:
                await _mark_rejected(session, current, "carrefour_target_or_source_changed")
            await session.commit()
            return
        for item in noops:
            await _finish_item(session, current, item)
        current.items = prepared
        current.state = "submitting" if prepared else "complete"
        if prepared:
            current.submitted_at = _now()
        await session.commit()
    if prepared:
        await _submit(batch_id, client.creds)


async def _submit(batch_id, prepared_creds):
    async with db.SessionLocal() as session:
        batch = await session.get(CarrefourOfferBatch, batch_id)
        if batch is None:
            return
        integration = await session.scalar(
            select(Integration)
            .where(Integration.id == batch.integration_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        batch = await session.scalar(
            select(CarrefourOfferBatch)
            .where(CarrefourOfferBatch.id == batch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if batch.state != "submitting":
            return
        creds = decrypt_json(integration.credentials)
        if not all(
            creds.get(k) == prepared_creds.get(k) for k in ("api_key", "shop_id")
        ) or not all(
            _enabled(integration, creds, qty=x.get("stock"), price=x.get("price"))
            for x in batch.items
        ):
            await _mark_rejected(session, batch, "carrefour_permission_or_credentials_changed")
            await session.commit()
            return
        target_state = await _targets_current(session, batch, check_fresh=True)
        if target_state != "current":
            if target_state == "superseded":
                await _superseded(session, batch)
            else:
                await _mark_rejected(session, batch, "carrefour_target_or_source_changed")
            await session.commit()
            return
        client = CarrefourClient(creds)
        try:
            submit = (
                client.submit_stock_updates
                if batch.kind == "stock"
                else client.submit_price_updates
            )
            import_id = await submit([item["offer"] for item in batch.items])
        except CarrefourError as exc:
            known = exc.http_status in {400, 401, 403, 404, 405, 409, 413, 422, 429}
            await _mark_rejected(
                session, batch, "carrefour_submit_" + exc.code, uncertain=not known
            )
        else:
            batch.import_id, batch.state = str(import_id), "submitted"
            batch.next_check_at = _now() + INTERVAL
        batch.submitted_at = _now()
        await session.commit()


async def _confirm(batch_id):
    async with db.SessionLocal() as session:
        batch = await session.scalar(
            select(CarrefourOfferBatch)
            .where(CarrefourOfferBatch.id == batch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            batch is None
            or batch.state != "submitted"
            or not batch.import_id
            or (batch.next_check_at is not None and batch.next_check_at > _now())
        ):
            return
        integration = await session.get(Integration, batch.integration_id)
        if integration is None:
            return
        batch.next_check_at = _now() + INTERVAL
        creds = decrypt_json(integration.credentials)
        if str(creds.get("shop_id")) != batch.shop_id:
            await _mark_rejected(
                session, batch, "carrefour_confirmation_shop_changed", uncertain=True
            )
            await session.commit()
            return
        client = CarrefourClient(creds)
        items, import_id, kind = list(batch.items), batch.import_id, batch.kind
        await session.commit()
    try:
        poll = client.get_stock_import if kind == "stock" else client.get_price_import
        outcome = import_outcome(await poll(import_id), expected_count=len(items))
        if outcome == "pending":
            return
        if outcome != "complete":
            await _reject(batch_id, "carrefour_import_" + outcome)
            return
        for item in items:
            offer = await client.get_offer(item["offer_id"])
            if kind == "stock":
                observed = offer.get("quantity")
                matches = (
                    _offer_id(offer.get("offer_id")) == item["offer_id"]
                    and offer.get("shop_sku") == item["sku"]
                    and type(observed) is int
                    and 0 <= observed <= item["stock"]
                )
                if matches:
                    item["observed_stock"] = observed
            else:
                matches = target_matches(offer, item)
            if not matches:
                await _reject(batch_id, "carrefour_readback_mismatch")
                return
    except CarrefourError as exc:
        if (
            exc.code.startswith(("invalid_", "unsupported_"))
            or exc.code.endswith("mismatch")
            or (
                exc.http_status is not None
                and 400 <= exc.http_status < 500
                and exc.http_status not in {408, 429}
            )
        ):
            await _reject(batch_id, "carrefour_confirmation_" + exc.code)
        return  # transient read failures can be checked again after the persisted interval
    async with db.SessionLocal() as session:
        initial = await session.get(CarrefourOfferBatch, batch_id)
        if initial is None:
            return
        await session.scalar(
            select(Integration)
            .where(Integration.id == initial.integration_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        batch = await session.scalar(
            select(CarrefourOfferBatch)
            .where(CarrefourOfferBatch.id == batch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if batch is None or batch.state != "submitted":
            return
        integration = await session.get(Integration, batch.integration_id)
        if str(decrypt_json(integration.credentials).get("shop_id")) != batch.shop_id:
            await _mark_rejected(
                session, batch, "carrefour_confirmation_shop_changed", uncertain=True
            )
            await session.commit()
            return
        for item in items:
            await _finish_item(session, batch, item)
        batch.state, batch.last_error = "complete", None
        await session.commit()
        return True


async def run_carrefour_offer_writes():
    async with db.SessionLocal() as session:
        ids = set(
            (
                await session.scalars(
                    select(CarrefourOfferWrite.integration_id)
                    .where(CarrefourOfferWrite.status == "queued")
                    .distinct()
                )
            ).all()
        )
        ids.update(
            (
                await session.scalars(
                    select(CarrefourOfferBatch.integration_id)
                    .where(CarrefourOfferBatch.state.in_(ACTIVE_STATES))
                    .distinct()
                )
            ).all()
        )
    counts = {"checked": 0, "errors": 0}
    for integration_id in sorted(ids, key=str):
        try:
            batch_id, claimed = await _claim(integration_id)
            if batch_id is None:
                continue
            async with db.SessionLocal() as session:
                batch = await session.get(CarrefourOfferBatch, batch_id)
                state, age = batch.state, _now() - batch.updated_at
            if state == "preparing" and claimed:
                await _prepare(batch_id)
            elif state == "preparing" and age > STALE_FENCE:
                await _reject(batch_id, "carrefour_interrupted_before_submission")
            elif state == "submitting" and age > STALE_FENCE:
                await _reject(batch_id, "carrefour_submission_unknown", uncertain=True)
            elif state == "submitted":
                if await _confirm(batch_id):
                    # A successful confirmation is read-only. Claim at most one
                    # next batch now; _claim enforces the persisted POST interval
                    # and the integration lock prevents a competing worker from
                    # dispatching the same next batch.
                    next_id, next_claimed = await _claim(integration_id)
                    if next_id is not None and next_claimed:
                        await _prepare(next_id)
            counts["checked"] += 1
        except Exception:  # never record raw transport/credential exceptions
            counts["errors"] += 1
    return counts
