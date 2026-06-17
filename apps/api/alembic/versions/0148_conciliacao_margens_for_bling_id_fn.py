# ruff: noqa: E501, S608
"""Função conciliacao_margens_for_bling_id: caminho rápido por pedido.

A view `vw_conciliacao_margens_marketplace_all` (sem janela de tempo) é lenta
(~3 min) mesmo filtrando por um pedido: a CTE `pricing_match` resolve o pricing
de TODO o histórico (~43k pedidos, regexp de variantes em pricing_products) e o
planner não consegue empurrar o filtro `pedido`/`bling_id` pra dentro dela —
~88% do custo do plano (EXPLAIN: 31,3M de 31,3M). Por isso o refresh pontual de
um pedido antigo (refresh_for_pedido/bling_id, usado pelo botão "atualizar este
pedido" da página de Margem) demorava minutos.

Esta migration cria uma FUNÇÃO que é a própria definição da view com o filtro
`bling_id` injetado nos dois pontos de entrada do `vw_bling_pedidos` (as cláusulas
`WHERE true` em `bp_keys` e em `joined`). Com isso a `pricing_match` processa só
o pedido pedido → o custo do plano cai ~637× (31,3M → 49k) e a query roda em ~2s.

Geração a partir do `pg_get_viewdef` ao vivo (mesma técnica das migrations que
editam a view): assim a função nasce em sincronia com a view ATUAL. Se uma
migration futura mudar a estrutura da `_all` (em especial os dois `WHERE true`),
esta função fica defasada e precisa ser regenerada — rode novamente o corpo do
upgrade (CREATE OR REPLACE) numa nova migration. O `_replace_exact` falha alto se
o número de `WHERE true` mudar, evitando gerar uma função silenciosamente errada.

Revision ID: 0148_conciliacao_margens_for_bling_id_fn
Revises: 0147_bling_orders_reembolso_so_conferido
Create Date: 2026-06-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0148_conciliacao_margens_for_bling_id_fn"
down_revision: str | None = "0147_bling_orders_reembolso_so_conferido"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_ALL = "vw_conciliacao_margens_marketplace_all"
FN = "conciliacao_margens_for_bling_id"

_WHERE_OLD = "WHERE true"
_WHERE_NEW = "WHERE vw_bling_pedidos.bling_id = p_bling_id"
_WHERE_COUNT = 2


def _view_sql() -> str:
    bind = op.get_bind()
    sql = sa.text("SELECT pg_get_viewdef(CAST(:v AS regclass), true)")
    return bind.execute(sql, {"v": f"{SCHEMA}.{VIEW_ALL}"}).scalar_one()


def upgrade() -> None:
    body = _view_sql().rstrip().rstrip(";")
    found = body.count(_WHERE_OLD)
    if found != _WHERE_COUNT:
        raise RuntimeError(
            f"{FN}: esperava {_WHERE_COUNT} ocorrencia(s) de {_WHERE_OLD!r} na "
            f"definicao de {VIEW_ALL}, achei {found}. A estrutura da view mudou "
            f"— revise os pontos de injecao do filtro bling_id."
        )
    body = body.replace(_WHERE_OLD, _WHERE_NEW)
    op.execute(f"SET LOCAL search_path TO {SCHEMA}, public")
    op.execute(
        f"CREATE OR REPLACE FUNCTION {SCHEMA}.{FN}(p_bling_id bigint)\n"
        f"RETURNS SETOF {SCHEMA}.{VIEW_ALL}\n"
        f"LANGUAGE sql STABLE\n"
        f"SET search_path = {SCHEMA}, public\n"
        f"AS $func$\n{body}\n$func$;"
    )


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{FN}(bigint)")
