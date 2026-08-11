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
    # Etiqueta passada pro Postgres via asyncpg `server_settings`. Cada
    # processo (api / worker / worker_ui / worker_marketplace) recebe um
    # nome diferente via docker-compose (env APP_NAME), alimentando
    # audit triggers (audit_em_andamento_data_fn) e pg_stat_activity
    # sem precisar rastrear PID. Default mantém dev local sem env.
    app_name: str = "davinci-api"

    redis_url: str = "redis://redis:6379/0"
    arq_redis_url: str = "redis://redis:6379/1"

    uploads_dir: str = "/data/uploads"

    # Sidecar MEGAcmd (fotos dos produtos da Tabela de Preços).
    # mega_fotos_root: pasta da conta MEGA onde ficam as pastas de fotos
    # por produto — ajustar via env quando a estrutura real for conhecida.
    mega_sidecar_url: str = "http://megacmd:9000"
    mega_sidecar_token: str = ""
    mega_fotos_root: str = "/"

    jwt_secret: str = "dev-secret-change-me"
    jwt_ttl_seconds: int = 7 * 24 * 3600
    # Senha extra que protege a página /financeiro/valuation, exigida
    # depois do login independentemente do user. Em prod vem da env
    # VALUATION_PASSWORD. TTL curto (15min) — janela apertada pra não
    # deixar o navegador desbloqueado por horas sem atenção.
    valuation_password: str = "924005"
    valuation_unlock_ttl_seconds: int = 15 * 60
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

    # 17track — rastreamento físico real dos Correios (`...BR`) na Logística.
    # `logi_17track_token`: API token da conta 17track (register/gettrackinfo).
    # `logi_17track_webhook_secret`: segmento secreto no path do webhook público
    # (o 17track não assina o push, então o segredo no URL é o guard).
    logi_17track_token: str = ""
    logi_17track_webhook_secret: str = ""

    # Melhor Envio — confere o frete da impressão tipo "próprio" (só Amazon).
    # `melhor_envio_token`: Bearer token OAuth2 da conta ME (calcula frete).
    # `melhor_envio_sandbox`: usa o ambiente de testes do ME quando True.
    # Inerte vazio — sem token o cálculo levanta MelhorEnvioConfigError.
    melhor_envio_token: str = ""
    melhor_envio_sandbox: bool = False
    # CEP de ORIGEM da postagem (remetente) usado no confere-frete automático.
    # Não fica no cadastro de empresa/loja — vem do .env. Inerte vazio: o
    # prefill do confere-frete AUTO levanta 400 nf_origem_cep_missing.
    nf_origem_cep: str = ""

    shopee_partner_id: str = ""
    shopee_partner_key: str = ""
    shopee_use_sandbox: bool = False
    # Public callback URL registered for the Shopee OAuth "login" flow. The
    # per-request `state` is appended as a PATH segment (not a query param) —
    # Shopee appends `?code=&shop_id=` to the redirect and does not reliably
    # preserve pre-existing query strings, so the state must live in the path.
    shopee_redirect_uri: str = ""

    ml_client_id: str = ""
    ml_client_secret: str = ""
    ml_redirect_uri: str = ""

    # Magalu (Magazine Luiza) — app OAuth único/compartilhado no ID Magalu
    # ("DavinciERP"). Diferente do ML, TODAS as integrações usam o MESMO
    # client_id/secret (nível env), e cada seller autoriza o próprio tenant no
    # login (choose_tenants=true). O client_secret vem SÓ da env em prod
    # (MAGALU_CLIENT_SECRET) — nunca commitado. redirect_uri tem que bater
    # exatamente com o registrado no app.
    magalu_client_id: str = ""
    magalu_client_secret: str = ""
    magalu_redirect_uri: str = ""
    # Proxy de saída EXCLUSIVO da Magalu (formato "http://user:senha@host:porta").
    # A Azion (edge da Magalu) bloqueia IPs de datacenter/fora do BR: o servidor
    # de produção (Hetzner/DE) leva 403 em TODO id.magalu.com e api.magalu.com.
    # Setando isto, o tráfego da Magalu (OAuth + API) — e SÓ ele — sai por um
    # proxy BR; as demais integrações continuam saindo direto. Vazia = conexão
    # direta (sem proxy), o comportamento padrão.
    magalu_proxy_url: str = ""

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

    # Threema Gateway (Basic mode) — notifica as pessoas de um caso de
    # logística. `threema_gateway_id`: o *ID de 8 chars (ex. "*3MAGW01").
    # `threema_gateway_secret`: API secret do painel. `threema_recipients`:
    # Threema IDs destino separados por vírgula (8 chars cada). Tudo inerte
    # vazio — sem config o envio levanta threema_nao_configurado.
    threema_gateway_id: str = ""
    threema_gateway_secret: str = ""
    threema_recipients: str = ""
    # Nomes dos destinatários pro seletor (`ID:Nome` separados por vírgula, ex.
    # "7KMPCBS5:Londres,M5TT27JA:Cairo,444UXUXN:Churchill"). ID sem nome cai no
    # próprio ID.
    threema_recipient_names: str = ""

    # Dev: enables /api/dev/mock-login (gated by ENV=development too).
    dev_mock_login: bool = False

    # Marketing module is in active development; off in prod until the
    # tables (`alembic upgrade head` after 0065_marketing_module) are
    # explicitly applied. Locally we enable it via ENABLE_MARKETING=true.
    enable_marketing: bool = False

    # Marketing AGENT NODE — the single dedicated machine allowed to talk
    # to Shopee/ML Ads (works around the per-partner rate-limit). ONLY when
    # this is true does the worker REGISTER the marketing command-consumer
    # and schedule-reconciler crons. The central server keeps it absent/0 so
    # it never executes ad actions (the registration itself is gated, not
    # just the cron body). Flip to true via env on the dedicated node.
    marketing_agent_node: bool = False

    # Machine-to-machine token the LOCAL external executor (marionete) sends on
    # the /marketing/agent/* endpoints (lease/result/heartbeat). Empty = those
    # endpoints stay CLOSED (401). This is how Shopee ad actions actually run:
    # the official Shopee Ads API is blocked by the partner quota, so DaVinci is
    # only the control plane (UI + BRT schedule + outbox) and the local Mac
    # drives Shopee via AdsPower, polling this token-gated queue over NAT. Set
    # via env MARKETING_AGENT_TOKEN on the server AND the same value in the
    # marionete .env to turn the integration on.
    marketing_agent_token: str = ""

    # Token M2M do executor de IMPORTAÇÃO DE NF (marionete AdsPower da Fase
    # 3a-4). Guarda os /nf-cadastro/agent/* (lease/result). Vazio = endpoints
    # FECHADOS (401). O executor local abre o AdsPower do faturador, loga no
    # Bling destino e importa a planilha avulsa. Set via NF_AGENT_TOKEN no
    # servidor E o mesmo valor no .env do executor pra ligar a integração.
    nf_agent_token: str = ""

    # Auto-enfileirador de NF (sweep): a cada tick varre pedidos Shopee/TikTok
    # "Em aberto" (situacao=6) de loja com faturador atribuído, confere o
    # estoque (saldo negativo → Aguardando Cancelamento) e enfileira a
    # importação avulsa sozinho — mesma cadeia do botão "Enfileirar" do
    # painel Faturamento. Desligado por default; ligar via
    # NF_AUTO_ENFILEIRAR=true no .env.
    nf_auto_enfileirar: bool = False

    # Inclui o Mercado Livre no sweep automático. Fica desligado até a
    # marionete de emissão no Bling destino (emitir_nf_bling) estar
    # calibrada — senão o import cria a venda mas ninguém emite a NF.
    # Ligar via NF_AUTO_ML=true no .env.
    nf_auto_ml: bool = False

    # Threema IDs (vírgula) avisados quando o sweep move um pedido pra
    # Aguardando Cancelamento por estoque negativo. Vazio = aviso desligado
    # (o sweep segue funcionando normal). Set via
    # NF_SEM_ESTOQUE_THREEMA_RECIPIENTS no .env.
    nf_sem_estoque_threema_recipients: str = ""

    # Safety-net cron que re-sincroniza pedidos suspeitos de stale com o
    # Bling (webhooks perdidos). Desligável via ENABLE_BLING_ORDERS_SAFETY_NET=false.
    enable_bling_orders_safety_net: bool = True

    # Varredura horária por período: lista pedidos alterados nas últimas 2h
    # (dataAlteracao do Bling) e re-ingere — recupera QUALQUER webhook
    # perdido, situação-agnóstica. Desligável via ENABLE_BLING_ORDERS_PERIOD_SYNC=false.
    enable_bling_orders_period_sync: bool = True

    # Varredura DIÁRIA por data de emissão: lista todos os pedidos do dia no
    # Bling e ingere os AUSENTES do banco (insert) ou com situação divergente
    # (update) — única rede que recupera pedido nunca-ingerido. Pula os já
    # presentes e inalterados. Desligável via ENABLE_BLING_ORDERS_DAILY_BACKFILL=false.
    enable_bling_orders_daily_backfill: bool = True

    # Sweep que re-dirige o ingest de pedidos que esgotou os retries do arq.
    # Cada webhook de pedido grava um BackgroundJob durável (type=ingest_bling_order);
    # ao falhar em definitivo ele fica FAILED e este cron re-enfileira (já tem o
    # bling_id, sem re-listar o Bling) com teto de tentativas — recuperação em
    # minutos, não no backfill diário. Desligável via ENABLE_INGEST_ORDERS_RETRY_SWEEP=false.
    enable_ingest_orders_retry_sweep: bool = True

    @property
    def is_prod(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
