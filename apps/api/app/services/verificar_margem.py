"""Refresh helpers for davinci.verificar_margem snapshot table.

The table mirrors vw_conciliacao_margens_marketplace. Reads (margens page)
hit the table; writes happen via:

  - worker cron `verificar_margem_snapshot` every 30 min (insert new only,
    fallback for cases not covered by hooks)
  - this module's `refresh_silent`, called from the services that mutate
    upstream tables (bling_orders, marketplace financials) and from the
    margens router after user-facing PATCHes.

Every helper finishes with `session.commit()` so callers don't have to
worry about flushing pending writes — the commit also exposes any
in-flight upstream changes to the next reader.
"""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


async def rebuild_all(session: AsyncSession) -> int:
    """Full rebuild dentro da janela de 20d (escopo da view base).

    Apaga somente as linhas cujo bling_order_item_id aparece na view base
    (janela de 20d) e re-insere a partir dela. Linhas de pedidos antigos
    inseridos por outros caminhos — tipicamente refunds manuais — ficam
    preservadas para que o usuario consiga aprovar o saldo final na
    pagina de margens mesmo apos um rebuild manual.

    MVCC keeps concurrent SELECT readers on the pre-commit snapshot,
    so the ~17s rebuild does not block the margens page.
    """
    await session.execute(
        text(
            "DELETE FROM davinci.verificar_margem v "
            "WHERE v.bling_order_item_id IN ("
            "  SELECT bling_order_item_id "
            "  FROM davinci.vw_conciliacao_margens_marketplace"
            ")"
        )
    )
    result = await session.execute(
        text(
            "INSERT INTO davinci.verificar_margem "
            "SELECT * FROM davinci.vw_conciliacao_margens_marketplace"
        )
    )
    await session.commit()
    return result.rowcount or 0


async def refresh_for_pedido(session: AsyncSession, pedido_bling: str) -> int:
    """Targeted refresh of one order by its Bling `numero`.

    Le da view "_all" (sem janela de 20d) para que pedidos antigos
    referenciados manualmente em refunds tambem apareçam em
    verificar_margem e fiquem disponiveis na pagina de margens.
    """
    await session.execute(
        text("DELETE FROM davinci.verificar_margem WHERE pedido_bling = :p"),
        {"p": pedido_bling},
    )
    result = await session.execute(
        text(
            "INSERT INTO davinci.verificar_margem "
            "SELECT * FROM davinci.vw_conciliacao_margens_marketplace_all "
            "WHERE pedido_bling = :p"
        ),
        {"p": pedido_bling},
    )
    await session.commit()
    return result.rowcount or 0


async def refresh_for_bling_id(session: AsyncSession, bling_id: int) -> int:
    """Targeted refresh of one order by its internal Bling id.

    Le da view "_all" (sem janela de 20d), mesmo motivo de refresh_for_pedido.
    """
    await session.execute(
        text("DELETE FROM davinci.verificar_margem WHERE bling_id = :b"),
        {"b": bling_id},
    )
    result = await session.execute(
        text(
            "INSERT INTO davinci.verificar_margem "
            "SELECT * FROM davinci.vw_conciliacao_margens_marketplace_all "
            "WHERE bling_id = :b"
        ),
        {"b": bling_id},
    )
    await session.commit()
    return result.rowcount or 0


async def refresh_silent(
    session: AsyncSession,
    *,
    pedido_bling: str | None = None,
    bling_id: int | None = None,
) -> None:
    """Best-effort wrapper for use in non-router code paths.

    Picks the most specific filter available (pedido_bling > bling_id >
    full rebuild). Swallows exceptions — a refresh failure must never
    break the upstream service that triggered it.
    """
    try:
        if pedido_bling is not None:
            await refresh_for_pedido(session, pedido_bling)
        elif bling_id is not None:
            await refresh_for_bling_id(session, bling_id)
        else:
            await rebuild_all(session)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "verificar_margem_refresh_failed",
            pedido_bling=pedido_bling,
            bling_id=bling_id,
            error=str(e)[:200],
        )
