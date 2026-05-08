from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user, require_permission
from app.models import (
    Integration,
    IntegrationPlatform,
    Store,
    User,
)
from app.schemas.integrations import (
    BlingStoreOut,
    BlingStoresOut,
    IntegrationCreate,
    IntegrationOut,
    IntegrationPatch,
    TestConnectionOut,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces import client_for
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()
router = APIRouter(prefix="/api/integrations", tags=["integrations"])


def _to_platform(v: str) -> IntegrationPlatform:
    try:
        return IntegrationPlatform(v)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "platform_invalid", "value": v}) from e


async def _resolve_store(session: AsyncSession, i: Integration) -> Store | None:
    """Resolve the linked store for an integration.

    Source of truth is `stores.integration_id` (set by every write path including
    the company-page "attach integration" patch). `integrations.store_id` may
    lag — e.g. cutover-migrated rows have it NULL — so we always reverse-lookup
    and fall back to the forward FK only if no reverse match exists.
    """
    s = (
        await session.execute(select(Store).where(Store.integration_id == i.id))
    ).scalar_one_or_none()
    if s is None and i.store_id is not None:
        s = (
            await session.execute(select(Store).where(Store.id == i.store_id))
        ).scalar_one_or_none()
    return s


async def _company_id_for(session: AsyncSession, store_id: UUID | None) -> UUID | None:
    if store_id is None:
        return None
    s = (await session.execute(select(Store).where(Store.id == store_id))).scalar_one_or_none()
    return s.company_id if s else None


def _to_out(i: Integration, company_id: UUID | None, store_id: UUID | None = None) -> IntegrationOut:
    return IntegrationOut(
        id=i.id,
        user_id=i.user_id,
        store_id=store_id if store_id is not None else i.store_id,
        company_id=company_id,
        platform=i.platform.value,
        name=i.name,
        status=i.status,
        token_expires_at=i.token_expires_at,
        last_test_at=i.last_test_at,
        last_test_ok=i.last_test_ok,
        last_error=i.last_error,
        created_at=i.created_at,
        updated_at=i.updated_at,
    )


def _make_token_save_callback(session: AsyncSession, integ: Integration):
    async def cb(creds: dict) -> None:
        integ.credentials = encrypt_json(creds)
        exp = creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()
    return cb


@router.get("", response_model=list[IntegrationOut])
async def list_integrations(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_active_user)],
) -> list[IntegrationOut]:
    rows = (await session.execute(select(Integration).order_by(Integration.created_at.desc()))).scalars().all()
    out: list[IntegrationOut] = []
    for i in rows:
        s = await _resolve_store(session, i)
        out.append(_to_out(i, s.company_id if s else None, s.id if s else None))
    return out


@router.get("/{integration_id}", response_model=IntegrationOut)
async def get_integration(
    integration_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_active_user)],
) -> IntegrationOut:
    i = (await session.execute(select(Integration).where(Integration.id == integration_id))).scalar_one_or_none()
    if i is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})
    s = await _resolve_store(session, i)
    return _to_out(i, s.company_id if s else None, s.id if s else None)


@router.post("", response_model=IntegrationOut, status_code=status.HTTP_201_CREATED)
async def create_integration(
    body: IntegrationCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("empresa", "edit"))],
) -> IntegrationOut:
    store = (await session.execute(select(Store).where(Store.id == body.store_id))).scalar_one_or_none()
    if store is None:
        raise HTTPException(404, detail={"code": "store_not_found"})
    if store.integration_id is not None:
        raise HTTPException(409, detail={"code": "store_already_linked"})

    integ = Integration(
        user_id=user.id,
        store_id=store.id,
        platform=_to_platform(body.platform),
        name=body.name,
        credentials=encrypt_json(body.credentials or {}),
    )
    session.add(integ)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        if "uq_integrations_store_id" in str(e.orig):
            raise HTTPException(409, detail={"code": "store_already_linked"}) from e
        raise

    store.integration_id = integ.id
    await session.commit()
    await session.refresh(integ)
    return _to_out(integ, store.company_id)


@router.patch("/{integration_id}", response_model=IntegrationOut)
async def patch_integration(
    integration_id: UUID,
    body: IntegrationPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "edit"))],
) -> IntegrationOut:
    i = (await session.execute(select(Integration).where(Integration.id == integration_id))).scalar_one_or_none()
    if i is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        i.name = data["name"]
    if "status" in data and data["status"] is not None:
        i.status = data["status"]
    if "credentials" in data and data["credentials"] is not None:
        # Merge with existing — never blow away refresh_token by accident.
        existing = decrypt_json(i.credentials) if i.credentials else {}
        existing.update(data["credentials"])
        i.credentials = encrypt_json(existing)
    await session.commit()
    await session.refresh(i)
    s = await _resolve_store(session, i)
    return _to_out(i, s.company_id if s else None, s.id if s else None)


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_integration(
    integration_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "delete"))],
) -> None:
    i = (await session.execute(select(Integration).where(Integration.id == integration_id))).scalar_one_or_none()
    if i is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})
    # Unlink store first (FK on stores.integration_id is SET NULL on integration delete,
    # but we want symmetric explicit cleanup).
    if i.store_id is not None:
        s = (await session.execute(select(Store).where(Store.id == i.store_id))).scalar_one_or_none()
        if s is not None:
            s.integration_id = None
    await session.delete(i)
    await session.commit()
    return None


@router.post("/{integration_id}/test", response_model=TestConnectionOut)
async def test_integration(
    integration_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_active_user)],
) -> TestConnectionOut:
    i = (await session.execute(select(Integration).where(Integration.id == integration_id))).scalar_one_or_none()
    if i is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})
    creds = decrypt_json(i.credentials)
    client = client_for(i.platform, creds, on_token_refresh=_make_token_save_callback(session, i))
    result = await client.test_connection()
    i.last_test_at = datetime.now(UTC)
    i.last_test_ok = result.ok
    i.last_error = None if result.ok else (result.detail or "")[:1000]
    await session.commit()
    return TestConnectionOut(ok=result.ok, detail=result.detail, info=result.info)


@router.get("/{integration_id}/bling-stores", response_model=BlingStoresOut)
async def list_bling_stores(
    integration_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "view"))],
) -> BlingStoresOut:
    i = (await session.execute(select(Integration).where(Integration.id == integration_id))).scalar_one_or_none()
    if i is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})
    if i.platform != IntegrationPlatform.BLING:
        raise HTTPException(400, detail={"code": "not_bling"})
    creds = decrypt_json(i.credentials)
    client = BlingClient(creds, on_token_refresh=_make_token_save_callback(session, i))
    lojas = await client.list_lojas()
    items = [
        BlingStoreOut(
            id=int(loja.get("id")),
            nome=loja.get("nome") or loja.get("descricao"),
            descricao=loja.get("descricao"),
        )
        for loja in lojas
        if loja.get("id") is not None
    ]
    return BlingStoresOut(items=items)
