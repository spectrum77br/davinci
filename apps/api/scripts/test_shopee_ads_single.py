"""Isolated single-call probe for the Shopee Ads throttle.

Run inside the api container:

    docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T api \
        python -m scripts.test_shopee_ads_single

What it does: picks ONE active Shopee integration, instantiates a
`ShopeeClient` + `ShopeeAdsClient`, and makes exactly ONE call to
`/api/v2/ads/get_total_balance`. No retries, no loops. The result tells
us whether the partner-id is still under the global throttle:

  ✅ balance returned        → throttle cleared, can enable cron
  ❌ ShopeeAdsRateLimit       → still throttled, open Shopee ticket
  ⚠️ other Shopee error       → token / permission issue (different fix)

Does NOT set the Redis cooldown so an operator can probe without
breaking the cron's next tick.
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.db import session_scope
from app.models.integration import Integration
from app.security.cipher import decrypt_json
from app.services.marketplaces.shopee import ShopeeClient
from app.services.shopee_ads import ShopeeAdsClient, ShopeeAdsError, ShopeeAdsRateLimit


async def main() -> int:
    async with session_scope() as session:
        # Pick the first active Shopee integration regardless of
        # `ads_enabled` — the operator might want to probe even when the
        # cron is gated off.
        integ = (
            await session.execute(
                select(Integration)
                .where(Integration.platform == "shopee")
                .where(Integration.status == "active")
                .order_by(Integration.name)
                .limit(1)
            )
        ).scalar_one_or_none()
        if integ is None:
            print("❌ Nenhuma integração Shopee ativa encontrada.")
            return 1

        print(f"🔍 Testando loja: {integ.name} ({str(integ.id)[:8]})")
        creds = decrypt_json(integ.credentials)
        shopee = ShopeeClient(creds)
        ads = ShopeeAdsClient(shopee)

        try:
            balance = await ads.get_balance()
            print(f"✅ SUCESSO — balance: R$ {balance:.2f}")
            print("→ Throttle do partner-id liberou.")
            print("→ Pode setar ENABLE_SHOPEE_ADS=true + restart worker.")
            return 0
        except ShopeeAdsRateLimit as e:
            print(f"❌ RATE LIMITED — code={e.code}: {e.message}")
            print("→ Partner_id ainda bloqueado pela Shopee.")
            print("→ Abrir ticket no Shopee Open Platform pedindo revisão da cota Ads.")
            return 2
        except ShopeeAdsError as e:
            print(f"⚠️ Erro Shopee — code={e.code}: {e.message}")
            if e.code in ("error_permission_denied", "error_permission"):
                print("→ Conta provavelmente não tem permissão Marketing/Ads ativada.")
                print("→ Verificar no Shopee Seller Center: Marketing → Configurações.")
            elif e.code in ("error_auth", "error_token_expired"):
                print("→ Token expirado. O refresh automático deveria resolver no próximo sync.")
            return 3
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ Erro inesperado: {type(e).__name__}: {e}")
            return 4


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
