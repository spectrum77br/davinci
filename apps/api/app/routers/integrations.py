import time
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_active_user, require_permission
from app.models import (
    Integration,
    IntegrationPlatform,
    OAuthState,
    Store,
    User,
)
from app.schemas.integrations import (
    BlingStoreOut,
    BlingStoresOut,
    IntegrationCreate,
    IntegrationOut,
    IntegrationPatch,
    OAuthStartOut,
    TestConnectionOut,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces import client_for
from app.services.marketplaces.bling import BlingClient
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient
from app.services.marketplaces.tiktok import TikTokClient

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
        vacation_mode=i.vacation_mode,
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
    user: Annotated[User, Depends(require_permission("integracoes", "edit"))],
) -> IntegrationOut:
    # Loja é opcional. Quando informada, validamos e vinculamos; quando
    # ausente, criamos a integração "solta" (store_id=None).
    store = None
    if body.store_id is not None:
        store = (
            await session.execute(select(Store).where(Store.id == body.store_id))
        ).scalar_one_or_none()
        if store is None:
            raise HTTPException(404, detail={"code": "store_not_found"})
        if store.integration_id is not None:
            raise HTTPException(409, detail={"code": "store_already_linked"})

    integ = Integration(
        user_id=user.id,
        store_id=store.id if store else None,
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

    if store is not None:
        store.integration_id = integ.id
    await session.commit()
    await session.refresh(integ)
    return _to_out(integ, store.company_id if store else None)


@router.patch("/{integration_id}", response_model=IntegrationOut)
async def patch_integration(
    integration_id: UUID,
    body: IntegrationPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("integracoes", "edit"))],
) -> IntegrationOut:
    i = (await session.execute(select(Integration).where(Integration.id == integration_id))).scalar_one_or_none()
    if i is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        i.name = data["name"]
    if "status" in data and data["status"] is not None:
        i.status = data["status"]
    if "vacation_mode" in data and data["vacation_mode"] is not None:
        i.vacation_mode = data["vacation_mode"]
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
    _u: Annotated[User, Depends(require_permission("integracoes", "delete"))],
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

    # Bling: NÃO dispara HTTP durante cooldown CF (o teste falharia e
    # re-armaria o cooldown) nem com reauth pendente. Retorna resposta
    # amigável pra UI mostrar contagem regressiva / pedir reconexão.
    if i.platform == IntegrationPlatform.BLING:
        if i.status == "reauth_needed":
            return TestConnectionOut(
                ok=False,
                code="bling_reauth_needed",
                detail="Reconectar Bling — refresh_token expirou",
            )
        from app.redis_client import redis as _redis
        blocked = await _redis.get("bling:cf_cooldown_until")
        if blocked is not None:
            try:
                ttl = int(blocked) - int(time.time())
            except (TypeError, ValueError):
                ttl = 0
            if ttl > 0:
                return TestConnectionOut(
                    ok=False,
                    code="bling_cf_cooldown_active",
                    ttl_seconds=ttl,
                    retry_after_epoch=int(blocked),
                    detail=f"Bling em cooldown CF, aguarde {ttl // 60}min{ttl % 60}s",
                )

    creds = decrypt_json(i.credentials)
    client = client_for(i.platform, creds, on_token_refresh=_make_token_save_callback(session, i), integration_id=i.id)
    result = await client.test_connection()
    i.last_test_at = datetime.now(UTC)
    i.last_test_ok = result.ok
    i.last_error = None if result.ok else (result.detail or "")[:1000]
    await session.commit()
    return TestConnectionOut(ok=result.ok, detail=result.detail, info=result.info)


# =============================================================================
# TikTok Shop OAuth — integration-bound (each integration carries its own
# app_key/app_secret/service_id; no global env credentials).
# =============================================================================

TIKTOK_STATE_TTL_MIN = 10


@router.get("/tiktok/start", response_model=OAuthStartOut)
async def tiktok_oauth_start(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("integracoes", "edit"))],
    integration_id: Annotated[UUID, Query(alias="integrationId")],
    origin: Annotated[str | None, Query()] = None,
) -> OAuthStartOut:
    del origin  # accepted for FE compatibility; final redirect uses settings.app_url
    integ = (
        await session.execute(select(Integration).where(Integration.id == integration_id))
    ).scalar_one_or_none()
    if integ is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})
    if integ.platform != IntegrationPlatform.TIKTOK:
        raise HTTPException(400, detail={"code": "not_tiktok"})
    creds = decrypt_json(integ.credentials) if integ.credentials else {}
    service_id = str(creds.get("service_id") or "").strip()
    if not service_id:
        raise HTTPException(400, detail={"code": "missing_service_id"})

    state = token_urlsafe(32)
    session.add(
        OAuthState(
            state=state,
            platform=IntegrationPlatform.TIKTOK,
            store_id=integ.store_id,
            user_id=user.id,
            code_verifier=str(integ.id),  # threads integration_id to callback
            expires_at=datetime.now(UTC) + timedelta(minutes=TIKTOK_STATE_TTL_MIN),
        )
    )
    await session.commit()
    return OAuthStartOut(url=TikTokClient.authorize_url(service_id, state), state=state)


@router.get("/tiktok/callback")
async def tiktok_oauth_callback(
    session: Annotated[AsyncSession, Depends(get_session)],
    code: Annotated[str, Query(...)],
    state: Annotated[str, Query(...)],
):
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
    if row.platform != IntegrationPlatform.TIKTOK:
        raise HTTPException(400, detail={"code": "state_platform_mismatch"})

    integ: Integration | None = None
    if row.code_verifier:
        try:
            integ_id = UUID(row.code_verifier)
        except ValueError:
            integ_id = None
        if integ_id is not None:
            integ = (
                await session.execute(select(Integration).where(Integration.id == integ_id))
            ).scalar_one_or_none()
    if integ is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})

    creds = decrypt_json(integ.credentials) if integ.credentials else {}
    app_key = str(creds.get("app_key") or "").strip()
    app_secret = str(creds.get("app_secret") or "").strip()
    if not app_key or not app_secret:
        raise HTTPException(400, detail={"code": "missing_app_credentials"})

    try:
        tokens = await TikTokClient.exchange_code(code, app_key, app_secret)
    except Exception as e:  # noqa: BLE001
        logger.warning("tiktok_exchange_failed", err=str(e), integration_id=str(integ.id))
        raise HTTPException(400, detail={"code": "exchange_failed", "err": str(e)[:200]}) from e

    access_token = str(tokens.get("access_token") or "").strip()
    try:
        shop = await TikTokClient.fetch_shop_info(access_token, app_key, app_secret)
    except Exception as e:  # noqa: BLE001
        logger.warning("tiktok_shop_fetch_failed", err=str(e), integration_id=str(integ.id))
        shop = {}

    creds.update(
        {
            "access_token": access_token,
            "refresh_token": tokens.get("refresh_token"),
            "token_expires_at": tokens.get("token_expires_at"),
            "refresh_token_expires_at": tokens.get("refresh_token_expires_at"),
            "open_id": tokens.get("open_id"),
            "seller_name": tokens.get("seller_name"),
            "seller_base_region": tokens.get("seller_base_region"),
            "shop_cipher": shop.get("cipher") or shop.get("shop_cipher"),
            "shop_name": shop.get("name") or shop.get("shop_name"),
        }
    )
    integ.credentials = encrypt_json(creds)
    integ.token_expires_at = (
        datetime.fromtimestamp(int(tokens["token_expires_at"]), tz=UTC)
        if tokens.get("token_expires_at")
        else None
    )
    integ.status = "active"
    integ.last_error = None
    row.consumed_at = datetime.now(UTC)
    await session.commit()

    company_id = await _company_id_for(session, integ.store_id)
    suffix = f"/companies/{company_id}" if company_id else "/integrations"
    return RedirectResponse(
        f"{settings.app_url}{suffix}?oauth=ok&platform=tiktok",
        status_code=302,
    )


# =============================================================================
# Mercado Livre OAuth — integration-bound (each integration carries its own
# client_id/client_secret; the seller logs into their own ML app). Mirrors the
# TikTok flow above; no global env credentials required.
# =============================================================================

ML_STATE_TTL_MIN = 10


@router.get("/ml/start", response_model=OAuthStartOut)
async def ml_oauth_start(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("integracoes", "edit"))],
    integration_id: Annotated[UUID, Query(alias="integrationId")],
    origin: Annotated[str | None, Query()] = None,
) -> OAuthStartOut:
    del origin  # accepted for FE compatibility; final redirect uses settings.app_url
    integ = (
        await session.execute(select(Integration).where(Integration.id == integration_id))
    ).scalar_one_or_none()
    if integ is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})
    if integ.platform != IntegrationPlatform.ML:
        raise HTTPException(400, detail={"code": "not_ml"})
    creds = decrypt_json(integ.credentials) if integ.credentials else {}
    client_id = str(creds.get("client_id") or "").strip()
    if not client_id:
        raise HTTPException(400, detail={"code": "missing_client_id"})

    settings = get_settings()
    state = token_urlsafe(32)
    session.add(
        OAuthState(
            state=state,
            platform=IntegrationPlatform.ML,
            store_id=integ.store_id,
            user_id=user.id,
            code_verifier=str(integ.id),  # threads integration_id to callback
            expires_at=datetime.now(UTC) + timedelta(minutes=ML_STATE_TTL_MIN),
        )
    )
    await session.commit()
    url = MercadoLivreClient.authorize_url(
        state, client_id=client_id, redirect_uri=settings.ml_redirect_uri
    )
    return OAuthStartOut(url=url, state=state)


@router.get("/ml/callback")
async def ml_oauth_callback(
    session: Annotated[AsyncSession, Depends(get_session)],
    code: Annotated[str, Query(...)],
    state: Annotated[str, Query(...)],
):
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
    if row.platform != IntegrationPlatform.ML:
        raise HTTPException(400, detail={"code": "state_platform_mismatch"})

    integ: Integration | None = None
    if row.code_verifier:
        try:
            integ_id = UUID(row.code_verifier)
        except ValueError:
            integ_id = None
        if integ_id is not None:
            integ = (
                await session.execute(select(Integration).where(Integration.id == integ_id))
            ).scalar_one_or_none()
    if integ is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})

    creds = decrypt_json(integ.credentials) if integ.credentials else {}
    client_id = str(creds.get("client_id") or "").strip()
    client_secret = str(creds.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise HTTPException(400, detail={"code": "missing_app_credentials"})

    try:
        new_creds = await MercadoLivreClient.exchange_code(
            code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=settings.ml_redirect_uri,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("ml_exchange_failed", err=str(e), integration_id=str(integ.id))
        raise HTTPException(400, detail={"code": "exchange_failed", "err": str(e)[:200]}) from e

    # Merge the exchanged tokens over the existing creds (keeps client_id/secret
    # and any manually-entered fields the operator may have set).
    creds.update(new_creds)
    integ.credentials = encrypt_json(creds)
    exp = creds.get("expires_at")
    integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC) if exp else None
    integ.status = "active"
    integ.last_error = None
    row.consumed_at = datetime.now(UTC)
    await session.commit()

    company_id = await _company_id_for(session, integ.store_id)
    suffix = f"/companies/{company_id}" if company_id else "/integrations"
    return RedirectResponse(
        f"{settings.app_url}{suffix}?oauth=ok&platform=ml",
        status_code=302,
    )


# =============================================================================
# Shopee OAuth — integration-bound "auth_partner" login (each integration
# carries its own partner_id/partner_key; the seller authorizes their shop).
# Mirrors the TikTok/ML flow above, with two Shopee-specific twists:
#   * the CSRF `state` rides in the callback PATH, not a query param — Shopee
#     appends `?code=&shop_id=` to the redirect and won't reliably preserve a
#     pre-existing query string;
#   * the redirect returns `shop_id`, stored alongside the tokens.
# =============================================================================

SHOPEE_STATE_TTL_MIN = 10


def _shopee_redirect_base() -> str:
    """Public callback base URL (no trailing slash). An explicit setting is
    required — in dev the API and web run on different ports, so it can't be
    derived from app_url the way the final success redirect can."""
    return get_settings().shopee_redirect_uri.rstrip("/")


@router.get("/shopee/start", response_model=OAuthStartOut)
async def shopee_oauth_start(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("integracoes", "edit"))],
    integration_id: Annotated[UUID, Query(alias="integrationId")],
    origin: Annotated[str | None, Query()] = None,
) -> OAuthStartOut:
    del origin  # accepted for FE compatibility; redirect uses shopee_redirect_uri
    integ = (
        await session.execute(select(Integration).where(Integration.id == integration_id))
    ).scalar_one_or_none()
    if integ is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})
    if integ.platform != IntegrationPlatform.SHOPEE:
        raise HTTPException(400, detail={"code": "not_shopee"})

    settings = get_settings()
    creds = decrypt_json(integ.credentials) if integ.credentials else {}
    partner_id = str(creds.get("partner_id") or settings.shopee_partner_id or "").strip()
    partner_key = str(creds.get("partner_key") or settings.shopee_partner_key or "").strip()
    if not partner_id or not partner_key:
        raise HTTPException(400, detail={"code": "missing_partner_credentials"})
    redirect_base = _shopee_redirect_base()
    if not redirect_base:
        raise HTTPException(400, detail={"code": "missing_shopee_redirect_uri"})

    state = token_urlsafe(32)
    session.add(
        OAuthState(
            state=state,
            platform=IntegrationPlatform.SHOPEE,
            store_id=integ.store_id,
            user_id=user.id,
            code_verifier=str(integ.id),  # threads integration_id to callback
            expires_at=datetime.now(UTC) + timedelta(minutes=SHOPEE_STATE_TTL_MIN),
        )
    )
    await session.commit()
    url = ShopeeClient.authorize_url(
        partner_id=partner_id,
        partner_key=partner_key,
        redirect=f"{redirect_base}/{state}",
        use_sandbox=settings.shopee_use_sandbox,
    )
    return OAuthStartOut(url=url, state=state)


@router.get("/shopee/callback/{state}")
async def shopee_oauth_callback(
    session: Annotated[AsyncSession, Depends(get_session)],
    state: str,
    code: Annotated[str, Query(...)],
    shop_id: Annotated[str | None, Query()] = None,
):
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
    if row.platform != IntegrationPlatform.SHOPEE:
        raise HTTPException(400, detail={"code": "state_platform_mismatch"})
    if not shop_id:
        raise HTTPException(400, detail={"code": "missing_shop_id"})

    integ: Integration | None = None
    if row.code_verifier:
        try:
            integ_id = UUID(row.code_verifier)
        except ValueError:
            integ_id = None
        if integ_id is not None:
            integ = (
                await session.execute(select(Integration).where(Integration.id == integ_id))
            ).scalar_one_or_none()
    if integ is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})

    creds = decrypt_json(integ.credentials) if integ.credentials else {}
    partner_id = str(creds.get("partner_id") or settings.shopee_partner_id or "").strip()
    partner_key = str(creds.get("partner_key") or settings.shopee_partner_key or "").strip()
    if not partner_id or not partner_key:
        raise HTTPException(400, detail={"code": "missing_partner_credentials"})

    try:
        new_creds = await ShopeeClient.exchange_code(
            code,
            shop_id,
            partner_id=partner_id,
            partner_key=partner_key,
            use_sandbox=settings.shopee_use_sandbox,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("shopee_exchange_failed", err=str(e), integration_id=str(integ.id))
        raise HTTPException(400, detail={"code": "exchange_failed", "err": str(e)[:200]}) from e

    # Merge exchanged tokens over the existing creds (keeps partner_id/key and
    # any manually-entered fields), then stamp expiry so both the refresh cron
    # and on-demand refresh pick it up.
    creds.update(new_creds)
    integ.credentials = encrypt_json(creds)
    exp = creds.get("expires_at")
    integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC) if exp else None
    integ.status = "active"
    integ.last_error = None
    row.consumed_at = datetime.now(UTC)
    await session.commit()

    company_id = await _company_id_for(session, integ.store_id)
    suffix = f"/companies/{company_id}" if company_id else "/integrations"
    return RedirectResponse(
        f"{settings.app_url}{suffix}?oauth=ok&platform=shopee",
        status_code=302,
    )


@router.get("/{integration_id}/bling-stores", response_model=BlingStoresOut)
async def list_bling_stores(
    integration_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("integracoes", "view"))],
) -> BlingStoresOut:
    i = (await session.execute(select(Integration).where(Integration.id == integration_id))).scalar_one_or_none()
    if i is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})
    if i.platform != IntegrationPlatform.BLING:
        raise HTTPException(400, detail={"code": "not_bling"})
    creds = decrypt_json(i.credentials)
    client = BlingClient(creds, on_token_refresh=_make_token_save_callback(session, i), integration_id=i.id)
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
