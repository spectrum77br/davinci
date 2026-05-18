# ruff: noqa: E501, S608
"""resolve pricing_product + pricing_account jointly in vw_conciliacao_margens

The previous pipeline picked one `pricing_segment` per (bling_id, item_codigo)
with a global `LIMIT 1`, then asked pricing_match to find an account whose
slot matches that one segment. When the same SKU lived in multiple
departments (e.g. "Celular > Diversos" + "Catalogo > Diversos"), the global
pick was arbitrary and frequently chose the department with no account
configured, producing "sem account p/ Diversos" gaps in the report.

This migration merges segment resolution into pricing_match's LATERAL so
every (pricing_product, pricing_account) pair is evaluated together per
listing, preferring (1) tuples where a slot actually covers the leaf,
(2) exact SKU match, (3) accounts with listing_type set, (4) recency.

Revision ID: 0054_vw_conciliacao_margens_fix_segment_priority
Revises: 0053_vw_conciliacao_margens_slot_match
Create Date: 2026-05-18
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0054_vw_conciliacao_margens_fix_segment_priority"
down_revision: str | None = "0053_vw_conciliacao_margens_slot_match"
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
        "0053_vw_conciliacao_margens_slot_match.py",
        "_davinci_0053_view",
    )._view_sql())


def _replace_once(sql: str, old: str, new: str) -> str:
    if old not in sql:
        raise RuntimeError(f"view SQL anchor not found: {old[:120]}")
    return sql.replace(old, new, 1)


# Removed entirely: pricing_match below now resolves the leaf segment too.
OLD_PRICING_SEGMENT_CTE = f"""pricing_segment AS (
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
    LEFT JOIN {SCHEMA}.segments leaf ON leaf.id = pp.segment_id
),
"""

NEW_PRICING_SEGMENT_CTE = ""


OLD_PRICING_MATCH_CTE = f"""pricing_match AS (
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


# Joint resolution: pricing_products x segments x pricing_accounts decided
# together per listing. Each candidate (pp, leaf, pa) is ranked and the
# best one wins. pa is LEFT JOIN'd so we still return a pp row even when no
# slot covers the leaf -- those rows just have pricing_account_* NULL and
# rank last. Output columns mirror the previous pricing_match exactly.
NEW_PRICING_MATCH_CTE = f"""pricing_match AS (
    SELECT
        lm.bling_id,
        lm.item_codigo,
        lm.bling_listing_type,
        lm.store_integration_id,
        best.pricing_leaf_segment_id,
        best.pricing_leaf_segment_name,
        best.pricing_leaf_sort_order,
        best.pricing_root_segment_id,
        best.pricing_account_id,
        best.pricing_account_name,
        best.pricing_account_listing_type,
        best.frete_projetado_unit
    FROM listing_match lm
    LEFT JOIN LATERAL (
        SELECT
            leaf.id          AS pricing_leaf_segment_id,
            leaf.name        AS pricing_leaf_segment_name,
            leaf.sort_order  AS pricing_leaf_sort_order,
            leaf.parent_id   AS pricing_root_segment_id,
            pa.id            AS pricing_account_id,
            pa.name          AS pricing_account_name,
            pa.listing_type  AS pricing_account_listing_type,
            CASE
                WHEN pa.slot1_segment_id = leaf.id THEN pa.shipping1
                WHEN pa.slot2_segment_id = leaf.id THEN pa.shipping2
                WHEN pa.slot3_segment_id = leaf.id THEN pa.shipping3
                WHEN pa.slot4_segment_id = leaf.id THEN pa.shipping4
                WHEN pa.slot5_segment_id = leaf.id THEN pa.shipping5
            END AS frete_projetado_unit
        FROM {SCHEMA}.pricing_products pp
        JOIN {SCHEMA}.segments leaf ON leaf.id = pp.segment_id
        LEFT JOIN {SCHEMA}.pricing_accounts pa
               ON pa.integration_id = lm.store_integration_id
              AND leaf.id IN (
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
        WHERE EXISTS (
            SELECT 1
            FROM unnest(string_to_array(replace(pp.sku, ' ', ''), ',')) AS pp_sku(s)
            WHERE pp_sku.s = lm.item_codigo
               OR regexp_replace(pp_sku.s, '\\.[a-z0-9]{{1,4}}$', '')
                  = regexp_replace(split_part(lm.item_codigo, '+', 1), '\\.[a-z0-9]{{1,4}}$', '')
        )
        ORDER BY
            -- 1. real account coverage wins over orphan pp rows
            CASE WHEN pa.id IS NOT NULL THEN 0 ELSE 1 END,
            -- 2. exact SKU match over .xx-suffix-stripped fuzzy fallback
            CASE
                WHEN string_to_array(replace(pp.sku, ' ', ''), ',') @> ARRAY[lm.item_codigo]
                THEN 0 ELSE 1
            END,
            -- 3. accounts with listing_type set (ML premium/classico disambiguation)
            CASE WHEN pa.listing_type IS NOT NULL THEN 0 ELSE 1 END,
            -- 4. recency
            pa.updated_at DESC NULLS LAST,
            pp.updated_at DESC NULLS LAST
        LIMIT 1
    ) best ON TRUE
),"""


def _view_sql() -> str:
    sql = _previous_view_sql()
    sql = _replace_once(sql, OLD_PRICING_SEGMENT_CTE, NEW_PRICING_SEGMENT_CTE)
    sql = _replace_once(sql, OLD_PRICING_MATCH_CTE, NEW_PRICING_MATCH_CTE)
    return sql


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(_view_sql())
    op.execute(
        f"COMMENT ON VIEW {SCHEMA}.{VIEW_NAME} IS "
        "'Compara margem Bling x financeiro marketplace (30d). Resolve pricing_product + pricing_account em conjunto por listing, priorizando slot coberto -> SKU exato -> listing_type -> recencia (corrige SKU multi-departamento).'"
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(_previous_view_sql())
