# ruff: noqa: E501, S608
"""add frete projetado (pricing_accounts) to vw_conciliacao_margens_marketplace

Revision ID: 0047_vw_conciliacao_margens_frete_projetado
Revises: 0046_marketplace_freight_reconciliation
Create Date: 2026-05-16
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0050_vw_conciliacao_margens_frete_projetado"
down_revision: str | None = "0049_pricing_products_uq_user_sku_dept"
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
        "0046_marketplace_freight_reconciliation.py",
        "_davinci_0046_view",
    )._view_sql())


def _replace_once(sql: str, old: str, new: str) -> str:
    if old not in sql:
        raise RuntimeError(f"view SQL anchor not found: {old[:120]}")
    return sql.replace(old, new, 1)


PRICING_CTES = f"""
bp_keys AS (
    SELECT DISTINCT bling_id, item_codigo, loja, marketplace
    FROM {SCHEMA}.vw_bling_pedidos
    WHERE data >= now() - interval '30 days'
),
store_integ AS (
    SELECT
        bk.bling_id,
        bk.item_codigo,
        bk.marketplace,
        st.integration_id AS store_integration_id
    FROM bp_keys bk
    LEFT JOIN {SCHEMA}.stores st ON st.bling_store_id = NULLIF(bk.loja, '')::bigint
),
listing_match AS (
    SELECT
        si.bling_id,
        si.item_codigo,
        si.marketplace,
        si.store_integration_id,
        l.listing_type AS bling_listing_type
    FROM store_integ si
    LEFT JOIN LATERAL (
        SELECT l.listing_type
        FROM {SCHEMA}.listings l
        WHERE l.integration_id = si.store_integration_id
          AND l.sku = si.item_codigo
          AND l.platform::text = CASE si.marketplace
              WHEN 'ml'     THEN 'ml'
              WHEN 'amazon' THEN 'amazon'
              WHEN 'shopee' THEN 'shopee'
              WHEN 'tiktok' THEN 'tiktok'
              WHEN 'temu'   THEN 'temu'
              ELSE si.marketplace
          END
        ORDER BY l.updated_at DESC NULLS LAST
        LIMIT 1
    ) l ON TRUE
),
pricing_segment AS (
    SELECT
        bk.bling_id,
        bk.item_codigo,
        leaf.id          AS pricing_leaf_segment_id,
        leaf.name        AS pricing_leaf_segment_name,
        leaf.sort_order  AS pricing_leaf_sort_order,
        leaf.parent_id   AS pricing_root_segment_id
    FROM bp_keys bk
    LEFT JOIN LATERAL (
        SELECT pp.segment_id
        FROM {SCHEMA}.pricing_products pp
        WHERE string_to_array(pp.sku, ',') @> ARRAY[bk.item_codigo]
        LIMIT 1
    ) pp ON TRUE
    LEFT JOIN {SCHEMA}.segments leaf ON leaf.id = pp.segment_id
),
pricing_match AS (
    SELECT
        lm.bling_id,
        lm.item_codigo,
        lm.bling_listing_type,
        lm.store_integration_id,
        ps.pricing_leaf_segment_id,
        ps.pricing_leaf_segment_name,
        ps.pricing_leaf_sort_order,
        ps.pricing_root_segment_id,
        pa.id    AS pricing_account_id,
        pa.name  AS pricing_account_name,
        pa.listing_type AS pricing_account_listing_type,
        CASE ps.pricing_leaf_sort_order
            WHEN 0 THEN pa.shipping1
            WHEN 1 THEN pa.shipping2
            WHEN 2 THEN pa.shipping3
            WHEN 3 THEN pa.shipping4
            WHEN 4 THEN pa.shipping5
        END AS frete_projetado_unit
    FROM listing_match lm
    JOIN pricing_segment ps USING (bling_id, item_codigo)
    LEFT JOIN LATERAL (
        SELECT pa.*
        FROM {SCHEMA}.pricing_accounts pa
        WHERE pa.integration_id = lm.store_integration_id
          AND pa.segment_id     = ps.pricing_root_segment_id
          AND pa.platform::text = CASE lm.marketplace
              WHEN 'ml'         THEN 'mercadolivre'
              WHEN 'amazon'     THEN 'amazon'
              WHEN 'shopee'     THEN 'shopee'
              WHEN 'tiktok'     THEN 'tiktok'
              WHEN 'aliexpress' THEN 'aliexpress'
              WHEN 'temu'       THEN 'temu'
              ELSE lm.marketplace
          END
          AND (
              lm.marketplace <> 'ml'
              OR pa.listing_type IS NULL
              OR lm.bling_listing_type IS NULL
              OR (lm.bling_listing_type IN ('gold_pro','gold_premium') AND pa.listing_type ILIKE '%premium%')
              OR (lm.bling_listing_type = 'gold_special'               AND pa.listing_type ILIKE '%classico%')
          )
        ORDER BY
            CASE WHEN pa.listing_type IS NOT NULL THEN 0 ELSE 1 END,
            pa.updated_at DESC NULLS LAST
        LIMIT 1
    ) pa ON TRUE
),
"""


SELECT_PROJETADO_BLOCK = """,
    pm.pricing_account_id,
    pm.pricing_account_name,
    pm.pricing_account_listing_type,
    pm.bling_listing_type,
    pm.pricing_leaf_segment_id,
    pm.pricing_leaf_segment_name,
    pm.pricing_leaf_sort_order,
    pm.frete_projetado_unit,
    (pm.frete_projetado_unit * j.quantidade) AS frete_projetado_item,
    CASE
        WHEN pm.frete_projetado_unit IS NOT NULL
             AND j.marketplace_frete_real_cobrado_pedido IS NOT NULL
        THEN (pm.frete_projetado_unit * j.quantidade)
             - (j.marketplace_frete_real_cobrado_pedido * j.item_proportion)
        ELSE NULL::numeric
    END AS frete_resultado_item
"""


def _view_sql() -> str:
    sql = _previous_view_sql()
    # 1) Inject our extra CTEs before `joined AS (`
    sql = _replace_once(sql, "joined AS (\n", PRICING_CTES + "joined AS (\n")
    # 2) Append the new columns at the very end of the final SELECT
    sql = _replace_once(
        sql,
        "    j.marketplace_frete_itens\nFROM joined j",
        "    j.marketplace_frete_itens"
        + SELECT_PROJETADO_BLOCK
        + "\nFROM joined j\nLEFT JOIN pricing_match pm ON pm.bling_id = j.bling_id AND pm.item_codigo = j.sku",
    )
    return sql


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(_view_sql())
    op.execute(
        f"COMMENT ON VIEW {SCHEMA}.{VIEW_NAME} IS "
        "'Compara margem Bling x financeiro marketplace dos ultimos 30 dias, inclui frete real/prometido e frete projetado da Tabela de Precos (pricing_accounts.shipping{1..5}).'"
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(_previous_view_sql())
