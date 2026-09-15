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
    SHEIN = "shein"
    MAGALU = "magalu"


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
    AUTO_IMPORT_LINK = "auto_import_link"
    EXPORT_NOTAS_FISCAIS = "export_notas_fiscais"
    # Registro durável de um ingest de pedido disparado por webhook. Antes o
    # webhook de pedido só enfileirava o arq (sem BackgroundJob), então uma
    # falha terminal do ingest sumia em silêncio — invisível ao
    # failed_jobs_alert_scan e sem re-drive. Com este tipo o pedido ganha o
    # mesmo tratamento durável do webhook de produto (ver ingest_orders_retry_sweep).
    INGEST_BLING_ORDER = "ingest_bling_order"


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
    TAREFA_ATRIBUIDA = "tarefa_atribuida"


class Department(StrEnum):
    CELULAR = "celular"
    MALA = "mala"
    ELETRO = "eletro"
    CATALOGO = "catalogo"
    SHEIN = "shein"


class PricingPlatform(StrEnum):
    ML = "mercadolivre"
    SHOPEE = "shopee"
    TEMU = "temu"
    AMAZON = "amazon"
    ALIEXPRESS = "aliexpress"
    TIKTOK = "tiktok"
    MAGALU = "magalu"
    SHEIN = "shein"


class CellStatus(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"
    LOCKED = "locked"
    DISABLED = "disabled"
    NA = "NA"
    SV = "SV"
    # Transient states written by push: error after API failure, no_link
    # when the resolver found 0 product_links. UI exposes a "NA"/"SV"
    # button when the cell is in one of these states.
    ERROR = "error"
    NO_LINK = "no_link"


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


class MarcaInpiStatus(StrEnum):
    """Situação do registro da marca no INPI (Cadastros › Marcas, 15/09/2026).

    Vocabulário do processo: sem pedido → depositado/em exame (aguardando) →
    registrado (vale 10 anos, renovável) | indeferido; registro vencido =
    expirado. A planilha do Eduardo só usava 'ok' e 'aguardando'
    ('ok' → registrado na importação). Coluna String no banco (sem PG enum):
    valor novo entra sem migration.
    """

    NAO_REGISTRADO = "nao_registrado"
    AGUARDANDO = "aguardando"
    REGISTRADO = "registrado"
    INDEFERIDO = "indeferido"
    EXPIRADO = "expirado"


MARCA_INPI_STATUS: tuple[str, ...] = tuple(s.value for s in MarcaInpiStatus)


class RedeSocialPlataforma(StrEnum):
    """Plataformas da aba Redes Sociais (Cadastros) — as 5 da planilha do
    Eduardo, na ordem dela ("seguir bem a planilha", 15/09/2026). Chave
    estável por plataforma — a futura auto-postagem de vídeos escolhe o
    cliente de API por este valor. Coluna String no banco (sem PG enum):
    plataforma nova é só acrescentar aqui e no lib/redesSociais.ts do web."""

    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    TWITTER = "twitter"
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"


REDES_SOCIAIS_PLATAFORMAS: tuple[str, ...] = tuple(p.value for p in RedeSocialPlataforma)


class VerificacaoStatus(StrEnum):
    """Andamento do selo de verificado (Meta Verified) de uma conta social ou
    do WhatsApp da marca. O DaVinci só REGISTRA o andamento — o pedido em si
    é feito no app da plataforma, pela equipe (2FA, documento, assinatura)."""

    NAO_SOLICITADO = "nao_solicitado"
    EM_ANDAMENTO = "em_andamento"
    VERIFICADO = "verificado"
    RECUSADO = "recusado"


VERIFICACAO_STATUS: tuple[str, ...] = tuple(s.value for s in VerificacaoStatus)


class EmailContexto(StrEnum):
    """Canal/contexto de um padrão de e-mail da marca (Cadastros › E-mails):
    "o padrão pro SAC, o padrão pro Mercado Livre…" (Eduardo, 15/09/2026).
    Os marketplaces usam os MESMOS valores do enum Marketplace ('ml' etc.),
    pra um chamado com `plataforma='ml'` achar o padrão pela chave."""

    SAC = "sac"
    ML = "ml"
    SHOPEE = "shopee"
    AMAZON = "amazon"
    ALIEXPRESS = "aliexpress"
    TEMU = "temu"
    TIKTOK = "tiktok"
    SHEIN = "shein"
    MAGALU = "magalu"
    SITE = "site"
    GERAL = "geral"


EMAIL_CONTEXTOS: tuple[str, ...] = tuple(c.value for c in EmailContexto)
