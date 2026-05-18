"""Value translations between legacy `stocksync` and new `davinci` enums."""

from app.models.enums import (
    AlertSeverity,
    AlertType,
    IntegrationPlatform,
    LinkSyncStatus,
    ListingRequestStatus,
    ListingStatus,
    PricingPlatform,
)

LEGACY_TO_NEW_PLATFORM = {
    "bling": IntegrationPlatform.BLING.value,
    "mercadolivre": IntegrationPlatform.ML.value,
    "ml": IntegrationPlatform.ML.value,
    "shopee": IntegrationPlatform.SHOPEE.value,
    "amazon": IntegrationPlatform.AMAZON.value,
}

DROPPED_PLATFORMS = {"tiktok", "temu", "aliexpress", "magalu", "site"}

LEGACY_ALERT_TYPE = {
    "sync_error": AlertType.SYNC_FAILURE.value,
    "low_stock": AlertType.LOW_STOCK.value,
    "connection_lost": AlertType.TOKEN_EXPIRING.value,
    "stock_discrepancy": AlertType.REQUIRES_REVIEW.value,
    "sync_success": AlertType.DAILY_SYNC_COMPLETED.value,
    "stock_restock": AlertType.GENERIC.value,
}

LEGACY_ALERT_SEVERITY = {
    "info": AlertSeverity.INFO.value,
    "warning": AlertSeverity.WARNING.value,
    "error": AlertSeverity.ERROR.value,
    "critical": AlertSeverity.ERROR.value,
}

LEGACY_LISTING_STATUS = {
    "active": ListingStatus.ACTIVE.value,
    "paused": ListingStatus.PAUSED.value,
    "closed": ListingStatus.CLOSED.value,
    "under_review": ListingStatus.UNDER_REVIEW.value,
    "inactive": ListingStatus.INACTIVE.value,
}

LEGACY_LISTING_REQUEST_STATUS = {
    "pending": ListingRequestStatus.PENDING.value,
    "in_progress": ListingRequestStatus.IN_PROGRESS.value,
    "completed": ListingRequestStatus.COMPLETED.value,
    "rejected": ListingRequestStatus.REJECTED.value,
}

LEGACY_PRICING_PLATFORM = {
    "mercadolivre": PricingPlatform.ML.value,
    "shopee": PricingPlatform.SHOPEE.value,
    "amazon": PricingPlatform.AMAZON.value,
    "temu": PricingPlatform.TEMU.value,
    "tiktok": PricingPlatform.TIKTOK.value,
    "aliexpress": PricingPlatform.ALIEXPRESS.value,
    "magalu": PricingPlatform.MAGALU.value,
    "shein": PricingPlatform.SHEIN.value,
}

LINK_SYNC_DEFAULT = LinkSyncStatus.PENDING.value
