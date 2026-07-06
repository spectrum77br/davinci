from collections.abc import Awaitable, Callable
from datetime import datetime

from fastapi import HTTPException

from app.models import IntegrationPlatform
from app.services.marketplaces.amazon import AmazonClient
from app.services.marketplaces.base import MarketplaceClient
from app.services.marketplaces.bling import BlingClient
from app.services.marketplaces.magalu import MagaluClient
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient
from app.services.marketplaces.temu import TemuClient
from app.services.marketplaces.tiktok import TikTokClient


def client_for(
    platform: IntegrationPlatform,
    creds: dict,
    on_token_refresh: Callable[[dict], Awaitable[None]] | None = None,
    integration_id=None,
) -> MarketplaceClient:
    if platform == IntegrationPlatform.BLING:
        return BlingClient(creds, on_token_refresh=on_token_refresh, integration_id=integration_id)
    if platform == IntegrationPlatform.ML:
        return MercadoLivreClient(creds, on_token_refresh=on_token_refresh)
    if platform == IntegrationPlatform.SHOPEE:
        return ShopeeClient(creds, on_token_refresh=on_token_refresh)
    if platform == IntegrationPlatform.AMAZON:
        return AmazonClient(creds, on_token_refresh=on_token_refresh)
    if platform == IntegrationPlatform.TIKTOK:
        return TikTokClient(creds, on_token_refresh=on_token_refresh)
    if platform == IntegrationPlatform.TEMU:
        return TemuClient(creds, on_token_refresh=on_token_refresh)
    if platform == IntegrationPlatform.MAGALU:
        return MagaluClient(creds, on_token_refresh=on_token_refresh)
    raise HTTPException(501, detail={"code": "platform_not_implemented", "platform": platform.value})


def oauth_authorize_url(platform: IntegrationPlatform, state: str) -> str:
    if platform == IntegrationPlatform.BLING:
        return BlingClient.authorize_url(state)
    if platform == IntegrationPlatform.ML:
        return MercadoLivreClient.authorize_url(state)
    raise HTTPException(501, detail={"code": "oauth_not_implemented", "platform": platform.value})


async def oauth_exchange_code(
    platform: IntegrationPlatform, code: str
) -> tuple[dict, datetime | None]:
    """Exchange authorization code for tokens. Returns (creds_dict, token_expires_at)."""
    from datetime import UTC

    if platform == IntegrationPlatform.BLING:
        creds = await BlingClient.exchange_code(code)
        exp_at = datetime.fromtimestamp(creds["expires_at"], tz=UTC)
        return creds, exp_at
    if platform == IntegrationPlatform.ML:
        creds = await MercadoLivreClient.exchange_code(code)
        exp_at = datetime.fromtimestamp(creds["expires_at"], tz=UTC)
        return creds, exp_at
    raise HTTPException(501, detail={"code": "oauth_not_implemented", "platform": platform.value})
