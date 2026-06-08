from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env at monorepo root (3 levels up from this file: app/ → api/ → apps/ → root)
_ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
_ENV_FILE = _ROOT_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    env: str = "development"
    app_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"
    port: int = 8000

    database_url: str
    database_schema: str = "davinci"

    redis_url: str = "redis://redis:6379/0"
    arq_redis_url: str = "redis://redis:6379/1"

    uploads_dir: str = "/data/uploads"

    jwt_secret: str = "dev-secret-change-me"
    jwt_ttl_seconds: int = 7 * 24 * 3600
    credentials_key: str = "dev-credentials-key-change-me"
    cookie_name: str = "davinci_session"
    cookie_domain: str = ""
    owner_open_id: str = "email:spectrum77@tuta.com"

    otp_code_ttl_ms: int = 600_000
    otp_max_attempts: int = 5
    otp_rate_per_ip: int = 3
    otp_rate_per_email: int = 3
    otp_prefix_len: int = 4
    otp_code_len: int = 8

    # Login por senha. min_length é o piso aceito ao DEFINIR uma senha
    # (admin). Os rate limits do login (por hora) protegem contra
    # brute-force — mais folgados que o OTP porque não disparam e-mail.
    password_min_length: int = 8
    login_rate_per_ip: int = 20
    login_rate_per_email: int = 8

    log_level: str = "info"
    sentry_dsn: str = ""

    turnstile_secret_key: str = ""

    mailjet_api_key: str = ""
    mailjet_secret_key: str = ""
    email_from: str = "DaVinci <no-reply@hadken.com>"
    email_from_name: str = "DaVinci"

    bling_client_id: str = ""
    bling_client_secret: str = ""
    bling_redirect_uri: str = ""
    bling_webhook_secret: str = ""
    bling_basic_auth: str = ""
    # Fornecedor padrão usado pra ancorar precoCusto no POST /produtos.
    # Bling V3 descarta precoCusto silenciosamente quando fornecedor.id
    # está vazio — sempre que o sistema cria produto com custo, vincula
    # ao contato com esse nome (resolvido via /contatos?pesquisa=).
    bling_default_supplier_name: str = "000000111111111ll"

    shopee_partner_id: str = ""
    shopee_partner_key: str = ""
    shopee_use_sandbox: bool = False

    ml_client_id: str = ""
    ml_client_secret: str = ""
    ml_redirect_uri: str = ""

    amazon_client_id: str = ""
    amazon_client_secret: str = ""
    amazon_refresh_token: str = ""
    amazon_marketplace_id: str = ""

    # Amazon Advertising API — separate OAuth app from SP-API. Leave blank
    # to disable; sync orchestrator returns {status: skipped, reason:
    # missing_amazon_ads_credentials} so the rest of the marketing pull
    # keeps working. `amazon_ads_profile_id` is per-region (one per
    # marketplace); look it up once via GET /v2/profiles.
    amazon_ads_client_id: str = ""
    amazon_ads_client_secret: str = ""
    amazon_ads_refresh_token: str = ""
    amazon_ads_profile_id: str = ""
    amazon_ads_region: str = "na"  # na | eu | fe — SA shares the NA cluster

    # Shopee Ads — separate feature flag from `enable_marketing` so the
    # operator can pause JUST Shopee (e.g. while a quota issue with
    # Shopee Open Platform is open) without affecting ML/Amazon sync.
    # Default off — flip to true via prod .env once the partner_id
    # rate-limit is confirmed cleared.
    enable_shopee_ads: bool = False
    # Pause between successive Ads API calls within a single shop sync.
    # 30s is conservative — Shopee's per-partner Ads throttle is
    # empirically <1 call/min, so spacing balance/daily/campaign calls
    # 30s apart keeps a single-shop sync under the ceiling.
    shopee_ads_delay_between_calls_s: int = 30
    # Global cooldown after the first `ads_rate_limit_total_api` hit.
    # During cooldown, the round-robin cron skips Shopee entirely.
    # Default 3600 (1h) — operator can shorten via env.
    shopee_ads_cooldown_on_rate_limit_s: int = 3600

    tiktok_app_key: str = ""
    tiktok_app_secret: str = ""
    tiktok_shop_cipher: str = ""
    tiktok_access_token: str = ""
    tiktok_refresh_token: str = ""

    temu_app_key: str = ""
    temu_app_secret: str = ""
    temu_access_token: str = ""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Dev: enables /api/dev/mock-login (gated by ENV=development too).
    dev_mock_login: bool = False

    # Marketing module is in active development; off in prod until the
    # tables (`alembic upgrade head` after 0065_marketing_module) are
    # explicitly applied. Locally we enable it via ENABLE_MARKETING=true.
    enable_marketing: bool = False

    # Safety-net cron que re-sincroniza pedidos suspeitos de stale com o
    # Bling (webhooks perdidos). Desligável via ENABLE_BLING_ORDERS_SAFETY_NET=false.
    enable_bling_orders_safety_net: bool = True

    # Varredura horária por período: lista pedidos alterados nas últimas 2h
    # (dataAlteracao do Bling) e re-ingere — recupera QUALQUER webhook
    # perdido, situação-agnóstica. Desligável via ENABLE_BLING_ORDERS_PERIOD_SYNC=false.
    enable_bling_orders_period_sync: bool = True

    @property
    def is_prod(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
