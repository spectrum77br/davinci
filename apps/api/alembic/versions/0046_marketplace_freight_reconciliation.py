# ruff: noqa: E501, S608
"""add marketplace freight reconciliation

Revision ID: 0046_marketplace_freight_reconciliation
Revises: 0045_vw_conciliacao_margens
Create Date: 2026-05-15
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0046_marketplace_freight_reconciliation"
down_revision: str | None = "0045_vw_conciliacao_margens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_NAME = "vw_conciliacao_margens_marketplace"


def _previous_view_sql() -> str:
    path = Path(__file__).with_name("0045_vw_conciliacao_margens_marketplace.py")
    spec = importlib.util.spec_from_file_location("_davinci_0045_view", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load 0045 view migration")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.VIEW_SQL)


def _replace_once(sql: str, old: str, new: str) -> str:
    if old not in sql:
        raise RuntimeError(f"view SQL anchor not found: {old[:80]}")
    return sql.replace(old, new, 1)


def _view_sql() -> str:
    sql = _previous_view_sql()
    freight_ctes = f"""
freight_actual_by_shipment AS (
    SELECT
        fr.order_financial_id,
        COALESCE(fr.shipping_id, fr.id::text) AS shipping_key,
        MAX(fr.freight_actual_amount) AS freight_actual_amount
    FROM {SCHEMA}.marketplace_order_freight_reconciliations fr
    WHERE fr.freight_actual_amount IS NOT NULL
    GROUP BY fr.order_financial_id, COALESCE(fr.shipping_id, fr.id::text)
),
freight_actual_totals AS (
    SELECT
        fs.order_financial_id,
        SUM(fs.freight_actual_amount) AS marketplace_frete_real_cobrado_pedido
    FROM freight_actual_by_shipment fs
    GROUP BY fs.order_financial_id
),
freight_totals AS (
    SELECT
        fr.order_financial_id,
        COUNT(*) AS marketplace_frete_reconciliacao_registros,
        COUNT(*) FILTER (WHERE fr.status = 'error') AS marketplace_frete_reconciliacao_erros,
        string_agg(DISTINCT fr.status, ', ' ORDER BY fr.status) AS marketplace_frete_reconciliacao_status,
        string_agg(DISTINCT fr.shipping_id, ', ' ORDER BY fr.shipping_id) FILTER (WHERE fr.shipping_id IS NOT NULL) AS marketplace_frete_shipping_id,
        MAX(fr.dimensions_text) FILTER (WHERE fr.dimensions_text IS NOT NULL) AS marketplace_frete_dimensoes,
        fa.marketplace_frete_real_cobrado_pedido,
        SUM(fr.freight_promised_amount) AS marketplace_frete_prometido_pedido,
        SUM(fr.freight_list_cost_amount) AS marketplace_frete_list_cost_pedido,
        AVG(fr.freight_discount_rate) FILTER (WHERE fr.freight_discount_rate IS NOT NULL) AS marketplace_frete_desconto_rate_medio,
        CASE
            WHEN fa.marketplace_frete_real_cobrado_pedido IS NOT NULL
                 AND SUM(fr.freight_promised_amount) IS NOT NULL
            THEN fa.marketplace_frete_real_cobrado_pedido - SUM(fr.freight_promised_amount)
            ELSE NULL::numeric
        END AS marketplace_frete_diferenca_pedido,
        CASE
            WHEN fa.marketplace_frete_real_cobrado_pedido IS NOT NULL
                 AND SUM(fr.freight_promised_amount) IS NOT NULL
                 AND SUM(fr.freight_promised_amount) <> 0
            THEN ((fa.marketplace_frete_real_cobrado_pedido - SUM(fr.freight_promised_amount))
                / SUM(fr.freight_promised_amount)) * 100
            ELSE NULL::numeric
        END AS marketplace_frete_diferenca_pct,
        jsonb_agg(
            jsonb_build_object(
                'item_index', fr.item_index,
                'status', fr.status,
                'shipping_id', fr.shipping_id,
                'marketplace_item_id', fr.marketplace_item_id,
                'sku', fr.sku,
                'quantity', fr.quantity,
                'frete_real_cobrado', fr.freight_actual_amount,
                'frete_prometido', fr.freight_promised_amount,
                'list_cost', fr.freight_list_cost_amount,
                'discount_rate', fr.freight_discount_rate,
                'diff', fr.freight_diff_amount,
                'diff_pct', fr.freight_diff_pct,
                'dimensions', fr.dimensions_text,
                'last_error', fr.last_error
            )
            ORDER BY fr.item_index
        ) AS marketplace_frete_itens
    FROM {SCHEMA}.marketplace_order_freight_reconciliations fr
    LEFT JOIN freight_actual_totals fa ON fa.order_financial_id = fr.order_financial_id
    GROUP BY fr.order_financial_id, fa.marketplace_frete_real_cobrado_pedido
),
"""
    sql = _replace_once(sql, "joined AS (\n", freight_ctes + "joined AS (\n")
    sql = _replace_once(
        sql,
        "        et.evento_net_estimated\n    FROM (",
        """        et.evento_net_estimated,
        ft.marketplace_frete_reconciliacao_registros,
        ft.marketplace_frete_reconciliacao_erros,
        ft.marketplace_frete_reconciliacao_status,
        ft.marketplace_frete_shipping_id,
        ft.marketplace_frete_dimensoes,
        ft.marketplace_frete_real_cobrado_pedido,
        ft.marketplace_frete_prometido_pedido,
        ft.marketplace_frete_list_cost_pedido,
        ft.marketplace_frete_desconto_rate_medio,
        ft.marketplace_frete_diferenca_pedido,
        ft.marketplace_frete_diferenca_pct,
        ft.marketplace_frete_itens
    FROM (""",
    )
    sql = _replace_once(
        sql,
        "    LEFT JOIN event_totals et ON et.order_financial_id = f.id\n)",
        """    LEFT JOIN event_totals et ON et.order_financial_id = f.id
    LEFT JOIN freight_totals ft ON ft.order_financial_id = f.id
)""",
    )
    sql = _replace_once(
        sql,
        "    j.evento_net_estimated\nFROM joined j",
        """    j.evento_net_estimated,
    j.marketplace_frete_reconciliacao_registros,
    j.marketplace_frete_reconciliacao_erros,
    j.marketplace_frete_reconciliacao_status,
    j.marketplace_frete_shipping_id,
    j.marketplace_frete_dimensoes,
    j.marketplace_frete_real_cobrado_pedido,
    j.marketplace_frete_prometido_pedido,
    j.marketplace_frete_list_cost_pedido,
    j.marketplace_frete_desconto_rate_medio,
    j.marketplace_frete_diferenca_pedido,
    j.marketplace_frete_diferenca_pct,
    (j.marketplace_frete_real_cobrado_pedido * j.item_proportion) AS marketplace_frete_real_cobrado_item,
    (j.marketplace_frete_prometido_pedido * j.item_proportion) AS marketplace_frete_prometido_item,
    (j.marketplace_frete_diferenca_pedido * j.item_proportion) AS marketplace_frete_diferenca_item,
    j.marketplace_frete_itens
FROM joined j""",
    )
    return sql


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(
        f"""
        CREATE TABLE "{SCHEMA}".marketplace_order_freight_reconciliations (
            id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            order_financial_id          UUID        NOT NULL REFERENCES "{SCHEMA}".marketplace_order_financials(id) ON DELETE CASCADE,
            platform                    "{SCHEMA}".integration_platform NOT NULL,
            integration_id              UUID        NULL REFERENCES "{SCHEMA}".integrations(id) ON DELETE SET NULL,
            store_id                    UUID        NULL REFERENCES "{SCHEMA}".stores(id) ON DELETE SET NULL,
            bling_id                    BIGINT      NULL,
            external_order_id           TEXT        NOT NULL,
            item_index                  INTEGER     NOT NULL,
            status                      VARCHAR(32) NOT NULL DEFAULT 'pending',
            currency                    VARCHAR(8)  NOT NULL DEFAULT 'BRL',
            seller_id                   TEXT        NULL,
            shipping_id                 TEXT        NULL,
            pack_id                     TEXT        NULL,
            shipping_status             TEXT        NULL,
            marketplace_item_id         TEXT        NULL,
            marketplace_variation_id    TEXT        NULL,
            sku                         TEXT        NULL,
            title                       TEXT        NULL,
            quantity                    NUMERIC(14,4) NULL,
            freight_actual_amount       NUMERIC(14,2) NULL,
            freight_promised_amount     NUMERIC(14,2) NULL,
            freight_list_cost_amount    NUMERIC(14,2) NULL,
            freight_discount_rate       NUMERIC(10,6) NULL,
            freight_diff_amount         NUMERIC(14,2) NULL,
            freight_diff_pct            NUMERIC(14,4) NULL,
            dimension_width             NUMERIC(14,4) NULL,
            dimension_length            NUMERIC(14,4) NULL,
            dimension_height            NUMERIC(14,4) NULL,
            dimension_weight            NUMERIC(14,4) NULL,
            dimensions_text             TEXT        NULL,
            raw                         JSONB       NOT NULL DEFAULT '{{}}'::jsonb,
            fetched_at                  TIMESTAMPTZ NULL,
            last_error                  TEXT        NULL,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_marketplace_order_freight_reconciliations_financial_item
                UNIQUE (order_financial_id, item_index)
        )
        """
    )
    op.execute(
        f'CREATE INDEX ix_marketplace_order_freight_reconciliations_order '
        f'ON "{SCHEMA}".marketplace_order_freight_reconciliations (order_financial_id)'
    )
    op.execute(
        f'CREATE INDEX ix_marketplace_order_freight_reconciliations_shipping '
        f'ON "{SCHEMA}".marketplace_order_freight_reconciliations (shipping_id)'
    )
    op.execute(
        f'CREATE INDEX ix_marketplace_order_freight_reconciliations_bling '
        f'ON "{SCHEMA}".marketplace_order_freight_reconciliations (bling_id, external_order_id)'
    )
    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.marketplace_order_freight_reconciliations IS "
        "'Guarda frete real cobrado do seller e frete prometido/simulado por item do marketplace, vinculado ao financeiro do pedido.'"
    )
    op.execute(_view_sql())
    op.execute(
        f"COMMENT ON VIEW {SCHEMA}.{VIEW_NAME} IS "
        "'Compara margem Bling x financeiro marketplace dos ultimos 30 dias e inclui frete real/prometido para conciliacao.'"
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(_previous_view_sql())
    op.execute(f'DROP TABLE IF EXISTS "{SCHEMA}".marketplace_order_freight_reconciliations')
