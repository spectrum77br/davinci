# ruff: noqa: E501, S608
"""Perdimento (situação 83956) não desconta o custo de novo no lucro/margem Bling

Em pedidos com situação Perdimento o produto foi perdido e o saldo
(valor_base − frete − taxa) já É o prejuízo lançado manualmente. A fórmula
padrão (`lucro = saldo − custo`) subtraía o custo de novo, contando o valor
do produto duas vezes — ex.: saldo -660 + custo 660 → margem -200% num
prejuízo que deveria ser -100%.

Esta migration injeta um CASE por situação nas colunas bling_lucro_calculado
e bling_margem_calculado de AMBAS as views (a base com janela de 20d e a
irmã `_all` sem janela, ambas alimentam o snapshot verificar_margem):

  Perdimento (83956):  lucro = saldo            ; margem = saldo / custo
  demais situações  :  lucro = saldo − custo    ; margem = (saldo − custo) / custo   (inalterado)

e re-carimba as linhas de perdimento já materializadas no snapshot com a
fórmula corrigida (UPDATE direcionado, sem rebuild — preserva linhas de
pedidos antigos fora da janela de 20d).

Revision ID: 0142_vw_conciliacao_margens_perdimento_margem
Revises: 0141_export_notas_enum
Create Date: 2026-06-16
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0142_vw_conciliacao_margens_perdimento_margem"
down_revision: str | None = "0141_export_notas_enum"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_BASE = "vw_conciliacao_margens_marketplace"
VIEW_ALL = "vw_conciliacao_margens_marketplace_all"
TABLE = "verificar_margem"
PERDIMENTO = "83956"

# Bloco lucro/margem padrão, exatamente como inserido pela 0074 (intacto
# através das migrations 0075/0076/0079/0080) e presente nas duas views.
_OLD_CALC = """        CASE
            WHEN j.bling_valorbase_item IS NOT NULL AND COALESCE(j.bling_custo_produtos, 0::numeric) > 0::numeric
            THEN (j.bling_valorbase_item - COALESCE(j.bling_custofrete_item, 0::numeric) - COALESCE(j.bling_taxacomissao_item, 0::numeric)) - j.bling_custo_produtos
            ELSE NULL::numeric
        END AS bling_lucro_calculado,
        CASE
            WHEN j.bling_valorbase_item IS NOT NULL AND COALESCE(j.bling_custo_produtos, 0::numeric) > 0::numeric
            THEN ((j.bling_valorbase_item - COALESCE(j.bling_custofrete_item, 0::numeric) - COALESCE(j.bling_taxacomissao_item, 0::numeric)) - j.bling_custo_produtos) / j.bling_custo_produtos
            ELSE NULL::numeric
        END AS bling_margem_calculado"""

# Versão perdimento-aware: para situação 83956 não desconta o custo de novo.
_NEW_CALC = """        CASE
            WHEN j.bling_valorbase_item IS NOT NULL AND COALESCE(j.bling_custo_produtos, 0::numeric) > 0::numeric
            THEN CASE
                WHEN j.situacao::text = '83956'
                THEN (j.bling_valorbase_item - COALESCE(j.bling_custofrete_item, 0::numeric) - COALESCE(j.bling_taxacomissao_item, 0::numeric))
                ELSE (j.bling_valorbase_item - COALESCE(j.bling_custofrete_item, 0::numeric) - COALESCE(j.bling_taxacomissao_item, 0::numeric)) - j.bling_custo_produtos
            END
            ELSE NULL::numeric
        END AS bling_lucro_calculado,
        CASE
            WHEN j.bling_valorbase_item IS NOT NULL AND COALESCE(j.bling_custo_produtos, 0::numeric) > 0::numeric
            THEN CASE
                WHEN j.situacao::text = '83956'
                THEN (j.bling_valorbase_item - COALESCE(j.bling_custofrete_item, 0::numeric) - COALESCE(j.bling_taxacomissao_item, 0::numeric)) / j.bling_custo_produtos
                ELSE ((j.bling_valorbase_item - COALESCE(j.bling_custofrete_item, 0::numeric) - COALESCE(j.bling_taxacomissao_item, 0::numeric)) - j.bling_custo_produtos) / j.bling_custo_produtos
            END
            ELSE NULL::numeric
        END AS bling_margem_calculado"""


def _load_module(filename: str, modname: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _base_view_sql_current() -> str:
    """CREATE da view base, versão corrente (0079)."""
    mod = _load_module(
        "0079_vw_conciliacao_margens_ajustes_reembolso.py",
        "_davinci_0079_view",
    )
    return mod._view_sql_ajustes_from_reembolso()


def _all_view_sql_current() -> str:
    """CREATE da view _all, versão corrente (0080)."""
    mod = _load_module(
        "0080_vw_conciliacao_margens_all.py",
        "_davinci_0080_all_view",
    )
    return mod._all_view_sql()


def _with_perdimento(sql: str) -> str:
    if _OLD_CALC not in sql:
        raise RuntimeError("bling_lucro/margem_calculado anchor não encontrado na view SQL")
    return sql.replace(_OLD_CALC, _NEW_CALC, 1)


# UPDATE direcionado: re-carimba lucro/margem das linhas de perdimento já
# no snapshot, com a fórmula nova (upgrade) ou antiga (downgrade).
def _resnapshot_perdimento(*, perdimento_drops_custo: bool) -> None:
    if perdimento_drops_custo:
        lucro_expr = "(bling_valorbase_item - COALESCE(bling_custofrete_item, 0::numeric) - COALESCE(bling_taxacomissao_item, 0::numeric))"
    else:
        lucro_expr = "((bling_valorbase_item - COALESCE(bling_custofrete_item, 0::numeric) - COALESCE(bling_taxacomissao_item, 0::numeric)) - bling_custo_produtos)"
    op.execute(
        f"""
        UPDATE "{SCHEMA}"."{TABLE}"
        SET bling_lucro_calculado = CASE
                WHEN bling_valorbase_item IS NOT NULL AND COALESCE(bling_custo_produtos, 0::numeric) > 0::numeric
                THEN {lucro_expr}
                ELSE NULL::numeric END,
            bling_margem_calculado = CASE
                WHEN bling_valorbase_item IS NOT NULL AND COALESCE(bling_custo_produtos, 0::numeric) > 0::numeric
                THEN {lucro_expr} / bling_custo_produtos
                ELSE NULL::numeric END
        WHERE situacao::text = '{PERDIMENTO}'
        """
    )


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW_BASE}" CASCADE')
    op.execute(_with_perdimento(_base_view_sql_current()))
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW_ALL}" CASCADE')
    op.execute(_with_perdimento(_all_view_sql_current()))
    _resnapshot_perdimento(perdimento_drops_custo=True)


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW_BASE}" CASCADE')
    op.execute(_base_view_sql_current())
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW_ALL}" CASCADE')
    op.execute(_all_view_sql_current())
    _resnapshot_perdimento(perdimento_drops_custo=False)
