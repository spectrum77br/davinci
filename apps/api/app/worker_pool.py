from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import get_settings

_settings = get_settings()
_pool: ArqRedis | None = None
_ui_pool: ArqRedis | None = None
_marketplace_pool: ArqRedis | None = None

# Fila default ARQ — concentra ~48 jobs (webhooks marketplace, syncs,
# crons gerais). Alto volume, latência ok.
ARQ_DEFAULT_QUEUE = "arq:queue"

# Fila prioritária — só os 2 jobs disparados por click do operador
# (criar kit, enviar produto pro Bling). Worker dedicado processa em
# <30s independente do backlog da fila default. Sem essa separação,
# jobs UI ficavam 17+ min atrás dos webhooks.
ARQ_UI_QUEUE = "davinci_ui"

# Fila de crons de marketplace status — `check_marketplace_shipped_orders`
# precisa rodar a cada 5 min cravados (promove pedidos pra situação 15
# quando marketplace confirma envio). Antes competia com webhooks na
# default e atrasava 15-20 min em pico. Worker dedicado garante o tick.
ARQ_MARKETPLACE_QUEUE = "davinci_marketplace"


async def get_arq_pool() -> ArqRedis:
    """Pool da fila default. Use pra jobs cron gerais, webhooks, syncs."""
    global _pool
    if _pool is None:
        _pool = await create_pool(
            RedisSettings.from_dsn(_settings.arq_redis_url),
            default_queue_name=ARQ_DEFAULT_QUEUE,
        )
    return _pool


async def get_arq_ui_pool() -> ArqRedis:
    """Pool da fila UI (alta prioridade). Use pra jobs disparados por
    click do operador: `sync_import_product_to_bling_job` e
    `create_bling_kit_for_mark_job`. Worker dedicado roda em paralelo
    ao default — webhooks não bloqueiam UI."""
    global _ui_pool
    if _ui_pool is None:
        _ui_pool = await create_pool(
            RedisSettings.from_dsn(_settings.arq_redis_url),
            default_queue_name=ARQ_UI_QUEUE,
        )
    return _ui_pool


async def get_arq_marketplace_pool() -> ArqRedis:
    """Pool da fila de crons marketplace. Use só pra enfileirar
    manualmente o `check_marketplace_shipped_orders` (debug/ops). O cron
    automático fica no WorkerSettingsMarketplace."""
    global _marketplace_pool
    if _marketplace_pool is None:
        _marketplace_pool = await create_pool(
            RedisSettings.from_dsn(_settings.arq_redis_url),
            default_queue_name=ARQ_MARKETPLACE_QUEUE,
        )
    return _marketplace_pool


async def close_arq_pool() -> None:
    global _pool, _ui_pool, _marketplace_pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
    if _ui_pool is not None:
        await _ui_pool.aclose()
        _ui_pool = None
    if _marketplace_pool is not None:
        await _marketplace_pool.aclose()
        _marketplace_pool = None
