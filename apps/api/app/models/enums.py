from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class UserStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class Marketplace(StrEnum):
    ML = "ml"
    SHOPEE = "shopee"
    AMAZON = "amazon"
    ALIEXPRESS = "aliexpress"
    TEMU = "temu"
    TIKTOK = "tiktok"
    SHEIN = "shein"
    MAGALU = "magalu"
    SITE = "site"


MARKETPLACES: tuple[str, ...] = tuple(m.value for m in Marketplace)


class StoreStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    CLOSING = "closing"
    BANNED = "banned"
    PENDING = "pending"
    UNDER_REVIEW = "under_review"


class CadastroTipo(StrEnum):
    FONE = "fone"
    EMAIL = "email"
    DOMINIO = "dominio"


class CadastroStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    EXCLUDED = "excluded"


class IntegrationPlatform(StrEnum):
    BLING = "bling"
    ML = "ml"
    SHOPEE = "shopee"
    AMAZON = "amazon"


PLATFORMS: tuple[str, ...] = tuple(p.value for p in IntegrationPlatform)


class LinkSyncStatus(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    RETRYABLE = "retryable"
    FATAL = "fatal"
    PENDING = "pending"
    REQUIRES_REVIEW = "requires_review"


class SyncLogAction(StrEnum):
    REFRESH_BLING = "refresh_bling"
    UPDATE_STOCK = "update_stock"
    UPDATE_PRICE = "update_price"
    STORE_STATUS_CHANGE = "store_status_change"
    AUTO_LINK = "auto_link"
    TEST_CONNECTION = "test_connection"


class BackgroundJobType(StrEnum):
    SYNC_ALL = "sync_all"
    SYNC_PRODUCT = "sync_product"
    AUTO_LINK = "auto_link"
    AUDIT = "audit"
    SYNC_BLING_COSTS = "sync_bling_costs"
    IMPORT_LISTINGS = "import_listings"
    IMPORT_BLING_PRODUCTS = "import_bling_products"
    PUSH_PRICES_BATCH = "push_prices_batch"
    BACKFILL_ML_STOCK = "backfill_ml_stock"


class BackgroundJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
