"""Restore integration credentials from `stocksync_legacy` into davinci.

Run after `cli migrate` left integrations marked disconnected. Reads each
legacy integration, finds the matching davinci row by (user.email, platform,
name), and writes encrypted credentials + status.
"""
# ruff: noqa: S608

from __future__ import annotations

import argparse
import asyncio
import json
import logging

import asyncpg

from app.config import get_settings
from app.security.cipher import encrypt_json

log = logging.getLogger("restore_integrations")


# Map legacy camelCase credential keys → davinci snake_case keys, per platform.
KEY_MAP: dict[str, dict[str, str]] = {
    "ml": {
        "accessToken": "access_token",
        "refreshToken": "refresh_token",
        "clientId": "client_id",
        "clientSecret": "client_secret",
        "mlUserId": "user_id",
        "tokenExpiresAt": "expires_at_ms",
    },
    "shopee": {
        "accessToken": "access_token",
        "refreshToken": "refresh_token",
        "shopId": "shop_id",
        "tokenExpiresAt": "expires_at_ms",
        "partnerId": "partner_id",
        "partnerKey": "partner_key",
    },
    "amazon": {
        "refreshToken": "refresh_token",
        "sellerId": "seller_id",
        "marketplaceId": "marketplace_id",
        "lwaClientId": "lwa_app_id",
        "lwaClientSecret": "lwa_client_secret",
        "region": "region",
    },
    "bling": {
        "refreshToken": "refresh_token",
        "token": "access_token",
        "tokenExpiresAt": "expires_at_ms",
        "apiKey": "api_key",
    },
}


def _remap_creds(platform: str, src: dict) -> dict:
    """Translate legacy keys into davinci shape; convert ms timestamps to seconds."""
    mapping = KEY_MAP.get(platform, {})
    out: dict = {}
    for k, v in src.items():
        new_key = mapping.get(k, k)  # unmapped keys preserved as-is
        if new_key is None:
            continue  # explicitly dropped
        out[new_key] = v
    if "expires_at_ms" in out:
        ms = out.pop("expires_at_ms")
        try:
            out["expires_at"] = int(ms) // 1000
        except (TypeError, ValueError):
            pass
    return out


async def run(legacy_schema: str = "stocksync_legacy", target_schema: str = "davinci") -> dict[str, int]:
    settings = get_settings()
    raw_url = settings.database_url.replace("+asyncpg", "")

    counts = {"updated": 0, "skipped_no_user": 0, "skipped_no_target": 0, "platform_mapped": 0}
    legacy_to_new_platform = {
        "bling": "bling", "shopee": "shopee", "amazon": "amazon",
        "mercadolivre": "ml",  # legacy stored "mercadolivre", davinci stores "ml"
        "tiktok": None,
    }

    conn = await asyncpg.connect(raw_url)
    try:
        legacy = await conn.fetch(
            f'SELECT i.id, i."userId", i.platform, i.name, i."isActive", '
            f'i.credentials, i.status, i."errorMessage", '
            f'u.email, u."openId" '
            f"FROM {legacy_schema}.integrations i "
            f"JOIN {legacy_schema}.users u ON u.id = i.\"userId\" "
            f"ORDER BY i.id"
        )
        log.info("legacy integrations: %d", len(legacy))

        for r in legacy:
            new_platform = legacy_to_new_platform.get(r["platform"])
            if not new_platform:
                continue
            if new_platform != r["platform"]:
                counts["platform_mapped"] += 1

            # Find davinci user by email-based open_id
            email_open_id = f"email:{(r['email'] or '').strip().lower()}" if r["email"] else None
            user_uuid = None
            if email_open_id:
                user_uuid = await conn.fetchval(
                    f"SELECT id FROM {target_schema}.users WHERE open_id = $1",
                    email_open_id,
                )
            if not user_uuid:
                counts["skipped_no_user"] += 1
                continue

            # Match davinci integration by (user_id, platform, name)
            target_id = await conn.fetchval(
                f"SELECT id FROM {target_schema}.integrations "
                f"WHERE user_id = $1 AND platform = $2 AND name = $3",
                user_uuid, new_platform, r["name"],
            )
            if not target_id:
                counts["skipped_no_target"] += 1
                continue

            try:
                payload = json.loads(r["credentials"]) if r["credentials"] else {}
            except (TypeError, ValueError):
                payload = {"_legacy_raw": r["credentials"]}
            payload = _remap_creds(new_platform, payload)
            blob = encrypt_json(payload)
            new_status = "active" if r["isActive"] else "disconnected"

            await conn.execute(
                f"UPDATE {target_schema}.integrations "
                f"SET credentials = $2, status = $3, last_error = $4 "
                f"WHERE id = $1",
                target_id, blob, new_status, r["errorMessage"],
            )
            counts["updated"] += 1
    finally:
        await conn.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-schema", default="stocksync_legacy")
    parser.add_argument("--target-schema", default="davinci")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    counts = asyncio.run(run(args.legacy_schema, args.target_schema))
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
