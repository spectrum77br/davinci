from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import MARKETPLACES, Company, Store, User
from app.schemas.companies import (
    CompanyCreate,
    CompanyDetailOut,
    CompanyGridOut,
    CompanyGridRow,
    CompanyOut,
    CompanyPatch,
    GridStoreCell,
    StoreOut,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/companies", tags=["companies"])


def _company_label(c: Company, override: str | None = None) -> str:
    return override or c.apelido


@router.get("/grid", response_model=CompanyGridOut)
async def companies_grid(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "view"))],
) -> CompanyGridOut:
    companies = (await session.execute(select(Company).order_by(Company.apelido))).scalars().all()
    stores_rows = (await session.execute(select(Store))).scalars().all()
    by_company: dict[UUID, dict[str, Store]] = {}
    for s in stores_rows:
        by_company.setdefault(s.company_id, {})[s.marketplace.value] = s

    rows: list[CompanyGridRow] = []
    for c in companies:
        cells: dict[str, GridStoreCell | None] = {}
        company_stores = by_company.get(c.id, {})
        for mk in MARKETPLACES:
            s = company_stores.get(mk)
            if s is None:
                cells[mk] = None
            else:
                cells[mk] = GridStoreCell(
                    id=s.id,
                    status=s.status.value,
                    label=_company_label(c, s.apelido_override),
                    integration_id=s.integration_id,
                    bling_store_id=s.bling_store_id,
                )
        rows.append(CompanyGridRow(company=CompanyOut.model_validate(c), stores=cells))
    return CompanyGridOut(marketplaces=list(MARKETPLACES), rows=rows)


@router.get("", response_model=list[CompanyOut])
async def list_companies(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "view"))],
) -> list[CompanyOut]:
    rows = (await session.execute(select(Company).order_by(Company.apelido))).scalars().all()
    return [CompanyOut.model_validate(c) for c in rows]


@router.get("/{company_id}", response_model=CompanyDetailOut)
async def get_company(
    company_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "view"))],
) -> CompanyDetailOut:
    c = (await session.execute(select(Company).where(Company.id == company_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "company_not_found"})
    stores = (await session.execute(select(Store).where(Store.company_id == c.id))).scalars().all()
    out = CompanyDetailOut.model_validate(c)
    out.stores = [StoreOut.model_validate(s) for s in stores]
    return out


@router.post("", response_model=CompanyOut, status_code=status.HTTP_201_CREATED)
async def create_company(
    body: CompanyCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "edit"))],
) -> CompanyOut:
    c = Company(**body.model_dump())
    session.add(c)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "uq_companies_cnpj" in str(e.orig):
            raise HTTPException(409, detail={"code": "cnpj_exists"}) from e
        raise
    await session.refresh(c)
    return CompanyOut.model_validate(c)


@router.patch("/{company_id}", response_model=CompanyOut)
async def patch_company(
    company_id: UUID,
    body: CompanyPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "edit"))],
) -> CompanyOut:
    c = (await session.execute(select(Company).where(Company.id == company_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "company_not_found"})
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(c, k, v)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "uq_companies_cnpj" in str(e.orig):
            raise HTTPException(409, detail={"code": "cnpj_exists"}) from e
        raise
    await session.refresh(c)
    return CompanyOut.model_validate(c)


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company(
    company_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "delete"))],
) -> None:
    c = (await session.execute(select(Company).where(Company.id == company_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "company_not_found"})
    await session.delete(c)
    await session.commit()
    logger.info("company_deleted", id=str(company_id))
    return None
