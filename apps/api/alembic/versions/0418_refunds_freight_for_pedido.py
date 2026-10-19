"""Consulta de frete por pedido preservando a definição da view de 20 dias.

O filtro externo de pedido não é empurrado para a CTE pricing_match. A função
mantém os dois limites de 20 dias e restringe as entradas antes dessa CTE.
As chaves de pricing incluem TODOS os pedidos recentes com o mesmo bling_id:
filtrá-las só pelo número alteraria a multiplicidade se houver dados legados
com números diferentes para o mesmo ID.

Gerada da definição vigente, sem alterar cálculos, views ou registros. Se a
view mudar depois, esta função também deve ser regenerada em uma migration.

Revision ID: 0418_refunds_freight_for_pedido
Revises: 0417_conciliacao_previsto_na_venda
"""

# Os nomes de schema/objetos são controlados pela aplicação.
# ruff: noqa: S608

import sqlalchemy as sa

from alembic import op
from app.config import get_settings

revision = "0418_refunds_freight_for_pedido"
down_revision = "0417_conciliacao_previsto_na_venda"
branch_labels = None
depends_on = None

SCHEMA = get_settings().database_schema
VIEW = "vw_conciliacao_margens_marketplace"
FN = "refunds_freight_for_pedido"
_DATE_GUARD = "WHERE vw_bling_pedidos.data >= (now() - '20 days'::interval)"


def _scoped_body(definition: str) -> str:
    body = definition.rstrip().rstrip(";")
    if body.count(_DATE_GUARD) != 2:
        raise RuntimeError(
            f"{FN}: definição inesperada de {VIEW}; esperava dois filtros de 20 dias. "
            "Revise bp_keys e joined antes de criar a função."
        )
    before, between, after = body.split(_DATE_GUARD)
    keys_guard = f"""{_DATE_GUARD}
            AND vw_bling_pedidos.bling_id IN (
                SELECT refund_target.bling_id
                FROM "{SCHEMA}".vw_bling_pedidos refund_target
                WHERE refund_target.numero = p_pedido_bling
                  AND refund_target.data >= (now() - '20 days'::interval)
            )"""
    order_guard = f"{_DATE_GUARD} AND vw_bling_pedidos.numero = p_pedido_bling"
    return before + keys_guard + between + order_guard + after


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    definition = op.get_bind().execute(
        sa.text("SELECT pg_get_viewdef(CAST(:view AS regclass), true)"),
        {"view": f"{SCHEMA}.{VIEW}"},
    ).scalar_one()
    body = _scoped_body(definition)
    op.execute(
        f'CREATE OR REPLACE FUNCTION "{SCHEMA}".{FN}(p_pedido_bling text)\n'
        f'RETURNS SETOF "{SCHEMA}".{VIEW}\n'
        "LANGUAGE sql STABLE\n"
        f'SET search_path = "{SCHEMA}", public\n'
        f"AS $function$\n{body}\n$function$"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(f'DROP FUNCTION IF EXISTS "{SCHEMA}".{FN}(text)')
