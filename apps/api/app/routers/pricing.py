"""Pricing router (Fase 9a) — accounts/products/overrides CRUD.

Push/Telegram/Audit endpoints arrive in 9b-9d. This sub-phase ships only
the data plane.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission, user_scope
from app.models import (
    AuditDismissedSku,
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    BlingOrder,
    CellStatus,
    Integration,
    IntegrationPlatform,
    Listing,
    PricingAccount,
    PricingOverride,
    PricingPlatform,
    PricingProduct,
    Product,
    ProductLink,
    Segment,
    StoreInfo,
    User,
)
from app.schemas.pricing import (
    AccountSetDepartmentIn,
    AutoMatchResult,
    CompetitorPriceRow,
    PricingAccountCreate,
    PricingAccountOut,
    PricingAccountPatch,
    PricingCatalogListingItem,
    PricingGridCell,
    PricingGridOut,
    PricingOverrideCellStatus,
    PricingOverrideOut,
    PricingOverrideUpsert,
    PricingProductCreate,
    PricingProductImport,
    PricingProductImportResult,
    PricingProductOut,
    PricingProductPatch,
    PricingPushBatchIn,
    PricingPushItemOut,
    PricingPushOut,
    PricingPushReportIn,
    SkuAuditRow,
    StoreInfoCreate,
    StoreInfoOut,
    StoreInfoPatch,
)
from app.schemas.products import JobCreatedOut
from app.security.cipher import decrypt, encrypt
from app.services.pricing.audit import (
    match_pricing_to_product_keys,
    scan_missing_skus,
)
from app.services.pricing.calc import calculate
from app.services.pricing.competitor import search_competitors
from app.services.pricing.push import push_one
from app.services.telegram import TelegramClient, TelegramConfigError
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api/pricing", tags=["pricing"])


def _coerce_platform(v: str | None) -> PricingPlatform | None:
    if v is None:
        return None
    try:
        return PricingPlatform(v)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_platform"}) from e


def _coerce_cell_status(v: str | None) -> CellStatus | None:
    if v is None:
        return None
    try:
        return CellStatus(v)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_cell_status"}) from e


async def _resolve_root_segment_id(
    session: AsyncSession, dept_slug: str | None
) -> UUID | None:
    """Returns the root segment id whose slug matches the given department
    string. Returns None if the slug doesn't resolve to a root segment."""
    if not dept_slug:
        return None
    res = await session.execute(
        select(Segment.id).where(
            and_(Segment.parent_id.is_(None), Segment.slug == dept_slug)
        )
    )
    return res.scalar_one_or_none()


async def _resolve_leaf_segment_id(
    session: AsyncSession,
    dept_slug: str | None,
    product_type: int | None,
) -> UUID | None:
    """Returns the leaf segment id for (root slug, column-index 1..5)."""
    if not dept_slug or product_type is None:
        return None
    sort_order = max(0, min(4, int(product_type) - 1))
    root_subq = select(Segment.id).where(
        and_(Segment.parent_id.is_(None), Segment.slug == dept_slug)
    )
    res = await session.execute(
        select(Segment.id).where(
            and_(
                Segment.sort_order == sort_order,
                Segment.parent_id.in_(root_subq),
            )
        )
    )
    return res.scalar_one_or_none()


async def _segment_index(
    session: AsyncSession,
) -> tuple[dict[UUID, str], dict[UUID, tuple[str, int]], dict[str, UUID]]:
    """Returns three indexes used to compose pricing schema outputs and
    translate department-slug filter queries:

      * `roots_by_id`     {segment_id: root_slug}      — for accounts
      * `leaves_by_id`    {segment_id: (root_slug, product_type 1..5)} — products
      * `root_id_by_slug` {slug: root_segment_id}      — for filter queries
    """
    rows = (await session.execute(select(Segment))).scalars().all()
    by_id = {s.id: s for s in rows}
    roots_by_id: dict[UUID, str] = {}
    leaves_by_id: dict[UUID, tuple[str, int]] = {}
    root_id_by_slug: dict[str, UUID] = {}
    for s in rows:
        if s.parent_id is None:
            roots_by_id[s.id] = s.slug
            root_id_by_slug[s.slug] = s.id
    for s in rows:
        if s.parent_id is not None:
            parent = by_id.get(s.parent_id)
            if parent and parent.parent_id is None:
                leaves_by_id[s.id] = (parent.slug, int(s.sort_order) + 1)
    return roots_by_id, leaves_by_id, root_id_by_slug


def _account_out(
    row: PricingAccount, roots_by_id: dict[UUID, str]
) -> PricingAccountOut:
    out = PricingAccountOut.model_validate(row)
    out.has_password = bool(row.password_enc)
    out.department = roots_by_id.get(row.segment_id)
    return out


def _product_out(
    row: PricingProduct, leaves_by_id: dict[UUID, tuple[str, int]]
) -> PricingProductOut:
    out = PricingProductOut.model_validate(row)
    pair = leaves_by_id.get(row.segment_id)
    if pair:
        out.department, out.product_type = pair
    return out


# =============================================================================
# Accounts
# =============================================================================

@router.get("/accounts", response_model=list[PricingAccountOut])
async def list_accounts(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_contas", "view"))
    ],
    department: str | None = Query(None),
    platform: str | None = Query(None),
) -> list[PricingAccountOut]:
    roots_by_id, _, root_id_by_slug = await _segment_index(session)
    stmt = select(PricingAccount).where(user_scope(PricingAccount, user))
    if department:
        rid = root_id_by_slug.get(department)
        if rid is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        stmt = stmt.where(PricingAccount.segment_id == rid)
    if platform:
        stmt = stmt.where(PricingAccount.platform == _coerce_platform(platform))
    stmt = stmt.order_by(PricingAccount.sort_order, PricingAccount.name)
    rows = (await session.execute(stmt)).scalars().all()
    return [_account_out(r, roots_by_id) for r in rows]


@router.post(
    "/accounts",
    response_model=PricingAccountOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_account(
    body: PricingAccountCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_contas", "edit"))
    ],
) -> PricingAccountOut:
    data = body.model_dump(exclude={"password", "department"})
    data["platform"] = _coerce_platform(body.platform)
    # Resolve segment_id: prefer explicit, fall back to department slug, then
    # default to root "celular" for backward compatibility.
    if data.get("segment_id") is None:
        sid = await _resolve_root_segment_id(session, body.department or "celular")
        if sid is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        data["segment_id"] = sid
    row = PricingAccount(user_id=user.id, **data)
    if body.password:
        row.password_enc = encrypt(body.password)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    roots_by_id, _, _ = await _segment_index(session)
    return _account_out(row, roots_by_id)


@router.patch("/accounts/{account_id}", response_model=PricingAccountOut)
async def patch_account(
    account_id: UUID,
    body: PricingAccountPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_contas", "edit"))
    ],
) -> PricingAccountOut:
    row = (
        await session.execute(
            select(PricingAccount).where(
                and_(
                    PricingAccount.id == account_id,
                    user_scope(PricingAccount, user),
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "account_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "password" in data:
        pwd = data.pop("password")
        row.password_enc = encrypt(pwd) if pwd else None
    if "platform" in data and data["platform"] is not None:
        data["platform"] = _coerce_platform(data["platform"])
    # Translate legacy department-string into segment_id; explicit segment_id
    # wins if both are present.
    dept_str = data.pop("department", None)
    if "segment_id" not in data and dept_str:
        sid = await _resolve_root_segment_id(session, dept_str)
        if sid is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        data["segment_id"] = sid
    for k, v in data.items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    roots_by_id, _, _ = await _segment_index(session)
    return _account_out(row, roots_by_id)


@router.delete(
    "/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_account(
    account_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_contas", "delete"))
    ],
) -> None:
    res = await session.execute(
        delete(PricingAccount).where(
            and_(
                PricingAccount.id == account_id,
                user_scope(PricingAccount, user),
            )
        )
    )
    if res.rowcount == 0:
        raise HTTPException(404, detail={"code": "account_not_found"})
    await session.commit()
    return None


# =============================================================================
# Products
# =============================================================================

@router.get("/products", response_model=list[PricingProductOut])
async def list_products(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "view"))
    ],
    department: str | None = Query(None),
    in_catalog: bool | None = Query(None),
    is_active: bool | None = Query(None),
) -> list[PricingProductOut]:
    _, leaves_by_id, root_id_by_slug = await _segment_index(session)
    stmt = select(PricingProduct).where(user_scope(PricingProduct, user))
    if department:
        rid = root_id_by_slug.get(department)
        if rid is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        leaf_ids = [lid for lid, (rs, _pt) in leaves_by_id.items() if rs == department]
        stmt = stmt.where(PricingProduct.segment_id.in_(leaf_ids))
    if in_catalog is not None:
        stmt = stmt.where(PricingProduct.in_catalog == in_catalog)
    if is_active is not None:
        stmt = stmt.where(PricingProduct.is_active == is_active)
    stmt = stmt.order_by(PricingProduct.sku)
    rows = (await session.execute(stmt)).scalars().all()
    return [_product_out(r, leaves_by_id) for r in rows]


@router.post(
    "/products",
    response_model=PricingProductOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_product(
    body: PricingProductCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> PricingProductOut:
    data = body.model_dump(exclude={"department", "product_type"})
    if data.get("segment_id") is None:
        sid = await _resolve_leaf_segment_id(
            session, body.department or "celular", body.product_type or 2
        )
        if sid is None:
            raise HTTPException(400, detail={"code": "invalid_segment"})
        data["segment_id"] = sid
    row = PricingProduct(user_id=user.id, **data)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "uq_pricing_products_user_sku" in str(e.orig):
            raise HTTPException(409, detail={"code": "sku_exists"}) from e
        raise
    await session.refresh(row)
    _, leaves_by_id, _ = await _segment_index(session)
    return _product_out(row, leaves_by_id)


@router.patch("/products/{product_id}", response_model=PricingProductOut)
async def patch_product(
    product_id: UUID,
    body: PricingProductPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> PricingProductOut:
    row = (
        await session.execute(
            select(PricingProduct).where(
                and_(
                    PricingProduct.id == product_id,
                    user_scope(PricingProduct, user),
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "product_not_found"})

    data = body.model_dump(exclude_unset=True)
    # Translate (department + product_type) → segment_id when the caller used
    # the legacy fields. Explicit segment_id wins if both are present.
    dept_str = data.pop("department", None)
    pt_val = data.pop("product_type", None)
    if "segment_id" not in data and (dept_str or pt_val is not None):
        # Need current values for whichever field was left out.
        _, leaves_by_id, _ = await _segment_index(session)
        cur = leaves_by_id.get(row.segment_id, ("celular", 2))
        sid = await _resolve_leaf_segment_id(
            session, dept_str or cur[0], pt_val if pt_val is not None else cur[1]
        )
        if sid is None:
            raise HTTPException(400, detail={"code": "invalid_segment"})
        data["segment_id"] = sid
    for k, v in data.items():
        setattr(row, k, v)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "uq_pricing_products_user_sku" in str(e.orig):
            raise HTTPException(409, detail={"code": "sku_exists"}) from e
        raise
    await session.refresh(row)
    _, leaves_by_id, _ = await _segment_index(session)
    return _product_out(row, leaves_by_id)


@router.delete(
    "/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_product(
    product_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "delete"))
    ],
) -> None:
    res = await session.execute(
        delete(PricingProduct).where(
            and_(
                PricingProduct.id == product_id,
                user_scope(PricingProduct, user),
            )
        )
    )
    if res.rowcount == 0:
        raise HTTPException(404, detail={"code": "product_not_found"})
    await session.commit()
    return None


@router.post(
    "/products/{product_id}/catalog",
    response_model=PricingProductOut,
)
async def toggle_catalog(
    product_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> PricingProductOut:
    row = (
        await session.execute(
            select(PricingProduct).where(
                and_(
                    PricingProduct.id == product_id,
                    user_scope(PricingProduct, user),
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "product_not_found"})
    row.in_catalog = not row.in_catalog
    await session.commit()
    await session.refresh(row)
    _, leaves_by_id, _ = await _segment_index(session)
    return _product_out(row, leaves_by_id)


@router.post("/products/import", response_model=PricingProductImportResult)
async def import_products(
    body: PricingProductImport,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> PricingProductImportResult:
    if not body.items:
        return PricingProductImportResult(created=0, updated=0, skipped=0)

    existing = (
        await session.execute(
            select(PricingProduct).where(user_scope(PricingProduct, user))
        )
    ).scalars().all()
    by_sku = {row.sku: row for row in existing}

    created = 0
    updated = 0
    skipped = 0
    for item in body.items:
        if not item.sku:
            skipped += 1
            continue
        data = item.model_dump(exclude={"department", "product_type"})
        if data.get("segment_id") is None:
            sid = await _resolve_leaf_segment_id(
                session,
                item.department or "celular",
                item.product_type if item.product_type is not None else 2,
            )
            if sid is None:
                skipped += 1
                continue
            data["segment_id"] = sid
        row = by_sku.get(item.sku)
        if row is None:
            session.add(PricingProduct(user_id=user.id, **data))
            created += 1
        else:
            for k, v in data.items():
                setattr(row, k, v)
            updated += 1

    await session.commit()
    return PricingProductImportResult(created=created, updated=updated, skipped=skipped)


# =============================================================================
# Overrides
# =============================================================================

@router.get("/overrides", response_model=list[PricingOverrideOut])
async def list_overrides(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "view"))
    ],
    department: str | None = Query(None),
) -> list[PricingOverrideOut]:
    stmt = select(PricingOverride).where(user_scope(PricingOverride, user))
    if department:
        _, leaves_by_id, root_id_by_slug = await _segment_index(session)
        if root_id_by_slug.get(department) is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        leaf_ids = [lid for lid, (rs, _pt) in leaves_by_id.items() if rs == department]
        stmt = stmt.join(
            PricingProduct, PricingProduct.id == PricingOverride.pricing_product_id
        ).where(PricingProduct.segment_id.in_(leaf_ids))
    rows = (await session.execute(stmt)).scalars().all()
    return [PricingOverrideOut.model_validate(r) for r in rows]


@router.put("/overrides", response_model=PricingOverrideOut)
async def upsert_override(
    body: PricingOverrideUpsert,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> PricingOverrideOut:
    cell_status = _coerce_cell_status(body.cell_status) or CellStatus.AUTO

    # Validate FK ownership
    prod = (
        await session.execute(
            select(PricingProduct).where(
                and_(
                    PricingProduct.id == body.pricing_product_id,
                    user_scope(PricingProduct, user),
                )
            )
        )
    ).scalar_one_or_none()
    if prod is None:
        raise HTTPException(404, detail={"code": "product_not_found"})
    acc = (
        await session.execute(
            select(PricingAccount).where(
                and_(
                    PricingAccount.id == body.pricing_account_id,
                    user_scope(PricingAccount, user),
                )
            )
        )
    ).scalar_one_or_none()
    if acc is None:
        raise HTTPException(404, detail={"code": "account_not_found"})

    row = (
        await session.execute(
            select(PricingOverride).where(
                and_(
                    PricingOverride.pricing_product_id == body.pricing_product_id,
                    PricingOverride.pricing_account_id == body.pricing_account_id,
                    user_scope(PricingOverride, user),
                )
            )
        )
    ).scalar_one_or_none()

    if row is None:
        row = PricingOverride(
            user_id=user.id,
            pricing_product_id=body.pricing_product_id,
            pricing_account_id=body.pricing_account_id,
            price_override=body.price_override,
            cell_status=cell_status,
        )
        session.add(row)
    else:
        row.price_override = body.price_override
        row.cell_status = cell_status

    await session.commit()
    await session.refresh(row)
    return PricingOverrideOut.model_validate(row)


@router.put("/overrides/cell-status", response_model=PricingOverrideOut)
async def set_cell_status(
    body: PricingOverrideCellStatus,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> PricingOverrideOut:
    cell_status = _coerce_cell_status(body.cell_status) or CellStatus.AUTO

    # Auto-create row when toggling cell status on a cell that has no override yet.
    row = (
        await session.execute(
            select(PricingOverride).where(
                and_(
                    PricingOverride.pricing_product_id == body.pricing_product_id,
                    PricingOverride.pricing_account_id == body.pricing_account_id,
                    user_scope(PricingOverride, user),
                )
            )
        )
    ).scalar_one_or_none()

    if row is None:
        prod_owned = (
            await session.execute(
                select(PricingProduct.id).where(
                    and_(
                        PricingProduct.id == body.pricing_product_id,
                        user_scope(PricingProduct, user),
                    )
                )
            )
        ).scalar_one_or_none()
        acc_owned = (
            await session.execute(
                select(PricingAccount.id).where(
                    and_(
                        PricingAccount.id == body.pricing_account_id,
                        user_scope(PricingAccount, user),
                    )
                )
            )
        ).scalar_one_or_none()
        if prod_owned is None or acc_owned is None:
            raise HTTPException(404, detail={"code": "override_target_not_found"})
        row = PricingOverride(
            user_id=user.id,
            pricing_product_id=body.pricing_product_id,
            pricing_account_id=body.pricing_account_id,
            cell_status=cell_status,
        )
        session.add(row)
    else:
        row.cell_status = cell_status

    await session.commit()
    await session.refresh(row)
    return PricingOverrideOut.model_validate(row)


@router.delete("/overrides", status_code=status.HTTP_204_NO_CONTENT)
async def remove_override(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("tabela_precos", "delete"))],
    pricing_product_id: Annotated[UUID, Query(...)],
    pricing_account_id: Annotated[UUID, Query(...)],
) -> None:
    res = await session.execute(
        delete(PricingOverride).where(
            and_(
                PricingOverride.pricing_product_id == pricing_product_id,
                PricingOverride.pricing_account_id == pricing_account_id,
                user_scope(PricingOverride, user),
            )
        )
    )
    if res.rowcount == 0:
        raise HTTPException(404, detail={"code": "override_not_found"})
    await session.commit()
    return None


# =============================================================================
# Push (9b) — single + batch, idempotency via header
# =============================================================================

@router.post("/push", response_model=PricingPushOut)
async def push_prices(
    body: PricingPushBatchIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PricingPushOut:
    if not body.items:
        return PricingPushOut(results=[])

    results: list[PricingPushItemOut] = []
    for i, item in enumerate(body.items):
        # Per-item key when batch has >1 entry — keeps each (acct,prod) replay-safe.
        key = (
            f"{idempotency_key}:{i}" if idempotency_key and len(body.items) > 1
            else idempotency_key
        )
        outcome = await push_one(
            session,
            user=user,
            account_id=item.pricing_account_id,
            product_id=item.pricing_product_id,
            idempotency_key=key,
        )
        results.append(
            PricingPushItemOut(
                pricing_account_id=item.pricing_account_id,
                pricing_product_id=item.pricing_product_id,
                ok=outcome.ok,
                code=outcome.code,
                detail=outcome.detail,
                price=outcome.price,
                item_id=outcome.item_id,
                variation_id=outcome.variation_id,
                cached=outcome.cached,
            )
        )
    await session.commit()
    return PricingPushOut(results=results)


# =============================================================================
# Grid (9b) — matrix produtos × contas com preço calculado
# =============================================================================

@router.get("/grid", response_model=PricingGridOut)
async def get_grid(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "view"))
    ],
    department: str | None = Query(None),
) -> PricingGridOut:
    _, leaves_by_id, root_id_by_slug = await _segment_index(session)
    accounts_stmt = select(PricingAccount).where(user_scope(PricingAccount, user))
    products_stmt = select(PricingProduct).where(user_scope(PricingProduct, user))
    if department:
        rid = root_id_by_slug.get(department)
        if rid is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        leaf_ids = [lid for lid, (rs, _pt) in leaves_by_id.items() if rs == department]
        accounts_stmt = accounts_stmt.where(PricingAccount.segment_id == rid)
        products_stmt = products_stmt.where(PricingProduct.segment_id.in_(leaf_ids))
    accounts = (
        await session.execute(
            accounts_stmt.order_by(PricingAccount.sort_order, PricingAccount.name)
        )
    ).scalars().all()
    products = (
        await session.execute(products_stmt.order_by(PricingProduct.sku))
    ).scalars().all()

    overrides = (
        await session.execute(
            select(PricingOverride).where(user_scope(PricingOverride, user))
        )
    ).scalars().all()
    by_pair = {(o.pricing_product_id, o.pricing_account_id): o for o in overrides}

    # cell_status reflects only what's stored in pricing_overrides. NA/SV are
    # user-set flags persisted after a failed push (SSH semantics); the grid
    # never infers them. Cells without a calculable price render as "—" in
    # the UI via the cellLabel fallback, not as NA.
    cells: list[PricingGridCell] = []
    for prod in products:
        pair = leaves_by_id.get(prod.segment_id)
        prod_type = pair[1] if pair else None
        for acc in accounts:
            ovr = by_pair.get((prod.id, acc.id))
            outcome = calculate(acc, prod, ovr, prod_type)
            cells.append(
                PricingGridCell(
                    pricing_account_id=acc.id,
                    pricing_product_id=prod.id,
                    price=outcome.price,
                    source=outcome.source,
                    cell_status=(ovr.cell_status.value if ovr else "auto"),
                    has_override=ovr is not None,
                )
            )

    roots_by_id, _, _ = await _segment_index(session)
    return PricingGridOut(
        accounts=[_account_out(a, roots_by_id) for a in accounts],
        products=[_product_out(p, leaves_by_id) for p in products],
        cells=cells,
    )


# =============================================================================
# Catalog (9c) — push isolado (B14) + listings filtradas
# =============================================================================

@router.get("/catalog-listings", response_model=list[PricingCatalogListingItem])
async def list_catalog_listings(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "view"))
    ],
    integration_id: Annotated[UUID | None, Query()] = None,
) -> list[PricingCatalogListingItem]:
    """Lista listings ML elegíveis para catálogo. Cruza com pricing_products
    via SKU para incluir só os que estão marcados como `in_catalog=True`."""
    in_catalog_skus = (
        await session.execute(
            select(PricingProduct.sku).where(
                and_(
                    user_scope(PricingProduct, user),
                    PricingProduct.in_catalog.is_(True),
                )
            )
        )
    ).scalars().all()
    if not in_catalog_skus:
        return []

    stmt = select(Listing).where(
        and_(
            user_scope(Listing, user),
            Listing.platform == IntegrationPlatform.ML,
            Listing.sku.in_(in_catalog_skus),
        )
    )
    if integration_id is not None:
        stmt = stmt.where(Listing.integration_id == integration_id)

    rows = (await session.execute(stmt.order_by(Listing.title))).scalars().all()
    return [
        PricingCatalogListingItem(
            id=r.id,
            integration_id=r.integration_id,
            external_id=r.external_id,
            sku=r.sku,
            title=r.title,
            price=r.price,
            status=r.status.value if hasattr(r.status, "value") else r.status,
            in_catalog=True,
        )
        for r in rows
    ]


@router.post("/push-catalog", response_model=PricingPushOut)
async def push_catalog_prices(
    body: PricingPushBatchIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PricingPushOut:
    """B14 — push exclusivo do canal catálogo. Refuse silently any item whose
    product não está em catálogo OU cuja conta não tem `department=catalogo`."""
    if not body.items:
        return PricingPushOut(results=[])

    catalog_roots_by_id, _, _ = await _segment_index(session)

    # Validate every item belongs to a catalog account + product first.
    prod_ids = {it.pricing_product_id for it in body.items}
    acc_ids = {it.pricing_account_id for it in body.items}
    products_by_id = {
        p.id: p
        for p in (
            await session.execute(
                select(PricingProduct).where(
                    and_(
                        user_scope(PricingProduct, user),
                        PricingProduct.id.in_(prod_ids),
                    )
                )
            )
        ).scalars().all()
    }
    accounts_by_id = {
        a.id: a
        for a in (
            await session.execute(
                select(PricingAccount).where(
                    and_(
                        user_scope(PricingAccount, user),
                        PricingAccount.id.in_(acc_ids),
                    )
                )
            )
        ).scalars().all()
    }

    results: list[PricingPushItemOut] = []
    for i, item in enumerate(body.items):
        prod = products_by_id.get(item.pricing_product_id)
        acc = accounts_by_id.get(item.pricing_account_id)
        if prod is None:
            results.append(
                PricingPushItemOut(
                    pricing_account_id=item.pricing_account_id,
                    pricing_product_id=item.pricing_product_id,
                    ok=False,
                    code="product_not_found",
                )
            )
            continue
        if acc is None:
            results.append(
                PricingPushItemOut(
                    pricing_account_id=item.pricing_account_id,
                    pricing_product_id=item.pricing_product_id,
                    ok=False,
                    code="account_not_found",
                )
            )
            continue
        if not prod.in_catalog:
            results.append(
                PricingPushItemOut(
                    pricing_account_id=item.pricing_account_id,
                    pricing_product_id=item.pricing_product_id,
                    ok=False,
                    code="not_in_catalog",
                    detail="produto sem flag in_catalog; use POST /api/pricing/push",
                )
            )
            continue
        # Roots map loaded once at top of function (see _segment_index call).
        dept_val = catalog_roots_by_id.get(acc.segment_id, "")
        if dept_val != "catalogo":
            results.append(
                PricingPushItemOut(
                    pricing_account_id=item.pricing_account_id,
                    pricing_product_id=item.pricing_product_id,
                    ok=False,
                    code="account_not_catalog",
                    detail="conta com department != catalogo; use POST /api/pricing/push",
                )
            )
            continue

        key = (
            f"{idempotency_key}:cat:{i}"
            if idempotency_key and len(body.items) > 1
            else (f"{idempotency_key}:cat" if idempotency_key else None)
        )
        outcome = await push_one(
            session,
            user=user,
            account_id=item.pricing_account_id,
            product_id=item.pricing_product_id,
            idempotency_key=key,
        )
        results.append(
            PricingPushItemOut(
                pricing_account_id=item.pricing_account_id,
                pricing_product_id=item.pricing_product_id,
                ok=outcome.ok,
                code=outcome.code,
                detail=outcome.detail,
                price=outcome.price,
                item_id=outcome.item_id,
                variation_id=outcome.variation_id,
                cached=outcome.cached,
            )
        )
    await session.commit()
    return PricingPushOut(results=results)


# =============================================================================
# Bulk push job (9c) — Arq
# =============================================================================

@router.post(
    "/push-batch",
    response_model=JobCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def enqueue_push_batch(
    body: PricingPushBatchIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    notify_telegram: bool = Query(True),
) -> JobCreatedOut:
    if not body.items:
        raise HTTPException(400, detail={"code": "empty_batch"})

    items = [
        {
            "pricing_account_id": str(it.pricing_account_id),
            "pricing_product_id": str(it.pricing_product_id),
        }
        for it in body.items
    ]
    job = BackgroundJob(
        type=BackgroundJobType.PUSH_PRICES_BATCH,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={
            "count": len(items),
            "idempotency_prefix": idempotency_key,
            "notify_telegram": notify_telegram,
        },
        total=len(items),
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_pool()
    arq = await pool.enqueue_job(
        "push_prices_batch_run",
        str(job.id),
        str(user.id),
        items,
        idempotency_key,
        notify_telegram,
    )
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    return JobCreatedOut(job_id=job.id)


# =============================================================================
# Manual Telegram report (9c)
# =============================================================================

@router.post("/push-report", status_code=status.HTTP_204_NO_CONTENT)
async def send_push_report(
    body: PricingPushReportIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> None:
    chat_id = body.chat_id
    if not chat_id:
        from app.models import UserSettings  # local import — avoids cycle

        st = (
            await session.execute(
                select(UserSettings).where(user_scope(UserSettings, user))
            )
        ).scalar_one_or_none()
        chat_id = st.telegram_chat_id if st else None
    try:
        cli = TelegramClient(default_chat_id=chat_id)
        await cli.send_message(body.summary)
    except TelegramConfigError as e:
        raise HTTPException(
            400,
            detail={"code": "telegram_not_configured", "reason": str(e)},
        ) from e


# =============================================================================
# Stock + sales maps (used by the grid Bling/7d/30d columns)
# =============================================================================


@router.get("/stock-map")
async def get_stock_map(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("tabela_precos", "view"))],
    department: Annotated[str | None, Query()] = None,
) -> dict[str, int]:
    """Returns {pricing_product_id: total_stock}.

    Algorithm (per spec):
      1. Split pricing_product.sku by comma → list of bases.
      2. For each base (celular): find products whose sku startswith
         base + "." AND does NOT contain "+". Pick the single "simple"
         representative — preferred order: shortest sku, then lex-min
         (so `x043.ra` is picked over `x043.kit2`). Take its stock.
         If no dotted variant exists, fall back to an exact `base` match.
      3. For mala / eletro / catalogo: pieces are already simple SKUs;
         use an exact case-insensitive lookup.
      4. SUM contributions across pieces. Missing pieces contribute 0.
    """
    _, leaves_by_id, root_id_by_slug = await _segment_index(session)
    stmt = select(PricingProduct).where(user_scope(PricingProduct, user))
    if department:
        if root_id_by_slug.get(department) is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        leaf_ids = [lid for lid, (rs, _pt) in leaves_by_id.items() if rs == department]
        stmt = stmt.where(PricingProduct.segment_id.in_(leaf_ids))
    pricing_rows = (await session.execute(stmt)).scalars().all()
    if not pricing_rows:
        logger.info(
            "pricing.stock_map.empty",
            user_id=str(user.id),
            department=department,
            reason="no_pricing_products",
        )
        return {}

    product_rows = (
        await session.execute(
            select(Product.sku, Product.stock).where(user_scope(Product, user))
        )
    ).all()

    # Two indexes:
    #   exact_stock[sku_lower] = stock (max on duplicate keys)
    #   variant_pick[base]     = (preferred_sku, stock) — best simple variant
    exact_stock: dict[str, int] = {}
    variant_pick: dict[str, tuple[str, int]] = {}
    for sku, stock in product_rows:
        if not sku:
            continue
        skl = sku.strip().lower()
        st = int(stock or 0)
        exact_stock[skl] = max(exact_stock.get(skl, 0), st)
        if "+" in skl or "." not in skl:
            continue
        base = skl.split(".", 1)[0]
        if not base:
            continue
        cur = variant_pick.get(base)
        if cur is None or (len(skl), skl) < (len(cur[0]), cur[0]):
            variant_pick[base] = (skl, st)

    from app.services.pricing.audit import _dept_value, _load_segment_roots

    segment_roots = await _load_segment_roots(session)

    out: dict[str, int] = {}
    matched_pids = 0
    matched_pieces_total = 0
    for pp in pricing_rows:
        dept_v = _dept_value(pp, segment_roots)
        total = 0
        any_match = False
        for piece in (pp.sku or "").split(","):
            key = piece.strip().lower()
            if not key:
                continue
            if dept_v == "celular":
                hit = variant_pick.get(key)
                if hit is not None:
                    total += hit[1]
                    any_match = True
                    matched_pieces_total += 1
                elif key in exact_stock:
                    total += exact_stock[key]
                    any_match = True
                    matched_pieces_total += 1
            else:
                if key in exact_stock:
                    total += exact_stock[key]
                    any_match = True
                    matched_pieces_total += 1
        out[str(pp.id)] = total
        if any_match:
            matched_pids += 1

    logger.info(
        "pricing.stock_map",
        user_id=str(user.id),
        department=department,
        pricing_products=len(pricing_rows),
        product_skus=len(exact_stock),
        celular_bases=len(variant_pick),
        matched_pricing_products=matched_pids,
        matched_pieces=matched_pieces_total,
    )
    return out


@router.get("/sales-map")
async def get_sales_map(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("tabela_precos", "view"))],
    department: Annotated[str | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=365)] = 7,
) -> dict[str, int]:
    """Returns {pricing_product_id: units_sold_in_last_N_days} from bling_orders.

    bling_orders is single-tenant (no user_scope). Cancelled / returned orders
    are excluded by `situacao`.
    Sales sum every matched product key — see celular base-SKU rule.
    """
    from datetime import UTC, datetime, timedelta

    _, leaves_by_id, root_id_by_slug = await _segment_index(session)
    pp_stmt = select(PricingProduct).where(user_scope(PricingProduct, user))
    if department:
        if root_id_by_slug.get(department) is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        leaf_ids = [lid for lid, (rs, _pt) in leaves_by_id.items() if rs == department]
        pp_stmt = pp_stmt.where(PricingProduct.segment_id.in_(leaf_ids))
    pricing_rows = (await session.execute(pp_stmt)).scalars().all()
    if not pricing_rows:
        logger.info(
            "pricing.sales_map.empty",
            user_id=str(user.id),
            department=department,
            days=days,
            reason="no_pricing_products",
        )
        return {}

    cutoff = datetime.now(UTC) - timedelta(days=days)
    excluded_situations = ("Cancelado", "Devolvido")
    rows = (
        await session.execute(
            select(BlingOrder.item_codigo, BlingOrder.item_quantidade).where(
                and_(
                    BlingOrder.item_codigo.isnot(None),
                    BlingOrder.item_codigo != "",
                    BlingOrder.data >= cutoff,
                    BlingOrder.situacao.notin_(excluded_situations),
                )
            )
        )
    ).all()

    sales_by_key: dict[str, int] = {}
    for codigo, qty in rows:
        key = (codigo or "").strip().lower()
        if not key:
            continue
        sales_by_key[key] = sales_by_key.get(key, 0) + int(qty or 0)

    from app.services.pricing.audit import _load_segment_roots

    segment_roots = await _load_segment_roots(session)
    matches = match_pricing_to_product_keys(
        pricing_rows, sales_by_key.keys(), segment_roots
    )

    out: dict[str, int] = {}
    matched_pids = 0
    for pp in pricing_rows:
        keys = matches.get(pp.id)
        if not keys:
            out[str(pp.id)] = 0
            continue
        out[str(pp.id)] = sum(sales_by_key.get(k, 0) for k in keys)
        matched_pids += 1

    logger.info(
        "pricing.sales_map",
        user_id=str(user.id),
        department=department,
        days=days,
        pricing_products=len(pricing_rows),
        order_rows=len(rows),
        sales_keys=len(sales_by_key),
        matched_pricing_products=matched_pids,
    )
    return out


# =============================================================================
# Actual marketplace prices — per-account current price for a pricing_product
# =============================================================================

@router.get("/actual-prices/{pricing_product_id}")
async def get_actual_prices(
    pricing_product_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "view"))
    ],
    department: Annotated[str | None, Query()] = None,
) -> dict[str, float | None]:
    """Fetch the *current* listing price from each connected marketplace for
    every pricing_account that could carry this product. Frontend uses the
    response to show "real: R$ XXX" alongside the computed price so the
    seller can spot drift between the table and what's live.

    Phase 1: ML only. Other platforms return null until their `get_price`
    helper lands.
    """
    from app.services.marketplaces.factory import client_for
    from app.services.pricing.sku_match import (
        build_prefix_index,
        dedup_links_for_push,
        filter_products_by_department,
        ml_listing_type_for_account,
        resolve_product_ids,
    )

    product = (
        await session.execute(
            select(PricingProduct).where(
                and_(
                    PricingProduct.id == pricing_product_id,
                    user_scope(PricingProduct, user),
                )
            )
        )
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(404, detail={"code": "product_not_found"})

    accounts_stmt = select(PricingAccount).where(user_scope(PricingAccount, user))
    if department:
        _, _, root_id_by_slug = await _segment_index(session)
        rid = root_id_by_slug.get(department)
        if rid is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        accounts_stmt = accounts_stmt.where(PricingAccount.segment_id == rid)
    accounts = (await session.execute(accounts_stmt)).scalars().all()

    # Resolve the same davinci.products that push would target so the price
    # we read corresponds to the listing push would write.
    products_rows = (
        await session.execute(
            select(Product.id, Product.sku).where(user_scope(Product, user))
        )
    ).all()
    prefix_index = build_prefix_index((pid, sku) for pid, sku in products_rows)
    candidate_ids = set(resolve_product_ids(product.sku, prefix_index))
    candidate_products = [
        (pid, sku) for pid, sku in products_rows if pid in candidate_ids
    ]

    integration_cache: dict[UUID, Integration | None] = {}
    client_cache: dict[UUID, object] = {}
    result: dict[str, float | None] = {}

    for acc in accounts:
        result[str(acc.id)] = None
        if acc.integration_id is None:
            continue

        if acc.integration_id not in integration_cache:
            integration_cache[acc.integration_id] = (
                await session.execute(
                    select(Integration).where(
                        and_(
                            Integration.id == acc.integration_id,
                            user_scope(Integration, user),
                        )
                    )
                )
            ).scalar_one_or_none()
        integ = integration_cache[acc.integration_id]
        if integ is None:
            continue
        # Only platforms with get_listing_price implemented carry real data;
        # the rest silently leave the cell at null.
        platform_value = integ.platform.value if hasattr(integ.platform, "value") else integ.platform
        if platform_value not in ("ml", "shopee"):
            continue

        # Apply the same SSH match push uses, with the real platform so the
        # celular ML kit-only filter only fires for ML.
        target_ids = filter_products_by_department(
            await _account_department_root(session, acc) or "celular",
            product.sku,
            candidate_products,
            platform=platform_value,
        )
        if not target_ids:
            continue

        links_q = select(ProductLink).where(
            and_(
                ProductLink.user_id == user.id,
                ProductLink.integration_id == acc.integration_id,
                ProductLink.product_id.in_(list(target_ids)),
            )
        )
        links = list((await session.execute(links_q)).scalars().all())
        if platform_value == "ml":
            wanted = ml_listing_type_for_account(acc.listing_type)
            if wanted:
                links = [lk for lk in links if (lk.listing_type or "") == wanted]
        links = dedup_links_for_push(platform_value, links)
        if not links:
            continue

        if acc.integration_id not in client_cache:
            from app.security.cipher import decrypt_json
            creds = decrypt_json(integ.credentials)
            client_cache[acc.integration_id] = client_for(
                integ.platform, creds
            )
        client = client_cache[acc.integration_id]

        # Try each candidate link until one returns a price — protects
        # against single-listing failures (closed/under_review) blanking
        # the whole cell.
        for link in links:
            try:
                price = await client.get_listing_price(link)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                price = None
            if price is not None:
                result[str(acc.id)] = float(price)
                break

    return result


async def _account_department_root(
    session: AsyncSession, acc: PricingAccount
) -> str | None:
    """Same as push._account_department but inlined here to avoid the
    cross-module dep."""
    if acc.segment_id is None:
        return None
    seg = await session.get(Segment, acc.segment_id)
    if seg is None:
        return None
    if seg.parent_id is None:
        return seg.slug
    parent = await session.get(Segment, seg.parent_id)
    return parent.slug if parent and parent.parent_id is None else None


# =============================================================================
# SKU Audit (9d) — scan + dismiss/undismiss
# =============================================================================

@router.get("/sku-audit", response_model=list[SkuAuditRow])
async def get_sku_audit(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "view"))
    ],
    include_dismissed: Annotated[bool, Query()] = False,
) -> list[SkuAuditRow]:
    rows = await scan_missing_skus(
        session, user_id=user.id, include_dismissed=include_dismissed
    )
    return [SkuAuditRow(**r) for r in rows]


@router.get("/sku-audit/dismissed", response_model=list[str])
async def list_dismissed_skus(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "view"))
    ],
) -> list[str]:
    rows = (
        await session.execute(
            select(AuditDismissedSku.sku).where(user_scope(AuditDismissedSku, user))
        )
    ).scalars().all()
    return list(rows)


@router.post("/sku-audit/{sku}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_sku(
    sku: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> None:
    existing = (
        await session.execute(
            select(AuditDismissedSku).where(
                and_(
                    user_scope(AuditDismissedSku, user),
                    AuditDismissedSku.sku == sku,
                )
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(AuditDismissedSku(user_id=user.id, sku=sku))
        await session.commit()
    return None


@router.post("/sku-audit/{sku}/undismiss", status_code=status.HTTP_204_NO_CONTENT)
async def undismiss_sku(
    sku: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> None:
    await session.execute(
        delete(AuditDismissedSku).where(
            and_(
                user_scope(AuditDismissedSku, user),
                AuditDismissedSku.sku == sku,
            )
        )
    )
    await session.commit()
    return None


# =============================================================================
# Competitor (9d) — ML public search com cache 5min
# =============================================================================

@router.get("/competitor-prices", response_model=list[CompetitorPriceRow])
async def competitor_prices(
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "view"))
    ],
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[CompetitorPriceRow]:
    del user  # auth-only dep
    entries = await search_competitors(q, limit=limit)
    return [
        CompetitorPriceRow(
            item_id=e.item_id,
            title=e.title,
            price=e.price,
            currency=e.currency,
            permalink=e.permalink,
            seller_id=e.seller_id,
            condition=e.condition,
            sold_quantity=e.sold_quantity,
            available_quantity=e.available_quantity,
            thumbnail=e.thumbnail,
        )
        for e in entries
    ]


# =============================================================================
# Bling cost sync (9d) — Arq job
# =============================================================================

@router.post(
    "/jobs/sync-bling-costs",
    response_model=JobCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def enqueue_sync_bling_costs(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> JobCreatedOut:
    job = BackgroundJob(
        type=BackgroundJobType.SYNC_BLING_COSTS,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={},
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_pool()
    arq = await pool.enqueue_job(
        "sync_bling_costs_run", str(job.id), str(user.id)
    )
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    return JobCreatedOut(job_id=job.id)


# =============================================================================
# Auto-match (9d) — link pricing_accounts to integrations by platform
# =============================================================================

PRICING_TO_INTEG_PLATFORM = {
    "mercadolivre": IntegrationPlatform.ML,
    "shopee": IntegrationPlatform.SHOPEE,
    "amazon": IntegrationPlatform.AMAZON,
    "tiktok": IntegrationPlatform.TIKTOK,
    "temu": IntegrationPlatform.TEMU,
}

# `store_info.platform` uses short codes (ml, …); `pricing_accounts.platform`
# uses the long form (mercadolivre, …). Used by `list_store_info` to wire
# Tipo / Tab. Preço badges across the two tables.
_STORE_TO_PRICING_PLATFORM = {
    "ml": "mercadolivre",
    "mercadolivre": "mercadolivre",
    "shopee": "shopee",
    "amazon": "amazon",
    "tiktok": "tiktok",
    "temu": "temu",
    "aliexpress": "aliexpress",
    "magalu": "magalu",
    "shein": "shein",
}


@router.post("/accounts/auto-match", response_model=AutoMatchResult)
async def auto_match_accounts(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_contas", "edit"))
    ],
) -> AutoMatchResult:
    """Tries to fill `pricing_accounts.integration_id` when null by matching:
      1. pricing_account.platform → integration.platform
      2. account.name (case-insensitive substring) ↔ integration.name
    First plain-platform match wins when a single integration exists.
    """
    accounts = (
        await session.execute(
            select(PricingAccount).where(
                and_(
                    user_scope(PricingAccount, user),
                    PricingAccount.integration_id.is_(None),
                )
            )
        )
    ).scalars().all()
    integrations = (
        await session.execute(
            select(Integration).where(user_scope(Integration, user))
        )
    ).scalars().all()

    by_platform: dict[IntegrationPlatform, list[Integration]] = {}
    for integ in integrations:
        by_platform.setdefault(integ.platform, []).append(integ)

    matched: list = []
    skipped = 0
    for acc in accounts:
        plat_val = acc.platform.value if hasattr(acc.platform, "value") else acc.platform
        target_plat = PRICING_TO_INTEG_PLATFORM.get(plat_val)
        if target_plat is None:
            skipped += 1
            continue
        candidates = by_platform.get(target_plat) or []
        if not candidates:
            skipped += 1
            continue
        chosen: Integration | None = None
        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            tokens = [
                t for t in (acc.name or "").lower().split() if len(t) >= 3
            ]
            for c in candidates:
                cname = (c.name or "").lower()
                if any(t in cname for t in tokens):
                    chosen = c
                    break
        if chosen is None:
            skipped += 1
            continue
        acc.integration_id = chosen.id
        matched.append(acc.id)

    await session.commit()
    return AutoMatchResult(matched=len(matched), skipped=skipped, accounts=matched)


@router.post("/accounts/{account_id}/department", response_model=PricingAccountOut)
async def set_account_department(
    account_id: UUID,
    body: AccountSetDepartmentIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_contas", "edit"))
    ],
) -> PricingAccountOut:
    sid = await _resolve_root_segment_id(session, body.department)
    if sid is None:
        raise HTTPException(400, detail={"code": "invalid_department"})
    row = (
        await session.execute(
            select(PricingAccount).where(
                and_(
                    PricingAccount.id == account_id,
                    user_scope(PricingAccount, user),
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "account_not_found"})
    row.segment_id = sid
    await session.commit()
    await session.refresh(row)
    roots_by_id, _, _ = await _segment_index(session)
    return _account_out(row, roots_by_id)


# =============================================================================
# Store info (9d) — CRUD com password cifrado
# =============================================================================

def _store_info_out(
    row: StoreInfo,
    departments: list[str] | None = None,
    has_pricing: bool | None = None,
    has_integration: bool | None = None,
) -> StoreInfoOut:
    out = StoreInfoOut.model_validate(row)
    out.has_password = bool(row.password_enc)
    out.departments = sorted(departments or [])
    # `has_pricing` is the strict-equality flag (an exact name+platform match
    # in pricing_accounts). Falls back to "any department was matched" so
    # callers that don't compute the strict variant still get a sensible value.
    out.has_pricing = bool(has_pricing) if has_pricing is not None else bool(out.departments)
    # `has_integration` is the strict (lower(name), platform) match against the
    # `integrations` table. Falls back to the legacy FK if the caller didn't
    # compute the strict variant.
    out.has_integration = (
        bool(has_integration) if has_integration is not None else (row.integration_id is not None)
    )
    return out


@router.get("/store-info", response_model=list[StoreInfoOut])
async def list_store_info(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "view"))
    ],
) -> list[StoreInfoOut]:
    rows = (
        await session.execute(
            select(StoreInfo)
            .where(user_scope(StoreInfo, user))
            .order_by(StoreInfo.sort_order, StoreInfo.platform)
        )
    ).scalars().all()
    # Wire badges to pricing_accounts by *(platform, account_name)* — that is
    # how SSH does it. `pricing_accounts.store_info_id` is empty in prod
    # (116/116 NULL), so a FK join returns nothing.
    #
    # Per-platform matching rules (mirrors SSH):
    #   * Tipo            (`departments`)  — same platform AND
    #                                         (exact OR prefix `<sname> `)
    #   * Tab. Preço      (`has_pricing`)  — same platform AND exact match
    #
    # store_info platforms use short codes (`ml`, …); pricing_accounts uses
    # the longer `mercadolivre`. Normalize both sides via PRICING_PLATFORM_ALIAS.
    roots_by_id, _, _ = await _segment_index(session)
    acc_rows = (
        await session.execute(
            select(PricingAccount.name, PricingAccount.platform, PricingAccount.segment_id)
            .where(user_scope(PricingAccount, user))
        )
    ).all()
    accs_normalized: list[tuple[str, str, str]] = []
    for name, plat, seg_id in acc_rows:
        slug = roots_by_id.get(seg_id)
        if not slug:
            continue
        plat_val = plat.value if hasattr(plat, "value") else str(plat)
        accs_normalized.append(((name or "").strip().lower(), plat_val.lower(), slug))

    # has_integration: strict (lower(name), platform) match against integrations.
    # Both store_info.platform and integrations.platform use short codes (ml,
    # shopee, …) so no alias translation is needed here.
    integ_rows = (
        await session.execute(select(Integration.name, Integration.platform))
    ).all()
    integ_keys: set[tuple[str, str]] = set()
    for iname, iplat in integ_rows:
        iplat_val = iplat.value if hasattr(iplat, "value") else str(iplat)
        if iname:
            integ_keys.add((iname.strip().lower(), iplat_val.lower()))

    out_list: list[StoreInfoOut] = []
    for r in rows:
        sname = (r.account_name or "").strip().lower()
        splat_alias = _STORE_TO_PRICING_PLATFORM.get(
            (r.platform or "").strip().lower(), (r.platform or "").strip().lower()
        )
        splat = (r.platform or "").strip().lower()
        depts: set[str] = set()
        if sname:
            for pname, pplat, pdept in accs_normalized:
                if pplat != splat_alias or not pname:
                    continue
                # SSH semantics: exact OR prefix `<sname> ` both count for
                # has_pricing. So "barbosa" matches "barbosa classico" /
                # "barbosa premium" — even when no exact "barbosa" pricing
                # account exists for that platform.
                if pname == sname or pname.startswith(sname + " "):
                    depts.add(pdept)
        has_integ = bool(sname and (sname, splat) in integ_keys)
        out_list.append(
            _store_info_out(r, list(depts), has_pricing=bool(depts), has_integration=has_integ)
        )
    return out_list


@router.post(
    "/store-info",
    response_model=StoreInfoOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_store_info(
    body: StoreInfoCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> StoreInfoOut:
    data = body.model_dump(exclude={"password"})
    row = StoreInfo(user_id=user.id, **data)
    if body.password:
        row.password_enc = encrypt(body.password)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _store_info_out(row)


@router.patch("/store-info/{store_info_id}", response_model=StoreInfoOut)
async def patch_store_info(
    store_info_id: UUID,
    body: StoreInfoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> StoreInfoOut:
    row = (
        await session.execute(
            select(StoreInfo).where(
                and_(
                    StoreInfo.id == store_info_id,
                    user_scope(StoreInfo, user),
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "store_info_not_found"})
    data = body.model_dump(exclude_unset=True)
    if "password" in data:
        pwd = data.pop("password")
        row.password_enc = encrypt(pwd) if pwd else None
    for k, v in data.items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return _store_info_out(row)


@router.get("/store-info/{store_info_id}/password")
async def reveal_store_info_password(
    store_info_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> dict[str, str]:
    row = (
        await session.execute(
            select(StoreInfo).where(
                and_(
                    StoreInfo.id == store_info_id,
                    user_scope(StoreInfo, user),
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "store_info_not_found"})
    if not row.password_enc:
        raise HTTPException(404, detail={"code": "no_password"})
    logger.info(
        "store_info.password_revealed",
        user_id=str(user.id),
        store_info_id=str(store_info_id),
    )
    return {"password": decrypt(row.password_enc)}


@router.post("/store-info/{store_info_id}/department", response_model=PricingAccountOut)
async def set_store_info_department(
    store_info_id: UUID,
    body: AccountSetDepartmentIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> PricingAccountOut:
    """Bind a department to a store_info — auto-creates the matching
    `pricing_accounts` row if absent (one per (store_info, root_segment))."""
    sid = await _resolve_root_segment_id(session, body.department)
    if sid is None:
        raise HTTPException(400, detail={"code": "invalid_department"})

    info = (
        await session.execute(
            select(StoreInfo).where(
                and_(
                    StoreInfo.id == store_info_id,
                    user_scope(StoreInfo, user),
                )
            )
        )
    ).scalar_one_or_none()
    if info is None:
        raise HTTPException(404, detail={"code": "store_info_not_found"})

    try:
        platform = PricingPlatform(info.platform)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "store_info_platform_unsupported"}) from e

    existing = (
        await session.execute(
            select(PricingAccount).where(
                and_(
                    user_scope(PricingAccount, user),
                    PricingAccount.store_info_id == store_info_id,
                    PricingAccount.segment_id == sid,
                )
            )
        )
    ).scalar_one_or_none()
    roots_by_id, _, _ = await _segment_index(session)
    if existing is not None:
        return _account_out(existing, roots_by_id)

    dept_slug = roots_by_id.get(sid, body.department)
    name = info.account_name or f"{info.platform} — {dept_slug}"
    row = PricingAccount(
        user_id=user.id,
        name=name,
        platform=platform,
        segment_id=sid,
        store_info_id=store_info_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _account_out(row, roots_by_id)


@router.delete(
    "/store-info/{store_info_id}/department/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unbind_store_info_department(
    store_info_id: UUID,
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> None:
    """Removes the pricing_accounts that wire `store_info_id` to a root segment
    matching `slug`. Mirrors the badge match in `list_store_info`: deletes
    rows with FK `store_info_id` set, **plus** rows whose `(platform, name)`
    matches the store_info entry — the SSH-style implicit name binding.
    """
    sid = await _resolve_root_segment_id(session, slug)
    if sid is None:
        raise HTTPException(400, detail={"code": "invalid_department"})
    info = (
        await session.execute(
            select(StoreInfo).where(
                and_(StoreInfo.id == store_info_id, user_scope(StoreInfo, user))
            )
        )
    ).scalar_one_or_none()
    if info is None:
        raise HTTPException(404, detail={"code": "store_info_not_found"})

    sname = (info.account_name or "").strip().lower()
    splat_alias = _STORE_TO_PRICING_PLATFORM.get(
        (info.platform or "").strip().lower(), (info.platform or "").strip().lower()
    )
    pricing_plat: PricingPlatform | None = None
    try:
        pricing_plat = PricingPlatform(splat_alias)
    except ValueError:
        pricing_plat = None

    # Build the delete predicate: FK match OR (same platform + same name).
    cond = PricingAccount.store_info_id == store_info_id
    if sname and pricing_plat is not None:
        cond = or_(
            cond,
            and_(
                PricingAccount.platform == pricing_plat,
                func.lower(PricingAccount.name) == sname,
            ),
        )
    await session.execute(
        delete(PricingAccount).where(
            and_(
                user_scope(PricingAccount, user),
                PricingAccount.segment_id == sid,
                cond,
            )
        )
    )
    await session.commit()


@router.delete("/store-info/{store_info_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_store_info(
    store_info_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "delete"))
    ],
) -> None:
    res = await session.execute(
        delete(StoreInfo).where(
            and_(
                StoreInfo.id == store_info_id,
                user_scope(StoreInfo, user),
            )
        )
    )
    if res.rowcount == 0:
        raise HTTPException(404, detail={"code": "store_info_not_found"})
    await session.commit()
    return None
