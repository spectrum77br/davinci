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
    CellStatus,
    Department,
    PricingAccount,
    PricingOverride,
    PricingPlatform,
    PricingProduct,
    User,
)
from app.schemas.pricing import (
    PricingAccountCreate,
    PricingAccountOut,
    PricingAccountPatch,
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
)
from app.security.cipher import encrypt
from app.services.pricing.calc import calculate
from app.services.pricing.push import push_one

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
# SKU Audit (read-only stub for 9a; dismiss/undismiss + scan ship in 9d)
# =============================================================================

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
