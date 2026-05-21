# ruff: noqa: E501, S608
"""ajustes agora projeta refunds.reembolso (credito), nao refunds.prejuizo

Mudanca pontual na vw_conciliacao_margens_marketplace: a coluna `ajustes`
passa a expor SUM(refunds.reembolso) * item_proportion em vez de
SUM(refunds.prejuizo) * item_proportion. saldo_final continua igual
(saldo_efetivo - prejuizo + reembolso, ambos do refunds).

A coluna mantem o nome `ajustes` no schema (frontend ainda exibe como
"Ajustes"), so a fonte do valor muda — usuario quer ver o credito
de reembolso na coluna em vez do prejuizo.

Revision ID: 0079_vw_conciliacao_margens_ajustes_reembolso
Revises: 0078_devolutions
Create Date: 2026-05-21
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0079_vw_conciliacao_margens_ajustes_reembolso"
down_revision: str | None = "0078_devolutions"
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
    """0076 version: ajustes = prejuizo * item_proportion."""
    mod = _load_module(
        "0076_vw_conciliacao_margens_saldo_final_reembolso.py",
        "_davinci_0076_view",
    )
    return mod._view_sql_with_reembolso()


def _replace_once(sql: str, old: str, new: str) -> str:
    if old not in sql:
        raise RuntimeError(f"view SQL anchor not found: {old[:120]}")
    return sql.replace(old, new, 1)


def _view_sql_ajustes_from_reembolso() -> str:
    sql = _previous_view_sql()
    sql = _replace_once(
        sql,
        "(COALESCE(ra.prejuizo_total, 0::numeric) * j.item_proportion) AS ajustes",
        "(COALESCE(ra.reembolso_total, 0::numeric) * j.item_proportion) AS ajustes",
    )
    return sql


def _view_sql_ajustes_from_prejuizo() -> str:
    return _previous_view_sql()


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
    op.execute(_view_sql_ajustes_from_reembolso())
    _recreate_table_from_view()


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_view_sql_ajustes_from_prejuizo())
    _recreate_table_from_view()
