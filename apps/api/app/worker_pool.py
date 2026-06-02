from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import get_settings

_settings = get_settings()
_pool: ArqRedis | None = None
_ui_pool: ArqRedis | None = None

# Fila default ARQ — concentra ~49 jobs (webhooks marketplace, syncs,
# crons). Alto volume, latência ok.
ARQ_DEFAULT_QUEUE = "arq:queue"

# Fila prioritária — só os 2 jobs disparados por click do operador
# (criar kit, enviar produto pro Bling). Worker dedicado processa em
# <30s independente do backlog da fila default. Sem essa separação,
# jobs UI ficavam 17+ min atrás dos webhooks.
ARQ_UI_QUEUE = "davinci_ui"


async def get_arq_pool() -> ArqRedis:
    """Pool da fila default. Use pra jobs cron, webhooks, syncs."""
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


async def close_arq_pool() -> None:
    global _pool, _ui_pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
    if _ui_pool is not None:
        await _ui_pool.aclose()
        _ui_pool = None
