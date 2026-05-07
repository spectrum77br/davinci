"""Pricing router (Fase 9a) — accounts/products/overrides CRUD.

Push/Telegram/Audit endpoints arrive in 9b-9d. This sub-phase ships only
the data plane.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import and_, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    AuditDismissedSku,
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    CellStatus,
    Department,
    Integration,
    IntegrationPlatform,
    Listing,
    PricingAccount,
    PricingOverride,
    PricingPlatform,
    PricingProduct,
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
from app.security.cipher import encrypt
from app.services.pricing.audit import scan_missing_skus
from app.services.pricing.calc import calculate
from app.services.pricing.competitor import search_competitors
from app.services.pricing.push import push_one
from app.services.telegram import TelegramClient, TelegramConfigError
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api/pricing", tags=["pricing"])


def _coerce_department(v: str | None) -> Department | None:
    if v is None:
        return None
    try:
        return Department(v)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_department"}) from e


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


def _account_out(row: PricingAccount) -> PricingAccountOut:
    out = PricingAccountOut.model_validate(row)
    out.has_password = bool(row.password_enc)
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
    stmt = select(PricingAccount).where(PricingAccount.user_id == user.id)
    if department:
        stmt = stmt.where(PricingAccount.department == _coerce_department(department))
    if platform:
        stmt = stmt.where(PricingAccount.platform == _coerce_platform(platform))
    stmt = stmt.order_by(PricingAccount.sort_order, PricingAccount.name)
    rows = (await session.execute(stmt)).scalars().all()
    return [_account_out(r) for r in rows]


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
    data = body.model_dump(exclude={"password"})
    data["platform"] = _coerce_platform(body.platform)
    data["department"] = _coerce_department(body.department) or Department.CELULAR
    row = PricingAccount(user_id=user.id, **data)
    if body.password:
        row.password_enc = encrypt(body.password)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _account_out(row)


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
                    PricingAccount.user_id == user.id,
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
    if "department" in data and data["department"] is not None:
        data["department"] = _coerce_department(data["department"])
    for k, v in data.items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return _account_out(row)


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
                PricingAccount.user_id == user.id,
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
    stmt = select(PricingProduct).where(PricingProduct.user_id == user.id)
    if department:
        stmt = stmt.where(PricingProduct.department == _coerce_department(department))
    if in_catalog is not None:
        stmt = stmt.where(PricingProduct.in_catalog == in_catalog)
    if is_active is not None:
        stmt = stmt.where(PricingProduct.is_active == is_active)
    stmt = stmt.order_by(PricingProduct.sku)
    rows = (await session.execute(stmt)).scalars().all()
    return [PricingProductOut.model_validate(r) for r in rows]


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
    data = body.model_dump()
    data["department"] = _coerce_department(body.department) or Department.CELULAR
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
    return PricingProductOut.model_validate(row)


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
                    PricingProduct.user_id == user.id,
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "product_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "department" in data and data["department"] is not None:
        data["department"] = _coerce_department(data["department"])
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
    return PricingProductOut.model_validate(row)


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
                PricingProduct.user_id == user.id,
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
                    PricingProduct.user_id == user.id,
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "product_not_found"})
    row.in_catalog = not row.in_catalog
    await session.commit()
    await session.refresh(row)
    return PricingProductOut.model_validate(row)


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
            select(PricingProduct).where(PricingProduct.user_id == user.id)
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
        data = item.model_dump()
        data["department"] = _coerce_department(item.department) or Department.CELULAR
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
    stmt = select(PricingOverride).where(PricingOverride.user_id == user.id)
    if department:
        dept = _coerce_department(department)
        stmt = stmt.join(
            PricingProduct, PricingProduct.id == PricingOverride.pricing_product_id
        ).where(PricingProduct.department == dept)
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
                    PricingProduct.user_id == user.id,
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
                    PricingAccount.user_id == user.id,
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
                    PricingOverride.user_id == user.id,
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
                    PricingOverride.user_id == user.id,
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
                        PricingProduct.user_id == user.id,
                    )
                )
            )
        ).scalar_one_or_none()
        acc_owned = (
            await session.execute(
                select(PricingAccount.id).where(
                    and_(
                        PricingAccount.id == body.pricing_account_id,
                        PricingAccount.user_id == user.id,
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
                PricingOverride.user_id == user.id,
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
    dept = _coerce_department(department)

    accounts_stmt = select(PricingAccount).where(PricingAccount.user_id == user.id)
    products_stmt = select(PricingProduct).where(PricingProduct.user_id == user.id)
    if dept is not None:
        accounts_stmt = accounts_stmt.where(PricingAccount.department == dept)
        products_stmt = products_stmt.where(PricingProduct.department == dept)
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
            select(PricingOverride).where(PricingOverride.user_id == user.id)
        )
    ).scalars().all()
    by_pair = {(o.pricing_product_id, o.pricing_account_id): o for o in overrides}

    cells: list[PricingGridCell] = []
    for prod in products:
        for acc in accounts:
            ovr = by_pair.get((prod.id, acc.id))
            outcome = calculate(acc, prod, ovr)
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

    return PricingGridOut(
        accounts=[_account_out(a) for a in accounts],
        products=[PricingProductOut.model_validate(p) for p in products],
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
                    PricingProduct.user_id == user.id,
                    PricingProduct.in_catalog.is_(True),
                )
            )
        )
    ).scalars().all()
    if not in_catalog_skus:
        return []

    stmt = select(Listing).where(
        and_(
            Listing.user_id == user.id,
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

    # Validate every item belongs to a catalog account + product first.
    prod_ids = {it.pricing_product_id for it in body.items}
    acc_ids = {it.pricing_account_id for it in body.items}
    products_by_id = {
        p.id: p
        for p in (
            await session.execute(
                select(PricingProduct).where(
                    and_(
                        PricingProduct.user_id == user.id,
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
                        PricingAccount.user_id == user.id,
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
        dept_val = acc.department.value if hasattr(acc.department, "value") else acc.department
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
                select(UserSettings).where(UserSettings.user_id == user.id)
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
            select(AuditDismissedSku.sku).where(AuditDismissedSku.user_id == user.id)
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
                    AuditDismissedSku.user_id == user.id,
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
                AuditDismissedSku.user_id == user.id,
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
                    PricingAccount.user_id == user.id,
                    PricingAccount.integration_id.is_(None),
                )
            )
        )
    ).scalars().all()
    integrations = (
        await session.execute(
            select(Integration).where(Integration.user_id == user.id)
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
    dept = _coerce_department(body.department)
    if dept is None:
        raise HTTPException(400, detail={"code": "invalid_department"})
    row = (
        await session.execute(
            select(PricingAccount).where(
                and_(
                    PricingAccount.id == account_id,
                    PricingAccount.user_id == user.id,
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "account_not_found"})
    row.department = dept
    await session.commit()
    await session.refresh(row)
    return _account_out(row)


# =============================================================================
# Store info (9d) — CRUD com password cifrado
# =============================================================================

def _store_info_out(row: StoreInfo) -> StoreInfoOut:
    out = StoreInfoOut.model_validate(row)
    out.has_password = bool(row.password_enc)
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
            .where(StoreInfo.user_id == user.id)
            .order_by(StoreInfo.sort_order, StoreInfo.platform)
        )
    ).scalars().all()
    return [_store_info_out(r) for r in rows]


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
                    StoreInfo.user_id == user.id,
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
    `pricing_accounts` row if absent (one per (store_info, department))."""
    dept = _coerce_department(body.department)
    if dept is None:
        raise HTTPException(400, detail={"code": "invalid_department"})

    info = (
        await session.execute(
            select(StoreInfo).where(
                and_(
                    StoreInfo.id == store_info_id,
                    StoreInfo.user_id == user.id,
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
                    PricingAccount.user_id == user.id,
                    PricingAccount.store_info_id == store_info_id,
                    PricingAccount.department == dept,
                )
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _account_out(existing)

    name = info.account_name or f"{info.platform} — {dept.value}"
    row = PricingAccount(
        user_id=user.id,
        name=name,
        platform=platform,
        department=dept,
        store_info_id=store_info_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _account_out(row)


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
                StoreInfo.user_id == user.id,
            )
        )
    )
    if res.rowcount == 0:
        raise HTTPException(404, detail={"code": "store_info_not_found"})
    await session.commit()
    return None
