"""Discrepancies router (Fase 11).

Lists stock mismatches between platform listings and Bling product stock.
A listing is considered discrepant when:
  - it is linked to a product (`listings.product_id IS NOT NULL`),
  - both stocks are known (`listings.stock IS NOT NULL`),
  - and the difference exceeds the optional `min_diff` threshold.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import IntegrationPlatform, Listing, Product, User

logger = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["discrepancies"])


class DiscrepancyOut(BaseModel):
    listing_id: UUID
    integration_id: UUID
    platform: str
    external_id: str
    title: str
    sku: str | None
    product_id: UUID
    expected_stock: int
    actual_stock: int
    diff: int
    last_imported_at: datetime


class DiscrepancyPage(BaseModel):
    items: list[DiscrepancyOut]
    total: int


@router.get("/discrepancies", response_model=DiscrepancyPage)
async def list_discrepancies(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("anuncios", "view"))],
    platform: str | None = Query(None),
    integration_id: UUID | None = Query(None),
    min_diff: int = Query(1, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> DiscrepancyPage:
    diff_expr = func.abs(Listing.stock - Product.stock)
    base = (
        select(
            Listing.id.label("listing_id"),
            Listing.integration_id,
            Listing.platform,
            Listing.external_id,
            Listing.title,
            Listing.sku,
            Listing.product_id,
            Product.stock.label("expected_stock"),
            Listing.stock.label("actual_stock"),
            diff_expr.label("diff"),
            Listing.imported_at.label("last_imported_at"),
        )
        .join(Product, Product.id == Listing.product_id)
        .where(
            Listing.user_id == user.id,
            Listing.product_id.is_not(None),
            Listing.stock.is_not(None),
            diff_expr >= max(min_diff, 1),
        )
    )

    if platform:
        try:
            p_enum = IntegrationPlatform(platform)
        except ValueError as e:
            raise HTTPException(400, detail={"code": "invalid_platform"}) from e
        base = base.where(Listing.platform == p_enum)
    if integration_id:
        base = base.where(Listing.integration_id == integration_id)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = (
        base.order_by(diff_expr.desc(), Listing.imported_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).all()
    items = [
        DiscrepancyOut(
            listing_id=r.listing_id,
            integration_id=r.integration_id,
            platform=r.platform.value if hasattr(r.platform, "value") else str(r.platform),
            external_id=r.external_id,
            title=r.title,
            sku=r.sku,
            product_id=r.product_id,
            expected_stock=int(r.expected_stock or 0),
            actual_stock=int(r.actual_stock or 0),
            diff=int(r.diff or 0),
            last_imported_at=r.last_imported_at,
        )
        for r in rows
    ]
    return DiscrepancyPage(items=items, total=total)
