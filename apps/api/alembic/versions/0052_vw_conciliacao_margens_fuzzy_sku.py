# ruff: noqa: E501, S608
"""fuzzy SKU match in vw_conciliacao_margens_marketplace

Bling sells variants with suffixes (.pi/.ci/.ra/.sa/.sp) and combos
(`dg053.sp+a001.sp`), while pricing_products stores the SKU base
(`dg053`). This migration teaches the pricing_segment CTE to:

1. take the first part of the Bling SKU (split on '+'),
2. strip a trailing `.xx` (1-4 alphanum) suffix,
3. and match against pricing_products.sku CSV entries that are equal
   either exactly OR after the same suffix strip.

Impact measured on 30d window: 39% -> 97.7% coverage of frete_projetado.

Revision ID: 0049_vw_conciliacao_margens_fuzzy_sku
Revises: 0048_bling_orders_observacao
Create Date: 2026-05-16
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0052_vw_conciliacao_margens_fuzzy_sku"
down_revision: str | None = "0051_bling_orders_observacao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_NAME = "vw_conciliacao_margens_marketplace"


def _load_module(filename: str, modname: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _previous_view_sql() -> str:
    return str(_load_module(
        "0050_vw_conciliacao_margens_frete_projetado.py",
        "_davinci_0050_view",
    )._view_sql())


def _replace_once(sql: str, old: str, new: str) -> str:
    if old not in sql:
        raise RuntimeError(f"view SQL anchor not found: {old[:120]}")
    return sql.replace(old, new, 1)


OLD_PRICING_SEGMENT = f"""    LEFT JOIN LATERAL (
        SELECT pp.segment_id
        FROM {SCHEMA}.pricing_products pp
        WHERE string_to_array(pp.sku, ',') @> ARRAY[bk.item_codigo]
        LIMIT 1
    ) pp ON TRUE
    LEFT JOIN {SCHEMA}.segments leaf ON leaf.id = pp.segment_id"""


NEW_PRICING_SEGMENT = f"""    LEFT JOIN LATERAL (
        SELECT pp.segment_id
        FROM {SCHEMA}.pricing_products pp
        WHERE EXISTS (
            SELECT 1
            FROM unnest(string_to_array(replace(pp.sku, ' ', ''), ',')) AS pp_sku(s)
            WHERE pp_sku.s = bk.item_codigo
               OR regexp_replace(pp_sku.s, '\\.[a-z0-9]{{1,4}}$', '')
                  = regexp_replace(split_part(bk.item_codigo, '+', 1), '\\.[a-z0-9]{{1,4}}$', '')
        )
        ORDER BY
            -- prefer exact-match rows over suffix-stripped fallbacks
            CASE
                WHEN string_to_array(replace(pp.sku, ' ', ''), ',') @> ARRAY[bk.item_codigo]
                THEN 0 ELSE 1
            END,
            pp.updated_at DESC NULLS LAST
        LIMIT 1
    ) pp ON TRUE
    LEFT JOIN {SCHEMA}.segments leaf ON leaf.id = pp.segment_id"""


def _view_sql() -> str:
    sql = _previous_view_sql()
    return _replace_once(sql, OLD_PRICING_SEGMENT, NEW_PRICING_SEGMENT)


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(_view_sql())
    op.execute(
        f"COMMENT ON VIEW {SCHEMA}.{VIEW_NAME} IS "
        "'Compara margem Bling x financeiro marketplace (30d), inclui frete projetado da Tabela de Precos com fuzzy SKU match (split por + e strip de sufixo .xx).'"
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(_previous_view_sql())
