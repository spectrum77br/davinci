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


_CHAVES_DE_SEGREDO = ("token", "secret", "senha", "password", "signature", "sig=")


def _sem_query_com_segredo(event, _hint=None):
    """Apaga query string de span/breadcrumb quando ela cheira a segredo.

    Vale pra QUALQUER host, não só a Meta: query string com token não deveria
    sair daqui de jeito nenhum, e a lista de integrações que gravam URL só
    cresce. Só apaga o que casa — o resto do trace continua útil pra
    diagnóstico.
    """

    def _limpa(d) -> None:
        if not isinstance(d, dict):
            return
        # `http.query` é só a query: se casar, some inteira.
        valor = d.get("http.query")
        if isinstance(valor, str) and any(c in valor.lower() for c in _CHAVES_DE_SEGREDO):
            d["http.query"] = "***"
        # Na URL completa, só a query é apagada — o caminho continua legível
        # (é o que diz QUAL chamada falhou). "debug_token" no path não é
        # segredo; `?input_token=…` é.
        for campo in ("url", "http.url"):
            valor = d.get(campo)
            if not isinstance(valor, str) or "?" not in valor:
                continue
            caminho, _, query = valor.partition("?")
            if any(c in query.lower() for c in _CHAVES_DE_SEGREDO):
                d[campo] = f"{caminho}?***"

    for span in event.get("spans") or []:
        _limpa(span.get("data") if isinstance(span, dict) else None)
    for migalha in (event.get("breadcrumbs") or {}).get("values") or []:
        _limpa(migalha.get("data") if isinstance(migalha, dict) else None)
    _limpa((event.get("contexts") or {}).get("trace", {}).get("data"))
    return event


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
            # Senha atual do certificado digital (trocar/excluir a senha pede
            # ela no PATCH de /companies/{id}/certificates/{cert}).
            "current_password",
            # Robô de postagem: o token da Meta entra pelo body do
            # POST /redes-sociais/{id}/conectar e publica na conta da marca —
            # vaza-lo é dar a conta. `token_enc` é o BYTEA cifrado, mas nem
            # cifrado precisa sair daqui.
            "access_token",
            "refresh_token",
            "token_enc",
            "input_token",
            "page_access_token",
            "page_token",
        ],
        recursive=True,
    )
    sentry_sdk.init(
        dsn=s.sentry_dsn,
        # O EventScrubber só mascara CHAVE de dicionário. A integração de
        # httpx (auto-habilitada) grava a QUERY STRING das chamadas de saída
        # em `span.data["http.query"]` — chave que nenhum denylist alcança, e
        # a Graph API da Meta exige `?input_token=<token>` no debug_token.
        # Sem estes dois ganchos, ~10% das conexões de conta mandariam o token
        # de publicação da marca em claro pro Sentry.
        before_send=_sem_query_com_segredo,
        before_send_transaction=_sem_query_com_segredo,
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
