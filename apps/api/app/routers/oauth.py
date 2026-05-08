"""OAuth start/callback for marketplace providers (Bling for now)."""

from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    Integration,
    IntegrationPlatform,
    OAuthState,
    Store,
    User,
)
from app.schemas.integrations import OAuthStartOut
from app.security.cipher import encrypt_json
from app.services.marketplaces.factory import (
    oauth_authorize_url,
    oauth_exchange_code,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/oauth", tags=["oauth"])

STATE_TTL_MIN = 10


def _to_platform(provider: str) -> IntegrationPlatform:
    alias = {"mercadolivre": "ml", "ml": "ml", "bling": "bling", "shopee": "shopee", "amazon": "amazon"}
    v = alias.get(provider.lower(), provider.lower())
    try:
        return IntegrationPlatform(v)
    except ValueError as e:
        raise HTTPException(404, detail={"code": "unknown_provider"}) from e


@router.get("/{provider}/start", response_model=OAuthStartOut)
async def oauth_start(
    provider: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("empresa", "edit"))],
    store_id: UUID = Query(...),
) -> OAuthStartOut:
    platform = _to_platform(provider)
    store = (await session.execute(select(Store).where(Store.id == store_id))).scalar_one_or_none()
    if store is None:
        raise HTTPException(404, detail={"code": "store_not_found"})
    # Existing integration is allowed: callback re-uses it and refreshes credentials.

    state = token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(minutes=STATE_TTL_MIN)
    session.add(OAuthState(
        state=state,
        platform=platform,
        store_id=store.id,
        user_id=user.id,
        expires_at=expires,
    ))
    await session.commit()

    url = oauth_authorize_url(platform, state)
    return OAuthStartOut(url=url, state=state)


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    code: str = Query(...),
    state: str = Query(...),
):
    platform = _to_platform(provider)
    settings = get_settings()

    row = (
        await session.execute(select(OAuthState).where(OAuthState.state == state))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(400, detail={"code": "state_not_found"})
    if row.consumed_at is not None:
        raise HTTPException(400, detail={"code": "state_consumed"})
    if row.expires_at < datetime.now(UTC):
        raise HTTPException(400, detail={"code": "state_expired"})
    if row.platform != platform:
        raise HTTPException(400, detail={"code": "state_platform_mismatch"})

    try:
        creds, exp_at = await oauth_exchange_code(platform, code)
    except Exception as e:  # noqa: BLE001
        logger.warning("oauth_exchange_failed", err=str(e), provider=provider)
        raise HTTPException(400, detail={"code": "exchange_failed", "err": str(e)[:200]}) from e

    store = None
    if row.store_id:
        store = (
            await session.execute(select(Store).where(Store.id == row.store_id))
        ).scalar_one_or_none()
    if store is None:
        raise HTTPException(404, detail={"code": "store_not_found"})

    integ: Integration | None = None
    if store.integration_id:
        integ = (
            await session.execute(select(Integration).where(Integration.id == store.integration_id))
        ).scalar_one_or_none()

    if integ is None:
        integ = Integration(
            user_id=row.user_id,
            store_id=store.id,
            platform=platform,
            name=f"{platform.value}-{store.id.hex[:8]}",
            credentials=encrypt_json(creds),
            token_expires_at=exp_at,
            status="active",
        )
        session.add(integ)
        await session.flush()
        store.integration_id = integ.id
    else:
        integ.credentials = encrypt_json(creds)
        integ.token_expires_at = exp_at
        integ.status = "active"
        integ.last_error = None

    row.consumed_at = datetime.now(UTC)
    await session.commit()

    redirect = f"{settings.app_url}/companies/{store.company_id}?oauth=ok&platform={platform.value}"
    return RedirectResponse(redirect, status_code=302)
