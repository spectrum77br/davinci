# ruff: noqa: E501, S608
"""switch pricing_account match to use slot{1..5}_segment_id

The previous implementation derived shipping index from
`leaf.sort_order + 1`, which silently breaks if anyone reorders
segments. `pricing_accounts.slot{1..5}_segment_id` already maps each
shipping slot to its leaf segment explicitly per account, so we use
that as the source of truth.

Behavior today: identical results (all 107 accounts have slots aligned
with sort_order). Behavior tomorrow: resilient to segment reordering
and supports per-account slot customization.

Revision ID: 0050_vw_conciliacao_margens_slot_match
Revises: 0049_vw_conciliacao_margens_fuzzy_sku
Create Date: 2026-05-16
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0050_vw_conciliacao_margens_slot_match"
down_revision: str | None = "0049_vw_conciliacao_margens_fuzzy_sku"
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
        "0049_vw_conciliacao_margens_fuzzy_sku.py",
        "_davinci_0049_view",
    )._view_sql())


def _replace_once(sql: str, old: str, new: str) -> str:
    if old not in sql:
        raise RuntimeError(f"view SQL anchor not found: {old[:120]}")
    return sql.replace(old, new, 1)


# Rewrite pricing_match CTE: use slot{1..5}_segment_id instead of sort_order.
OLD_PRICING_MATCH = f"""pricing_match AS (
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
),"""

NEW_PRICING_MATCH = f"""pricing_match AS (
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
        -- Match the product's leaf segment against slot{{1..5}}_segment_id;
        -- pick the corresponding shipping value. This replaces the prior
        -- `leaf.sort_order + 1` indirection.
        CASE
            WHEN pa.slot1_segment_id = ps.pricing_leaf_segment_id THEN pa.shipping1
            WHEN pa.slot2_segment_id = ps.pricing_leaf_segment_id THEN pa.shipping2
            WHEN pa.slot3_segment_id = ps.pricing_leaf_segment_id THEN pa.shipping3
            WHEN pa.slot4_segment_id = ps.pricing_leaf_segment_id THEN pa.shipping4
            WHEN pa.slot5_segment_id = ps.pricing_leaf_segment_id THEN pa.shipping5
        END AS frete_projetado_unit
    FROM listing_match lm
    JOIN pricing_segment ps USING (bling_id, item_codigo)
    LEFT JOIN LATERAL (
        SELECT pa.*
        FROM {SCHEMA}.pricing_accounts pa
        WHERE pa.integration_id = lm.store_integration_id
          AND ps.pricing_leaf_segment_id IN (
              pa.slot1_segment_id, pa.slot2_segment_id,
              pa.slot3_segment_id, pa.slot4_segment_id,
              pa.slot5_segment_id
          )
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
),"""


def _view_sql() -> str:
    sql = _previous_view_sql()
    return _replace_once(sql, OLD_PRICING_MATCH, NEW_PRICING_MATCH)


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(_view_sql())
    op.execute(
        f"COMMENT ON VIEW {SCHEMA}.{VIEW_NAME} IS "
        "'Compara margem Bling x financeiro marketplace (30d) e usa pricing_accounts.slot{1..5}_segment_id para resolver o frete projetado por conta + segmento leaf (resiliente a reordenacao de segments).'"
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(_previous_view_sql())
