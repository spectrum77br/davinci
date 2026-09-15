"""Sentry init — Phase 13.

Idempotent. Skips if `sentry_dsn` is empty or the SDK isn't installed; the SDK
is an optional runtime dep (added to pyproject.toml `dependencies` but pinned
loosely so dev installs without a DSN don't fail).
"""

from __future__ import annotations

import structlog

from app.config import get_settings

logger = structlog.get_logger()

_initialized = False


def init_sentry(*, component: str) -> bool:
    """Returns True if Sentry was initialized in this call."""
    global _initialized
    if _initialized:
        return False
    s = get_settings()
    if not s.sentry_dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber
    except ImportError:
        logger.warning("sentry_sdk_missing_skipping_init", component=component)
        return False
    sample_rate = 0.1 if s.is_prod else 0.0
    # Senhas dos cadastros (Marcas, Redes Sociais, NF Faturador, Lojas) viajam
    # no body como `senha`/`password`. O denylist padrão do SDK só mascara
    # password/passwd — sem isto um 500 num POST/PATCH mandava a senha em
    # claro pro Sentry (body do request e variáveis locais).
    scrubber = EventScrubber(
        # Chave EXATA (não substring): cada campo novo com segredo entra aqui.
        denylist=[
            *DEFAULT_DENYLIST,
            "senha",
            "senha_enc",
            "sac_senha",
            "sac_senha_enc",
            "password_enc",
            "pwd",
        ],
        recursive=True,
    )
    sentry_sdk.init(
        dsn=s.sentry_dsn,
        environment=s.env,
        traces_sample_rate=sample_rate,
        send_default_pii=False,
        event_scrubber=scrubber,
        # O scrubber só mascara CHAVES de dict; um body pydantic/dataclass com
        # senha entra no evento como repr() dentro de uma string e passa
        # ileso. Sem variáveis locais no evento não existe esse caminho.
        include_local_variables=False,
        integrations=[AsyncioIntegration()],
        release=f"davinci-api@{s.env}",
    )
    sentry_sdk.set_tag("component", component)
    _initialized = True
    logger.info("sentry_initialized", component=component, env=s.env)
    return True
