# ruff: noqa: E501, S608
"""sibling view vw_conciliacao_margens_marketplace_all without 20d window

Cria uma irma da view padrao, identica em colunas e logica, mas sem o
filtro `data >= now() - interval '20 days'` aplicado dentro dos CTEs.

Motivacao: pedidos antigos (fora da janela de 20d) podem ser inseridos
manualmente na pagina de reembolso. O hook que atualiza
`verificar_margem` precisa enxergar esses pedidos para que apareçam na
pagina de margens e o usuario possa aprovar o saldo final ajustado.

A view original continua existindo como esta — o cron de snapshot e o
rebuild manual continuam usando ela (janela de 20d, performance).
Somente os helpers `refresh_for_pedido` / `refresh_for_bling_id` usam
a versao "_all".

Revision ID: 0080_vw_conciliacao_margens_all
Revises: 0079_vw_conciliacao_margens_ajustes_reembolso
Create Date: 2026-05-21
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0080_vw_conciliacao_margens_all"
down_revision: str | None = "0079_vw_conciliacao_margens_ajustes_reembolso"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_BASE = "vw_conciliacao_margens_marketplace"
VIEW_ALL = "vw_conciliacao_margens_marketplace_all"

# Substring procurado dentro do SQL base (apos as substituicoes de
# `interval '30 days'` -> `interval '20 days'` feitas pela 0055). Aparece
# duas vezes: uma no CTE `bp_keys`, outra no FROM (vw_bling_pedidos ...) bp.
_DATE_FILTER = "WHERE data >= now() - interval '20 days'"


def _load_module(filename: str, modname: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _base_view_sql() -> str:
    """SQL CREATE da view base, na versao corrente (0079)."""
    mod = _load_module(
        "0079_vw_conciliacao_margens_ajustes_reembolso.py",
        "_davinci_0079_view",
    )
    return mod._view_sql_ajustes_from_reembolso()


def _all_view_sql() -> str:
    sql = _base_view_sql()
    # Renomear a view alvo do CREATE para a versao _all.
    target_base = f"CREATE OR REPLACE VIEW {SCHEMA}.{VIEW_BASE} AS"
    target_all = f"CREATE OR REPLACE VIEW {SCHEMA}.{VIEW_ALL} AS"
    if target_base not in sql:
        raise RuntimeError("CREATE VIEW anchor not found in base SQL")
    sql = sql.replace(target_base, target_all, 1)

    # Remover os dois filtros de 20 dias.
    if sql.count(_DATE_FILTER) != 2:
        raise RuntimeError(
            f"expected 2 occurrences of date filter, got {sql.count(_DATE_FILTER)}"
        )
    sql = sql.replace(_DATE_FILTER, "WHERE TRUE")
    return sql


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW_ALL}" CASCADE')
    op.execute(_all_view_sql())


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW_ALL}" CASCADE')
