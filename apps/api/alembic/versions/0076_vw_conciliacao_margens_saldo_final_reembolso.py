# ruff: noqa: E501, S608
"""saldo_final agora soma refunds.reembolso (credito do marketplace)

Ajuste na formula de saldo_final na vw_conciliacao_margens_marketplace:

  saldo_final = saldo_efetivo − prejuizo + reembolso

Onde tanto prejuizo quanto reembolso vem de davinci.refunds, somados
por pedido_bling e proporcionados ao item via item_proportion.

A coluna `ajustes` permanece igual (= SUM(prejuizo) * item_proportion);
apenas saldo_final passa a creditar o reembolso. Quando o marketplace
reembolsa parte do valor, ele volta como saldo para o vendedor.

Revision ID: 0076_vw_conciliacao_margens_saldo_final_reembolso
Revises: 0075_vw_conciliacao_margens_ajustes
Create Date: 2026-05-21
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0076_vw_conciliacao_margens_saldo_final_reembolso"
down_revision: str | None = "0075_vw_conciliacao_margens_ajustes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW = "vw_conciliacao_margens_marketplace"
TABLE = "verificar_margem"


def _load_module(filename: str, modname: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _previous_view_sql() -> str:
    """0075 version: ajustes + saldo_final without reembolso."""
    mod = _load_module(
        "0075_vw_conciliacao_margens_ajustes.py",
        "_davinci_0075_view",
    )
    return mod._view_sql_with_ajustes()


def _replace_once(sql: str, old: str, new: str) -> str:
    if old not in sql:
        raise RuntimeError(f"view SQL anchor not found: {old[:120]}")
    return sql.replace(old, new, 1)


def _view_sql_with_reembolso() -> str:
    sql = _previous_view_sql()
    # 1) LATERAL join: also project reembolso_total alongside prejuizo_total.
    sql = _replace_once(
        sql,
        "SELECT COALESCE(SUM(r.prejuizo), 0::double precision)::numeric AS prejuizo_total\n    FROM davinci.refunds r",
        "SELECT COALESCE(SUM(r.prejuizo), 0::double precision)::numeric AS prejuizo_total,\n"
        "           COALESCE(SUM(r.reembolso), 0::double precision)::numeric AS reembolso_total\n"
        "    FROM davinci.refunds r",
    )
    # 2) saldo_final: subtract prejuizo, add reembolso (proportioned to the item).
    sql = _replace_once(
        sql,
        "(j.marketplace_liquido_base_margem_pedido * j.item_proportion - COALESCE(ra.prejuizo_total, 0::numeric) * j.item_proportion) AS saldo_final",
        "(j.marketplace_liquido_base_margem_pedido * j.item_proportion - COALESCE(ra.prejuizo_total, 0::numeric) * j.item_proportion + COALESCE(ra.reembolso_total, 0::numeric) * j.item_proportion) AS saldo_final",
    )
    return sql


def _recreate_table_from_view() -> None:
    op.execute(f'DROP TABLE IF EXISTS "{SCHEMA}"."{TABLE}"')
    op.execute(
        f'CREATE TABLE "{SCHEMA}"."{TABLE}" AS '
        f'SELECT * FROM "{SCHEMA}"."{VIEW}" WITH NO DATA'
    )
    op.execute(
        f'ALTER TABLE "{SCHEMA}"."{TABLE}" '
        f'ALTER COLUMN bling_order_item_id SET NOT NULL, '
        f'ADD CONSTRAINT {TABLE}_pkey PRIMARY KEY (bling_order_item_id)'
    )
    op.execute(
        f'CREATE INDEX "idx_{TABLE}_bling_id" ON "{SCHEMA}"."{TABLE}" (bling_id)'
    )
    op.execute(
        f'CREATE INDEX "idx_{TABLE}_data" ON "{SCHEMA}"."{TABLE}" (data DESC)'
    )
    op.execute(
        f'INSERT INTO "{SCHEMA}"."{TABLE}" SELECT * FROM "{SCHEMA}"."{VIEW}"'
    )


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_view_sql_with_reembolso())
    _recreate_table_from_view()


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_previous_view_sql())
    _recreate_table_from_view()
