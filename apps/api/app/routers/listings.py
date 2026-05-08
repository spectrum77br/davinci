"""Listings + listing requests router (Fase 8).

Listings CRUD is **read+patch+delete** only — creation is reserved for the
import job (POST /api/listings/import) so the unique key
(integration_id, external_id) cannot be violated by hand-rolled inserts.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission, user_scope
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingRequest,
    ListingRequestStatus,
    ListingStatus,
    Product,
    User,
)
from app.schemas.listings import (
    ListingImportIn,
    ListingOut,
    ListingPage,
    ListingPatch,
    ListingRequestCreate,
    ListingRequestOut,
    ListingRequestPatch,
)
from app.schemas.products import JobCreatedOut
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["listings"])


# --------------------------------------------------------------------- listings

@router.get("/listings", response_model=ListingPage)
async def list_listings(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("anuncios", "view"))],
    integration_id: UUID | None = Query(None),
    platform: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None),
    unlinked: bool = Query(False, description="Only listings without product_id"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> ListingPage:
    stmt = select(Listing).where(user_scope(Listing, user))
    count_stmt = select(func.count()).select_from(Listing).where(user_scope(Listing, user))

    if integration_id:
        stmt = stmt.where(Listing.integration_id == integration_id)
        count_stmt = count_stmt.where(Listing.integration_id == integration_id)
    if platform:
        try:
            p_enum = IntegrationPlatform(platform)
        except ValueError as e:
            raise HTTPException(400, detail={"code": "invalid_platform"}) from e
        stmt = stmt.where(Listing.platform == p_enum)
        count_stmt = count_stmt.where(Listing.platform == p_enum)
    if status_filter:
        try:
            s_enum = ListingStatus(status_filter)
        except ValueError as e:
            raise HTTPException(400, detail={"code": "invalid_status"}) from e
        stmt = stmt.where(Listing.status == s_enum)
        count_stmt = count_stmt.where(Listing.status == s_enum)
    if search:
        like = f"%{search}%"
        cond = or_(
            Listing.title.ilike(like),
            Listing.sku.ilike(like),
            Listing.external_id.ilike(like),
        )
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if unlinked:
        stmt = stmt.where(Listing.product_id.is_(None))
        count_stmt = count_stmt.where(Listing.product_id.is_(None))

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(Listing.imported_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return ListingPage(
        items=[ListingOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/listings/{listing_id}", response_model=ListingOut)
async def get_listing(
    listing_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("anuncios", "view"))],
) -> ListingOut:
    row = (
        await session.execute(
            select(Listing).where(
                and_(Listing.id == listing_id, user_scope(Listing, user))
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "listing_not_found"})
    return ListingOut.model_validate(row)


@router.patch("/listings/{listing_id}", response_model=ListingOut)
async def patch_listing(
    listing_id: UUID,
    body: ListingPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("anuncios", "edit"))],
) -> ListingOut:
    row = (
        await session.execute(
            select(Listing).where(
                and_(Listing.id == listing_id, user_scope(Listing, user))
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "listing_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        try:
            data["status"] = ListingStatus(data["status"])
        except ValueError as e:
            raise HTTPException(400, detail={"code": "invalid_status"}) from e
    if "product_id" in data and data["product_id"] is not None:
        prod = (
            await session.execute(
                select(Product).where(
                    and_(Product.id == data["product_id"], user_scope(Product, user))
                )
            )
        ).scalar_one_or_none()
        if prod is None:
            raise HTTPException(404, detail={"code": "product_not_found"})
    for k, v in data.items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return ListingOut.model_validate(row)


@router.delete("/listings/{listing_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_listing(
    listing_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("anuncios", "delete"))],
) -> None:
    res = await session.execute(
        delete(Listing).where(
            and_(Listing.id == listing_id, user_scope(Listing, user))
        )
    )
    if res.rowcount == 0:
        raise HTTPException(404, detail={"code": "listing_not_found"})
    await session.commit()
    return None


@router.post(
    "/listings/import",
    response_model=JobCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def enqueue_import(
    body: ListingImportIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("anuncios", "edit"))],
) -> JobCreatedOut:
    integ = (
        await session.execute(
            select(Integration).where(
                and_(
                    Integration.id == body.integration_id,
                    user_scope(Integration, user),
                )
            )
        )
    ).scalar_one_or_none()
    if integ is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})
    if integ.platform not in {IntegrationPlatform.ML, IntegrationPlatform.SHOPEE}:
        raise HTTPException(
            501,
            detail={
                "code": "platform_not_supported",
                "platform": integ.platform.value,
            },
        )

    job = BackgroundJob(
        type=BackgroundJobType.IMPORT_LISTINGS,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={
            "integration_id": str(integ.id),
            "platform": integ.platform.value,
            "max_pages": body.max_pages,
        },
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_pool()
    arq = await pool.enqueue_job(
        "import_listings_run",
        str(job.id),
        str(user.id),
        str(integ.id),
        body.max_pages,
    )
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    return JobCreatedOut(job_id=job.id)


# ------------------------------------------------------------- listing_requests

@router.get("/listing-requests", response_model=list[ListingRequestOut])
async def list_listing_requests(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("anuncios", "view"))],
    status_filter: str | None = Query(None, alias="status"),
) -> list[ListingRequestOut]:
    stmt = select(ListingRequest).where(user_scope(ListingRequest, user))
    if status_filter:
        try:
            s_enum = ListingRequestStatus(status_filter)
        except ValueError as e:
            raise HTTPException(400, detail={"code": "invalid_status"}) from e
        stmt = stmt.where(ListingRequest.status == s_enum)
    rows = (
        await session.execute(stmt.order_by(ListingRequest.created_at.desc()))
    ).scalars().all()
    return [ListingRequestOut.model_validate(r) for r in rows]


@router.post(
    "/listing-requests",
    response_model=ListingRequestOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_listing_request(
    body: ListingRequestCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("anuncios", "edit"))],
) -> ListingRequestOut:
    try:
        platform = IntegrationPlatform(body.platform)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_platform"}) from e
    row = ListingRequest(
        user_id=user.id,
        platform=platform,
        sku=body.sku,
        product_name=body.product_name,
        description=body.description,
        requested_price=body.requested_price,
        category=body.category,
        notes=body.notes,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ListingRequestOut.model_validate(row)


@router.patch("/listing-requests/{request_id}", response_model=ListingRequestOut)
async def patch_listing_request(
    request_id: UUID,
    body: ListingRequestPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("anuncios", "edit"))],
) -> ListingRequestOut:
    row = (
        await session.execute(
            select(ListingRequest).where(
                and_(
                    ListingRequest.id == request_id,
                    user_scope(ListingRequest, user),
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "listing_request_not_found"})
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        try:
            data["status"] = ListingRequestStatus(data["status"])
        except ValueError as e:
            raise HTTPException(400, detail={"code": "invalid_status"}) from e
    for k, v in data.items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return ListingRequestOut.model_validate(row)


@router.delete(
    "/listing-requests/{request_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_listing_request(
    request_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("anuncios", "delete"))],
) -> None:
    res = await session.execute(
        delete(ListingRequest).where(
            and_(
                ListingRequest.id == request_id,
                user_scope(ListingRequest, user),
            )
        )
    )
    if res.rowcount == 0:
        raise HTTPException(404, detail={"code": "listing_request_not_found"})
    await session.commit()
    return None
