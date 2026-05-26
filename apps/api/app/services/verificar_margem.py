# ruff: noqa: S608
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

from app.config import get_settings

logger = structlog.get_logger()
SCHEMA = get_settings().database_schema


def _ident(name: str) -> str:
    return f'"{name.replace(chr(34), chr(34) + chr(34))}"'


def qualified_table(name: str) -> str:
    return f"{_ident(SCHEMA)}.{_ident(name)}"


SNAPSHOT_TABLE = qualified_table("verificar_margem")
VIEW_TABLE = qualified_table("vw_conciliacao_margens_marketplace")
VIEW_ALL_TABLE = qualified_table("vw_conciliacao_margens_marketplace_all")


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
            f"DELETE FROM {SNAPSHOT_TABLE} v "
            "WHERE v.bling_order_item_id IN ("
            "  SELECT bling_order_item_id "
            f"  FROM {VIEW_TABLE}"
            ")"
        )
    )
    result = await session.execute(
        text(
            f"INSERT INTO {SNAPSHOT_TABLE} "
            f"SELECT * FROM {VIEW_TABLE}"
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
        text(f"DELETE FROM {SNAPSHOT_TABLE} WHERE pedido_bling = :p"),
        {"p": pedido_bling},
    )
    result = await session.execute(
        text(
            f"INSERT INTO {SNAPSHOT_TABLE} "
            f"SELECT * FROM {VIEW_ALL_TABLE} "
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
        text(f"DELETE FROM {SNAPSHOT_TABLE} WHERE bling_id = :b"),
        {"b": bling_id},
    )
    result = await session.execute(
        text(
            f"INSERT INTO {SNAPSHOT_TABLE} "
            f"SELECT * FROM {VIEW_ALL_TABLE} "
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


async def patch_status_for_pedido(
    session: AsyncSession,
    *,
    pedido_bling: str,
    status: str,
    aprovado_por: str | None = None,
    verificado: bool | None = None,
) -> int:
    """Patch user-facing approval status in the snapshot without rebuilding it."""
    result = await session.execute(
        text(
            f"""
            UPDATE {SNAPSHOT_TABLE}
            SET bling_status_margem = :status,
                aprovado_por = COALESCE(CAST(:aprovado_por AS uuid), aprovado_por),
                verificado = COALESCE(CAST(:verificado AS boolean), verificado)
            WHERE pedido_bling = :pedido_bling
            """
        ),
        {
            "pedido_bling": pedido_bling,
            "status": status,
            "aprovado_por": aprovado_por,
            "verificado": verificado,
        },
    )
    return result.rowcount or 0


async def patch_status_for_pedido_silent(
    session: AsyncSession,
    *,
    pedido_bling: str,
    status: str,
    aprovado_por: str | None = None,
    verificado: bool | None = None,
) -> None:
    try:
        await patch_status_for_pedido(
            session,
            pedido_bling=pedido_bling,
            status=status,
            aprovado_por=aprovado_por,
            verificado=verificado,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "verificar_margem_status_patch_failed",
            pedido_bling=pedido_bling,
            status=status,
            error=str(e)[:200],
        )


async def patch_financials_for_item(
    session: AsyncSession,
    *,
    bling_order_item_id: str,
    valorbase: object,
    taxacomissao: object,
    custofrete: object,
) -> int:
    """Patch Bling-derived money columns in the snapshot after an inline sync."""
    result = await session.execute(
        text(
            f"""
            UPDATE {SNAPSHOT_TABLE}
            SET bling_valorbase_item = CAST(:valorbase AS numeric),
                bling_taxacomissao_item = CAST(:taxacomissao AS numeric),
                bling_custofrete_item = CAST(:custofrete AS numeric),
                bling_lucro_calculado = CASE
                    WHEN CAST(:valorbase AS numeric) IS NOT NULL
                         AND COALESCE(bling_custo_produtos, 0::numeric) > 0::numeric
                    THEN (
                        CAST(:valorbase AS numeric)
                        - COALESCE(CAST(:custofrete AS numeric), 0::numeric)
                        - COALESCE(CAST(:taxacomissao AS numeric), 0::numeric)
                    ) - bling_custo_produtos
                    ELSE NULL::numeric
                END,
                bling_margem_calculado = CASE
                    WHEN CAST(:valorbase AS numeric) IS NOT NULL
                         AND COALESCE(bling_custo_produtos, 0::numeric) > 0::numeric
                    THEN (
                        (
                            CAST(:valorbase AS numeric)
                            - COALESCE(CAST(:custofrete AS numeric), 0::numeric)
                            - COALESCE(CAST(:taxacomissao AS numeric), 0::numeric)
                        ) - bling_custo_produtos
                    ) / bling_custo_produtos
                    ELSE NULL::numeric
                END
            WHERE bling_order_item_id = CAST(:bling_order_item_id AS uuid)
            """
        ),
        {
            "bling_order_item_id": bling_order_item_id,
            "valorbase": valorbase,
            "taxacomissao": taxacomissao,
            "custofrete": custofrete,
        },
    )
    return result.rowcount or 0


async def patch_financials_for_item_silent(
    session: AsyncSession,
    *,
    bling_order_item_id: str,
    valorbase: object,
    taxacomissao: object,
    custofrete: object,
) -> None:
    try:
        await patch_financials_for_item(
            session,
            bling_order_item_id=bling_order_item_id,
            valorbase=valorbase,
            taxacomissao=taxacomissao,
            custofrete=custofrete,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "verificar_margem_financials_patch_failed",
            bling_order_item_id=bling_order_item_id,
            error=str(e)[:200],
        )
