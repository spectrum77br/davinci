from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import Company, Marketplace, Store, StoreStatus, User
from app.schemas.companies import StoreCreate, StoreOut, StorePatch

logger = structlog.get_logger()
router = APIRouter(prefix="/api/stores", tags=["stores"])


def _to_marketplace(v: str) -> Marketplace:
    try:
        return Marketplace(v)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "marketplace_invalid", "value": v}) from e


def _to_status(v: str) -> StoreStatus:
    try:
        return StoreStatus(v)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "status_invalid", "value": v}) from e


@router.get("", response_model=list[StoreOut])
async def list_stores(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "view"))],
    company_id: UUID | None = Query(None),
    marketplace: str | None = Query(None),
    status_q: str | None = Query(None, alias="status"),
) -> list[StoreOut]:
    stmt = select(Store)
    if company_id:
        stmt = stmt.where(Store.company_id == company_id)
    if marketplace:
        stmt = stmt.where(Store.marketplace == _to_marketplace(marketplace))
    if status_q:
        stmt = stmt.where(Store.status == _to_status(status_q))
    rows = (await session.execute(stmt)).scalars().all()
    return [StoreOut.model_validate(s) for s in rows]


@router.post("", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
async def create_store(
    body: StoreCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "edit"))],
) -> StoreOut:
    company = (
        await session.execute(select(Company).where(Company.id == body.company_id))
    ).scalar_one_or_none()
    if company is None:
        raise HTTPException(404, detail={"code": "company_not_found"})
    s = Store(
        company_id=body.company_id,
        marketplace=_to_marketplace(body.marketplace),
        apelido_override=body.apelido_override,
        status=_to_status(body.status) if body.status else StoreStatus.PENDING,
        notes=body.notes,
        bling_store_id=body.bling_store_id,
    )
    session.add(s)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "uq_stores_company_id_marketplace" in str(e.orig):
            raise HTTPException(409, detail={"code": "store_already_exists"}) from e
        raise
    await session.refresh(s)
    logger.info("store_created", id=str(s.id), company_id=str(s.company_id), marketplace=s.marketplace.value)
    return StoreOut.model_validate(s)


@router.patch("/{store_id}", response_model=StoreOut)
async def patch_store(
    store_id: UUID,
    body: StorePatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "edit"))],
) -> StoreOut:
    s = (await session.execute(select(Store).where(Store.id == store_id))).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "store_not_found"})
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        data["status"] = _to_status(data["status"])
    for k, v in data.items():
        setattr(s, k, v)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "uq_stores_integration_id" in str(e.orig):
            raise HTTPException(409, detail={"code": "integration_already_linked"}) from e
        raise
    await session.refresh(s)
    return StoreOut.model_validate(s)


@router.delete("/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_store(
    store_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "delete"))],
) -> None:
    s = (await session.execute(select(Store).where(Store.id == store_id))).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "store_not_found"})
    await session.delete(s)
    await session.commit()
    return None


@router.post("/{store_id}/unlink-integration", response_model=StoreOut)
async def unlink_integration(
    store_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "edit"))],
) -> StoreOut:
    s = (await session.execute(select(Store).where(Store.id == store_id))).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "store_not_found"})
    s.integration_id = None
    await session.commit()
    await session.refresh(s)
    return StoreOut.model_validate(s)
