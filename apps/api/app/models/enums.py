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
    SERVIDOR = "servidor"


class CadastroStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    EXCLUDED = "excluded"


class IntegrationPlatform(StrEnum):
    BLING = "bling"
    ML = "ml"
    SHOPEE = "shopee"
    AMAZON = "amazon"
    TIKTOK = "tiktok"
    TEMU = "temu"


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
    WEBHOOK_UNMATCHED = "webhook_unmatched"


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
    REFRESH_BLING_STOCK = "refresh_bling_stock"


class BackgroundJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ListingStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"
    UNDER_REVIEW = "under_review"
    INACTIVE = "inactive"


class ListingRequestStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


class AlertType(StrEnum):
    LOW_STOCK = "low_stock"
    SYNC_FAILURE = "sync_failure"
    LISTING_BANNED = "listing_banned"
    REQUIRES_REVIEW = "requires_review"
    DAILY_SYNC_COMPLETED = "daily_sync_completed"
    TOKEN_EXPIRING = "token_expiring"
    GENERIC = "generic"


class Department(StrEnum):
    CELULAR = "celular"
    MALA = "mala"
    ELETRO = "eletro"
    CATALOGO = "catalogo"


class PricingPlatform(StrEnum):
    ML = "mercadolivre"
    SHOPEE = "shopee"
    TEMU = "temu"
    AMAZON = "amazon"
    ALIEXPRESS = "aliexpress"
    TIKTOK = "tiktok"
    MAGALU = "magalu"


class CellStatus(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"
    LOCKED = "locked"
    DISABLED = "disabled"


class AuditRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AuditFindingStatus(StrEnum):
    OK = "ok"
    PRICE_MISMATCH = "price_mismatch"
    MISSING = "missing"
    PAUSED = "paused"
    EXTRA = "extra"
