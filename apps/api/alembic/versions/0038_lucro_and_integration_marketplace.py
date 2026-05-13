# ruff: noqa: E501, S608
"""add lucro and use integrations platform in vw_bling_pedidos

Revision ID: 0038_lucro_marketplace
Revises: 0037_margens_conta_loja_nome
Create Date: 2026-05-13
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0038_lucro_marketplace"
down_revision: str | None = "0037_margens_conta_loja_nome"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_NAME = "vw_bling_pedidos"

MARKETPLACE_PATTERN = re.compile(
    r"s\.marketplace::text\s+AS\s+marketplace",
    re.IGNORECASE,
)
STORES_JOIN_PATTERN = re.compile(
    r"(LEFT\s+JOIN\s+(?:davinci\.)?stores\s+s\s+ON\s+s\.bling_store_id\s*=\s*CASE\s+WHEN\s+wm\.loja\s*~\s*'\^\[0-9\]\+\$'(?:::text)?\s+THEN\s+wm\.loja::bigint\s+ELSE\s+NULL::bigint\s+END\s*)",
    re.IGNORECASE,
)
MARGIN_FLOOR_PATTERN = re.compile(
    r"(END\s+AS\s+margin_floor_diff)(\s+FROM\s+with_margin\s+wm)",
    re.IGNORECASE,
)

LUCRO_SQL = """
        CASE
            WHEN COALESCE(wm.preco_custo::numeric(10,2), 0::numeric(10,2)) > 0::numeric
                 AND COALESCE(wm.item_quantidade, 0) > 0
                 AND wm.valorbase_proporcional > 0::numeric
            THEN wm.valorbase_proporcional
                 - (wm.custofrete_proporcional + wm.taxacomissao_proporcional)
                 - wm.preco_custo::numeric(10,2) * wm.item_quantidade::numeric
            ELSE NULL::numeric
        END AS lucro"""


def _current_view_sql() -> str:
    bind = op.get_bind()
    sql = sa.text("SELECT pg_get_viewdef(CAST(:view_name AS regclass), true)")
    return bind.execute(sql, {"view_name": f"{SCHEMA}.{VIEW_NAME}"}).scalar_one()


def _replace_once(view_sql: str, pattern: re.Pattern[str], replacement: str) -> str:
    rewritten, count = pattern.subn(replacement, view_sql, count=1)
    if count != 1:
        raise RuntimeError(f"Could not rewrite {SCHEMA}.{VIEW_NAME}")
    return rewritten


def _create_view(view_sql: str) -> None:
    op.execute(f"SET LOCAL search_path TO {SCHEMA}, public")
    op.execute(f"CREATE OR REPLACE VIEW {SCHEMA}.{VIEW_NAME} AS\n{view_sql}")


def _upgrade_view(view_sql: str) -> str:
    view_sql = _replace_once(
        view_sql,
        MARKETPLACE_PATTERN,
        "COALESCE(i.platform::text, s.marketplace::text) AS marketplace",
    )
    view_sql = _replace_once(
        view_sql,
        STORES_JOIN_PATTERN,
        r"\1\n     LEFT JOIN integrations i ON i.id = s.integration_id\n    ",
    )
    view_sql = _replace_once(
        view_sql,
        MARGIN_FLOOR_PATTERN,
        rf"\1,{LUCRO_SQL}\2",
    )
    return view_sql


def _downgrade_view(view_sql: str) -> str:
    view_sql = re.sub(
        r"COALESCE\(i\.platform::text,\s*s\.marketplace::text\)\s+AS\s+marketplace",
        "s.marketplace::text AS marketplace",
        view_sql,
        count=1,
        flags=re.IGNORECASE,
    )
    view_sql = re.sub(
        r"\s+LEFT\s+JOIN\s+(?:davinci\.)?integrations\s+i\s+ON\s+i\.id\s*=\s*s\.integration_id",
        "",
        view_sql,
        count=1,
        flags=re.IGNORECASE,
    )
    view_sql = re.sub(
        r",\s*CASE\s+WHEN\s+COALESCE\(wm\.preco_custo::numeric\(10,2\),\s*0::numeric\(10,2\)\)\s*>\s*0::numeric\s+AND\s+COALESCE\(wm\.item_quantidade,\s*0\)\s*>\s*0\s+AND\s+wm\.valorbase_proporcional\s*>\s*0::numeric\s+THEN\s+wm\.valorbase_proporcional\s*-\s*\(wm\.custofrete_proporcional\s*\+\s*wm\.taxacomissao_proporcional\)\s*-\s*wm\.preco_custo::numeric\(10,2\)\s*\*\s*wm\.item_quantidade::numeric\s+ELSE\s+NULL::numeric\s+END\s+AS\s+lucro",
        "",
        view_sql,
        count=1,
        flags=re.IGNORECASE,
    )
    return view_sql


def upgrade() -> None:
    op.add_column("margens", sa.Column("lucro", sa.Float(), nullable=True), schema=SCHEMA)

    _create_view(_upgrade_view(_current_view_sql()))

    op.execute(
        f"""
        WITH src AS (
            SELECT DISTINCT ON (numero, LOWER(item_codigo))
                   numero,
                   LOWER(item_codigo) AS sku_lc,
                   lucro
              FROM {SCHEMA}.vw_bling_pedidos
             WHERE lucro IS NOT NULL
             ORDER BY numero, LOWER(item_codigo), bo_created_at DESC NULLS LAST
        )
        UPDATE {SCHEMA}.margens m
           SET lucro = src.lucro::double precision
          FROM src
         WHERE m.pedido_bling::text = src.numero
           AND LOWER(COALESCE(m.sku, '')) = src.sku_lc
           AND m.lucro IS DISTINCT FROM src.lucro::double precision
        """
    )


def downgrade() -> None:
    _create_view(_downgrade_view(_current_view_sql()))
    op.drop_column("margens", "lucro", schema=SCHEMA)
