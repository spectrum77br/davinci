"""Marketing module Telegram alerts.

Three signals the operator cares about across all platforms:

  1. Low credit / remaining budget — `notify_low_credit` (called by each
     sync orchestrator after the upsert when balance < R$50).
  2. Three consecutive sync failures — `record_sync_failure` increments
     `Integration.consecutive_errors`; at exactly the 3rd hit we fire one
     alert (no further spam until the next success resets the counter).
  3. ACOS materially above target — `notify_high_acos` fires when the
     account-level ACOS exceeds `target × 2.15` (≈15% on a 7% target).
     A 30-minute Telegram dedup avoids alerting on every cron tick.

All notifications are best-effort: `TelegramClient.safe_send` swallows
exceptions so an outage in Telegram doesn't break the sync.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import Integration
from app.services.telegram import TelegramClient

logger = structlog.get_logger()

# Telegram dedup — same alert key won't be re-sent within this window.
# Process-local; survives until the worker restarts. Cron runs every 30
# min, so a 1800s TTL effectively means "one alert per cron tick".
_DEDUP_WINDOW_SECONDS = 1800
_FAILURE_ALERT_THRESHOLD = 3
_HIGH_ACOS_MULTIPLIER = 2.15  # 7% target → 15% ACOS triggers alert

# (integration_id, alert_key) → last_sent_ts.
_dedup: dict[tuple[UUID, str], datetime] = {}


def _should_send(integration_id: UUID, key: str) -> bool:
    now = datetime.now(UTC)
    last = _dedup.get((integration_id, key))
    if last and (now - last) < timedelta(seconds=_DEDUP_WINDOW_SECONDS):
        return False
    _dedup[(integration_id, key)] = now
    return True


def _platform_label(platform: str) -> str:
    return {"shopee": "Shopee", "ml": "Mercado Livre", "mercadolivre": "Mercado Livre", "amazon": "Amazon"}.get(
        platform, platform.title()
    )


async def notify_low_credit(
    integration: Integration, *, credit: float, threshold: float = 50.0
) -> None:
    """Fire when balance/remaining-budget falls under the threshold.
    Per-integration dedup so the cron doesn't re-alert every 30 min."""
    if credit >= threshold:
        return
    if not _should_send(integration.id, "low_credit"):
        return
    label = _platform_label(integration.platform.value if hasattr(integration.platform, "value") else str(integration.platform))
    await TelegramClient().safe_send(
        f"⚠️ <b>{label} Ads</b>: crédito baixo em <b>{integration.name}</b> — saldo R$ {credit:.2f}"
    )


async def notify_high_acos(
    integration: Integration, acos: float, acos_target: float
) -> None:
    """Fire when the account-level ACOS exceeds target × multiplier. We
    use a multiplier (not an absolute threshold) so shops with different
    acceptable ACOS get appropriate signals."""
    if acos_target <= 0:
        return
    threshold = acos_target * _HIGH_ACOS_MULTIPLIER
    if acos < threshold:
        return
    if not _should_send(integration.id, "high_acos"):
        return
    label = _platform_label(integration.platform.value if hasattr(integration.platform, "value") else str(integration.platform))
    await TelegramClient().safe_send(
        f"🚨 <b>{label} Ads</b>: ACOS alto em <b>{integration.name}</b> — "
        f"{acos:.1f}% (alvo {acos_target:.1f}%)"
    )


async def record_sync_failure(
    session: AsyncSession,
    integration: Integration,
    *,
    code: str,
    message: str,
) -> None:
    """Bump the consecutive-failure counter. At exactly the threshold we
    fire one Telegram alert and let the counter keep climbing — the next
    alert won't fire until the counter resets to 0 (a successful sync)
    and climbs back to the threshold. This avoids spam while still
    re-alerting after recovery + relapse."""
    integration.consecutive_errors = (integration.consecutive_errors or 0) + 1
    if integration.consecutive_errors == _FAILURE_ALERT_THRESHOLD:
        if _should_send(integration.id, "consecutive_failures"):
            label = _platform_label(
                integration.platform.value if hasattr(integration.platform, "value") else str(integration.platform)
            )
            await TelegramClient().safe_send(
                f"❌ <b>{label} Ads</b>: 3 falhas seguidas em <b>{integration.name}</b> — "
                f"último erro: <code>{code}: {message[:200]}</code>"
            )


async def record_sync_success(
    session: AsyncSession, integration: Integration
) -> None:
    """Reset the failure counter on a clean run so the next failure
    streak starts from zero."""
    if integration.consecutive_errors:
        integration.consecutive_errors = 0
