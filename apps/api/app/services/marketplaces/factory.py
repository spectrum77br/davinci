from datetime import datetime
from typing import Awaitable, Callable

from fastapi import HTTPException

from app.models import IntegrationPlatform
from app.services.marketplaces.base import MarketplaceClient
from app.services.marketplaces.bling import BlingClient


def client_for(
    platform: IntegrationPlatform,
    creds: dict,
    on_token_refresh: Callable[[dict], Awaitable[None]] | None = None,
) -> MarketplaceClient:
    if platform == IntegrationPlatform.BLING:
        return BlingClient(creds, on_token_refresh=on_token_refresh)
    raise HTTPException(501, detail={"code": "platform_not_implemented", "platform": platform.value})


def oauth_authorize_url(platform: IntegrationPlatform, state: str) -> str:
    if platform == IntegrationPlatform.BLING:
        return BlingClient.authorize_url(state)
    raise HTTPException(501, detail={"code": "oauth_not_implemented", "platform": platform.value})


async def oauth_exchange_code(
    platform: IntegrationPlatform, code: str
) -> tuple[dict, datetime | None]:
    """Exchange authorization code for tokens. Returns (creds_dict, token_expires_at)."""
    if platform == IntegrationPlatform.BLING:
        creds = await BlingClient.exchange_code(code)
        from datetime import UTC
        exp_at = datetime.fromtimestamp(creds["expires_at"], tz=UTC)
        return creds, exp_at
    raise HTTPException(501, detail={"code": "oauth_not_implemented", "platform": platform.value})
