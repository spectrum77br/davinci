import asyncio
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission, user_scope
from app.models import (
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Product,
    ProductLink,
    Segment,
    User,
)
from app.schemas.products import (
    BlingImportIn,
    BlingImportSummary,
    BlingPreviewItem,
    BlingPreviewOut,
    BulkDeleteIn,
    BulkSegmentAssignIn,
    CsvImportSummary,
    ProductCreate,
    ProductLinkOut,
    ProductOut,
    ProductPage,
    ProductPatch,
)
from app.security.cipher import decrypt_json
from app.services.marketplaces.bling import (
    BLING_PRODUCTS_PAGE_SIZE,
    BlingClient,
    parse_bling_product,
)
from app.services.relink_hook import trigger_user_relink

logger = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["products"])


# ------------------------------------------------------------------ helpers

def _to_link_out(link: ProductLink) -> ProductLinkOut:
    return ProductLinkOut.model_validate(link)


async def _segment_lookup(
    session: AsyncSession, segment_ids: list[UUID]
) -> dict[UUID, tuple[str, str]]:
    """Returns {segment_id: (name, path)} for the given ids. Path joins
    ancestors as "Root / Child / Leaf" using cached segment rows."""
    if not segment_ids:
        return {}
    ids = list({sid for sid in segment_ids if sid is not None})
    if not ids:
        return {}
    # Load all segments — table is small (taxonomy) so a full read is fine and
    # avoids recursive CTEs when assembling paths.
    rows = (await session.execute(select(Segment))).scalars().all()
    by_id = {s.id: s for s in rows}
    out: dict[UUID, tuple[str, str]] = {}
    for sid in ids:
        s = by_id.get(sid)
        if s is None:
            continue
        chain: list[str] = []
        cur: Segment | None = s
        seen: set[UUID] = set()
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            chain.append(cur.name)
            cur = by_id.get(cur.parent_id) if cur.parent_id else None
        chain.reverse()
        out[sid] = (s.name, " / ".join(chain))
    return out


def _to_product_out(
    p: Product,
    links: list[ProductLink],
    segments: dict[UUID, tuple[str, str]] | None = None,
) -> ProductOut:
    out = ProductOut.model_validate(p)
    out.links = [_to_link_out(link) for link in links]
    if segments and p.segment_id and p.segment_id in segments:
        name, path = segments[p.segment_id]
        out.segment_name = name
        out.segment_path = path
    return out


async def _bling_client_for(
    session: AsyncSession, integration: Integration
) -> BlingClient:
    if integration.platform != IntegrationPlatform.BLING:
        raise HTTPException(
            400,
            detail={"code": "not_bling", "platform": integration.platform.value},
        )
    creds = decrypt_json(integration.credentials)

    async def on_refresh(new_creds: dict) -> None:
        from app.security.cipher import encrypt_json

        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    return BlingClient(creds, on_token_refresh=on_refresh, integration_id=integration.id)


# ------------------------------------------------------------------ products

@router.get("/products", response_model=ProductPage)
async def list_products(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "view"))],
    search: str | None = Query(None),
    integration_id: UUID | None = Query(None),
    low_stock: bool = Query(False),
    zero_stock: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> ProductPage:
    stmt = select(Product).where(user_scope(Product, user))
    count_stmt = select(func.count()).select_from(Product).where(user_scope(Product, user))
    if search:
        like = f"%{search}%"
        cond = or_(Product.sku.ilike(like), Product.name.ilike(like))
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if integration_id:
        # Filter products that have at least one ProductLink to this
        # integration. Previously this matched `Product.integration_id` —
        # but that column only stores the *source* (Bling) integration, so
        # selecting a marketplace account in the filter dropdown returned
        # an empty list. Subquery via EXISTS keeps the statement composable
        # with the other filters and avoids row duplication a JOIN would
        # introduce when a product has multiple links to the same account.
        link_match = select(ProductLink.id).where(
            and_(
                ProductLink.product_id == Product.id,
                ProductLink.integration_id == integration_id,
            )
        ).exists()
        stmt = stmt.where(link_match)
        count_stmt = count_stmt.where(link_match)
    if low_stock:
        cond = Product.stock < Product.min_stock
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if zero_stock:
        cond = Product.stock == 0
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(Product.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    # Batch-load links to avoid N+1.
    if rows:
        ids = [p.id for p in rows]
        links = (
            await session.execute(select(ProductLink).where(ProductLink.product_id.in_(ids)))
        ).scalars().all()
    else:
        links = []
    by_pid: dict[UUID, list[ProductLink]] = {}
    for link in links:
        by_pid.setdefault(link.product_id, []).append(link)

    seg_map = await _segment_lookup(session, [p.segment_id for p in rows if p.segment_id])
    items = [_to_product_out(p, by_pid.get(p.id, []), seg_map) for p in rows]
    return ProductPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(
    product_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "view"))],
) -> ProductOut:
    p = (
        await session.execute(
            select(Product).where(and_(Product.id == product_id, user_scope(Product, user)))
        )
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, detail={"code": "product_not_found"})
    links = (
        await session.execute(select(ProductLink).where(ProductLink.product_id == p.id))
    ).scalars().all()
    seg_map = await _segment_lookup(session, [p.segment_id]) if p.segment_id else {}
    return _to_product_out(p, list(links), seg_map)


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    body: ProductCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
) -> ProductOut:
    p = Product(
        user_id=user.id,
        sku=body.sku,
        name=body.name,
        category=body.category,
        cost_price=body.cost_price,
        price=body.price,
        stock=body.stock,
        min_stock=body.min_stock,
        integration_id=body.integration_id,
        image_url=body.image_url,
        observation=body.observation,
        observation2=body.observation2,
        observation3=body.observation3,
    )
    session.add(p)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        if "uq_products_user_id_sku" in str(e.orig):
            raise HTTPException(409, detail={"code": "sku_already_exists", "sku": body.sku}) from e
        raise
    await session.commit()
    await session.refresh(p)
    await trigger_user_relink(user.id)
    return _to_product_out(p, [])


@router.patch("/products/{product_id}", response_model=ProductOut)
async def patch_product(
    product_id: UUID,
    body: ProductPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
) -> ProductOut:
    p = (
        await session.execute(
            select(Product).where(and_(Product.id == product_id, user_scope(Product, user)))
        )
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, detail={"code": "product_not_found"})
    data = body.model_dump(exclude_unset=True)
    if "segment_id" in data and data["segment_id"] is not None:
        seg = (
            await session.execute(select(Segment).where(Segment.id == data["segment_id"]))
        ).scalar_one_or_none()
        if seg is None:
            raise HTTPException(400, detail={"code": "segment_not_found"})
    sku_changed = "sku" in data and data["sku"] != p.sku
    for k, v in data.items():
        setattr(p, k, v)
    await session.commit()
    await session.refresh(p)
    if sku_changed:
        await trigger_user_relink(user.id)
    links = (
        await session.execute(select(ProductLink).where(ProductLink.product_id == p.id))
    ).scalars().all()
    seg_map = await _segment_lookup(session, [p.segment_id]) if p.segment_id else {}
    return _to_product_out(p, list(links), seg_map)


def _audit_request_ctx(request: Request) -> dict:
    return {
        "client_ip": (request.client.host if request.client else None),
        "user_agent": request.headers.get("user-agent", "")[:200],
        "referer": request.headers.get("referer", "")[:200],
    }


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "delete"))],
) -> None:
    # Capture cascade impact before the DELETE so the audit log records
    # how many product_links / listings get pulled down with the product.
    cascade = (
        await session.execute(
            select(
                func.count(ProductLink.id).label("links"),
            ).where(ProductLink.product_id == product_id)
        )
    ).one()
    target = (
        await session.execute(
            select(Product.sku, Product.name, Product.bling_product_id).where(
                and_(Product.id == product_id, user_scope(Product, user))
            )
        )
    ).one_or_none()

    res = await session.execute(
        delete(Product).where(and_(Product.id == product_id, user_scope(Product, user)))
    )
    if res.rowcount == 0:
        raise HTTPException(404, detail={"code": "product_not_found"})
    await session.commit()
    logger.info(
        "product_deleted",
        actor_user_id=str(user.id),
        actor_email=user.email,
        product_id=str(product_id),
        sku=target.sku if target else None,
        name=target.name if target else None,
        bling_product_id=target.bling_product_id if target else None,
        cascade_product_links=cascade.links or 0,
        **_audit_request_ctx(request),
    )
    return None


@router.post("/products/bulk-delete")
async def bulk_delete_products(
    body: BulkDeleteIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "delete"))],
) -> dict:
    # Snapshot the would-be-deleted set for the audit trail.
    snapshot = (
        await session.execute(
            select(Product.id, Product.sku, Product.bling_product_id).where(
                and_(Product.id.in_(body.ids), user_scope(Product, user))
            )
        )
    ).all()
    cascade = (
        await session.execute(
            select(func.count(ProductLink.id)).where(
                ProductLink.product_id.in_(body.ids)
            )
        )
    ).scalar() or 0

    res = await session.execute(
        delete(Product).where(
            and_(Product.id.in_(body.ids), user_scope(Product, user))
        )
    )
    await session.commit()
    logger.info(
        "products_bulk_deleted",
        actor_user_id=str(user.id),
        actor_email=user.email,
        requested=len(body.ids),
        deleted=res.rowcount or 0,
        cascade_product_links=cascade,
        sample_skus=[r.sku for r in snapshot[:20]],
        sample_bling_ids=[r.bling_product_id for r in snapshot[:20] if r.bling_product_id],
        **_audit_request_ctx(request),
    )
    return {"deleted": res.rowcount or 0}


@router.post("/products/bulk-segment")
async def bulk_assign_segment(
    body: BulkSegmentAssignIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
) -> dict:
    if body.segment_id is not None:
        seg = (
            await session.execute(select(Segment).where(Segment.id == body.segment_id))
        ).scalar_one_or_none()
        if seg is None:
            raise HTTPException(400, detail={"code": "segment_not_found"})
    from sqlalchemy import update

    res = await session.execute(
        update(Product)
        .where(and_(Product.id.in_(body.product_ids), user_scope(Product, user)))
        .values(segment_id=body.segment_id)
    )
    await session.commit()
    return {"updated": res.rowcount or 0}


# --------------------------------------------------------------- product_links

@router.get("/product-links", response_model=list[ProductLinkOut])
async def list_product_links(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "view"))],
    integration_id: UUID | None = Query(None),
    product_id: UUID | None = Query(None),
) -> list[ProductLinkOut]:
    stmt = select(ProductLink).where(user_scope(ProductLink, user))
    if integration_id:
        stmt = stmt.where(ProductLink.integration_id == integration_id)
    if product_id:
        stmt = stmt.where(ProductLink.product_id == product_id)
    rows = (
        await session.execute(stmt.order_by(ProductLink.updated_at.desc()))
    ).scalars().all()
    return [_to_link_out(link) for link in rows]


@router.delete("/product-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product_link(
    link_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "delete"))],
) -> None:
    target = (
        await session.execute(
            select(
                ProductLink.product_id, ProductLink.platform,
                ProductLink.external_id, ProductLink.variation_id,
                ProductLink.integration_id, ProductLink.external_sku,
            ).where(and_(ProductLink.id == link_id, user_scope(ProductLink, user)))
        )
    ).one_or_none()
    res = await session.execute(
        delete(ProductLink).where(
            and_(ProductLink.id == link_id, user_scope(ProductLink, user))
        )
    )
    if res.rowcount == 0:
        raise HTTPException(404, detail={"code": "product_link_not_found"})
    await session.commit()
    logger.info(
        "product_link_deleted",
        actor_user_id=str(user.id),
        actor_email=user.email,
        link_id=str(link_id),
        product_id=str(target.product_id) if target else None,
        platform=target.platform.value if target else None,
        external_id=target.external_id if target else None,
        variation_id=target.variation_id if target else None,
        integration_id=str(target.integration_id) if target else None,
        external_sku=target.external_sku if target else None,
        **_audit_request_ctx(request),
    )
    return None


# ----------------------------------------------------------- Bling preview/import

async def _ensure_bling_link(
    session: AsyncSession,
    *,
    user_id: UUID,
    integration_id: UUID,
    product: Product,
    bling_product_id: int,
) -> None:
    """Make sure every product imported from Bling carries a `bling` platform
    link pointing at its bling produto.id — otherwise the SyncOrchestrator's
    `_refresh_bling` never runs for that product and the stock stays stale.
    No-op when the link already exists."""
    existing = (
        await session.execute(
            select(ProductLink.id).where(
                and_(
                    ProductLink.product_id == product.id,
                    ProductLink.platform == IntegrationPlatform.BLING,
                )
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(
        ProductLink(
            user_id=user_id,
            product_id=product.id,
            integration_id=integration_id,
            platform=IntegrationPlatform.BLING,
            external_id=str(bling_product_id),
            external_sku=product.sku,
            listing_title=product.name,
            stock=product.stock,
            last_sync_status=LinkSyncStatus.PENDING,
        )
    )


# Bling listing rate-limit guard: CF gates the API at ~3 req/s. 1.5s between
# pages stays comfortably under that and matches SSH's getAllProducts cadence.
_BLING_PREVIEW_PAGE_DELAY_SEC = 1.5
# Hard cap on pages to prevent runaway loops if the API stops returning empty.
_BLING_PREVIEW_MAX_PAGES = 200
# Stop after this many back-to-back page errors so a transient outage doesn't
# leave the request hanging forever — partial results still flow to the UI.
_BLING_PREVIEW_MAX_CONSECUTIVE_ERRORS = 3


@router.get("/products/preview/bling", response_model=BlingPreviewOut)
async def bling_preview(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
    integration_id: UUID = Query(...),
) -> BlingPreviewOut:
    """Fetch ALL Bling products (paginating internally) so the UI can show
    a single scrollable list with accurate total/imported/new counters.
    SSH parity: replaces the old page-by-page preview that forced the
    operator to click ← → to discover the rest of the catalog."""
    integ = (
        await session.execute(select(Integration).where(Integration.id == integration_id))
    ).scalar_one_or_none()
    if integ is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})
    client = await _bling_client_for(session, integ)

    all_raw: list[dict] = []
    page = 1
    consecutive_errors = 0
    while page <= _BLING_PREVIEW_MAX_PAGES:
        try:
            items_page = await client.list_products_page(
                pagina=page, limite=BLING_PRODUCTS_PAGE_SIZE
            )
            consecutive_errors = 0
        except Exception as e:  # noqa: BLE001
            consecutive_errors += 1
            logger.warning(
                "bling_preview_page_error",
                page=page,
                err=str(e)[:200],
                consecutive=consecutive_errors,
                total_so_far=len(all_raw),
            )
            if consecutive_errors >= _BLING_PREVIEW_MAX_CONSECUTIVE_ERRORS:
                logger.error(
                    "bling_preview_stopping",
                    page=page,
                    total_so_far=len(all_raw),
                )
                break
            await asyncio.sleep(10)  # cool off before retry
            continue
        if not items_page:
            break
        all_raw.extend(items_page)
        if len(items_page) < BLING_PRODUCTS_PAGE_SIZE:
            break
        page += 1
        await asyncio.sleep(_BLING_PREVIEW_PAGE_DELAY_SEC)

    parsed = [parse_bling_product(r) for r in all_raw]

    # Batch the already_imported lookup so the IN clause stays manageable
    # even with a 5k+ product catalog (Postgres caps params at ~32k but the
    # planner balks long before that on a single OR'd list).
    all_bling_ids = [p["bling_product_id"] for p in parsed if p.get("bling_product_id")]
    already: set[int] = set()
    BATCH = 500
    for i in range(0, len(all_bling_ids), BATCH):
        chunk = all_bling_ids[i : i + BATCH]
        rows = (
            await session.execute(
                select(Product.bling_product_id).where(
                    and_(
                        user_scope(Product, user),
                        Product.bling_product_id.in_(chunk),
                    )
                )
            )
        ).scalars().all()
        already.update(int(b) for b in rows if b is not None)

    items = [
        BlingPreviewItem(
            **p,
            already_imported=int(p["bling_product_id"]) in already
            if p.get("bling_product_id")
            else False,
        )
        for p in parsed
    ]
    logger.info(
        "bling_preview_done",
        pages_fetched=page,
        total_items=len(items),
        already_imported=len(already),
    )
    return BlingPreviewOut(integration_id=integ.id, page=1, items=items)


@router.post("/products/import/bling", response_model=BlingImportSummary)
async def bling_import(
    body: BlingImportIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
) -> BlingImportSummary:
    integ = (
        await session.execute(select(Integration).where(Integration.id == body.integration_id))
    ).scalar_one_or_none()
    if integ is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})
    client = await _bling_client_for(session, integ)

    imported = 0
    updated = 0
    skipped: list[int] = []

    # Existing products keyed by bling_product_id (to dedup updates).
    existing_rows = (
        await session.execute(
            select(Product).where(
                and_(
                    user_scope(Product, user),
                    Product.bling_product_id.in_(body.bling_product_ids),
                )
            )
        )
    ).scalars().all()
    existing_by_bid = {p.bling_product_id: p for p in existing_rows}

    for bid in body.bling_product_ids:
        try:
            raw = await client.get_product(int(bid))
        except Exception as e:  # noqa: BLE001
            logger.warning("bling_get_product_failed", bid=bid, err=str(e))
            skipped.append(int(bid))
            continue
        norm = parse_bling_product({"id": bid, **raw})
        if not norm["sku"]:
            skipped.append(int(bid))
            continue

        p = existing_by_bid.get(int(bid))
        if p is None:
            p = Product(
                user_id=user.id,
                sku=norm["sku"],
                name=norm["name"] or norm["sku"],
                category=norm["category"],
                cost_price=norm["cost_price"],
                bling_cost_price=norm["bling_cost_price"],
                price=norm["price"],
                stock=norm["stock"] or 0,
                min_stock=norm["min_stock"] or 0,
                bling_product_id=int(bid),
                integration_id=integ.id,
                image_url=norm["image_url"],
                observation=norm["observation"],
                last_imported_at=datetime.now(UTC),
            )
            session.add(p)
            try:
                await session.flush()
            except IntegrityError as e:
                await session.rollback()
                if "uq_products_user_id_sku" in str(e.orig):
                    # SKU already exists for a different bling_product_id — update that row.
                    existing = (
                        await session.execute(
                            select(Product).where(
                                and_(
                                    user_scope(Product, user),
                                    Product.sku == norm["sku"],
                                )
                            )
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        existing.bling_product_id = int(bid)
                        existing.name = norm["name"] or existing.name
                        existing.bling_cost_price = norm["bling_cost_price"]
                        existing.price = norm["price"]
                        if norm["stock"] is not None:
                            existing.stock = norm["stock"]
                        if norm["min_stock"] is not None:
                            existing.min_stock = norm["min_stock"]
                        if norm["category"]:
                            existing.category = norm["category"]
                        if norm["observation"]:
                            existing.observation = norm["observation"]
                        existing.image_url = norm["image_url"] or existing.image_url
                        existing.integration_id = integ.id
                        existing.last_imported_at = datetime.now(UTC)
                        await _ensure_bling_link(
                            session,
                            user_id=user.id,
                            integration_id=integ.id,
                            product=existing,
                            bling_product_id=int(bid),
                        )
                        updated += 1
                        continue
                raise
            await _ensure_bling_link(
                session,
                user_id=user.id,
                integration_id=integ.id,
                product=p,
                bling_product_id=int(bid),
            )
            imported += 1
        else:
            p.name = norm["name"] or p.name
            p.bling_cost_price = norm["bling_cost_price"]
            p.price = norm["price"]
            if norm["stock"] is not None:
                p.stock = norm["stock"]
            if norm["min_stock"] is not None:
                p.min_stock = norm["min_stock"]
            if norm["category"]:
                p.category = norm["category"]
            if norm["observation"]:
                p.observation = norm["observation"]
            p.image_url = norm["image_url"] or p.image_url
            p.integration_id = integ.id
            p.last_imported_at = datetime.now(UTC)
            await _ensure_bling_link(
                session,
                user_id=user.id,
                integration_id=integ.id,
                product=p,
                bling_product_id=int(bid),
            )
            updated += 1

    await session.commit()
    if imported > 0 or updated > 0:
        await trigger_user_relink(user.id)
    return BlingImportSummary(imported=imported, updated=updated, skipped_no_sku=skipped)


# ----------------------------------------------------------- CSV import

@router.post("/products/import/csv", response_model=CsvImportSummary)
async def csv_import(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
    file: UploadFile = File(...),
) -> CsvImportSummary:
    import csv
    import io
    from decimal import Decimal, InvalidOperation

    raw = await file.read()
    text = raw.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise HTTPException(400, detail={"code": "csv_empty"})

    # Expected columns (in order): SKU, Nome, Custo, Estoque, Estoque Mínimo.
    # First row is treated as header and skipped.
    imported = 0
    updated = 0
    errors: list[str] = []

    existing = (
        await session.execute(select(Product).where(user_scope(Product, user)))
    ).scalars().all()
    by_sku = {p.sku: p for p in existing}

    for idx, row in enumerate(rows[1:], start=2):
        if not row or all((c or "").strip() == "" for c in row):
            continue

        def _get(i: int) -> str:
            if i >= len(row):
                return ""
            return (row[i] or "").strip()

        sku = _get(0)
        name = _get(1)
        if not sku:
            errors.append(f"linha {idx}: SKU vazio")
            continue
        if not name:
            errors.append(f"linha {idx}: nome vazio (SKU {sku})")
            continue

        try:
            cost_raw = _get(2)
            cost = Decimal(cost_raw) if cost_raw else None
        except InvalidOperation:
            errors.append(f"linha {idx}: custo inválido (SKU {sku})")
            continue

        try:
            stock_raw = _get(3)
            stock = int(stock_raw) if stock_raw else 0
            min_raw = _get(4)
            min_stock = int(min_raw) if min_raw else 0
        except ValueError:
            errors.append(f"linha {idx}: estoque inválido (SKU {sku})")
            continue

        existing_p = by_sku.get(sku)
        if existing_p is None:
            p = Product(
                user_id=user.id,
                sku=sku,
                name=name,
                cost_price=cost,
                stock=stock,
                min_stock=min_stock,
            )
            session.add(p)
            by_sku[sku] = p
            imported += 1
        else:
            existing_p.name = name
            if cost is not None:
                existing_p.cost_price = cost
            existing_p.stock = stock
            existing_p.min_stock = min_stock
            updated += 1

    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(409, detail={"code": "csv_integrity_error"}) from e

    if imported > 0 or updated > 0:
        await trigger_user_relink(user.id)
    return CsvImportSummary(imported=imported, updated=updated, errors=errors)
