# ruff: noqa: E501, S608
"""join vw_bling_pedidos stores by Bling loja id

Revision ID: 0036_vw_bling_store_join
Revises: 0035_merge_heads
Create Date: 2026-05-13
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0036_vw_bling_store_join"
down_revision: str | None = "0035_merge_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_NAME = "vw_bling_pedidos"

OLD_JOIN = "LEFT JOIN davinci.stores s ON s.id = wm.store_id\n    "
NEW_JOIN = (
    "LEFT JOIN davinci.stores s ON s.bling_store_id = "
    "CASE WHEN wm.loja ~ '^[0-9]+$' THEN wm.loja::bigint ELSE NULL::bigint END\n    "
)

OLD_JOIN_PATTERN = re.compile(
    r"LEFT\s+JOIN\s+(?:davinci\.)?stores\s+s\s+ON\s+\(*\s*s\.id\s*=\s*wm\.store_id\s*\)*",
    re.IGNORECASE,
)
NEW_JOIN_PATTERN = re.compile(
    r"LEFT\s+JOIN\s+(?:davinci\.)?stores\s+s\s+ON\s+\(*\s*s\.bling_store_id\s*=\s*CASE\s+WHEN\s+wm\.loja\s*~\s*'\^\[0-9\]\+\$'(?:::text)?\s+THEN\s+wm\.loja::bigint\s+ELSE\s+NULL::bigint\s+END\s*\)*",
    re.IGNORECASE,
)


def _current_view_sql() -> str:
    bind = op.get_bind()
    sql = sa.text("SELECT pg_get_viewdef(CAST(:view_name AS regclass), true)")
    return bind.execute(sql, {"view_name": f"{SCHEMA}.{VIEW_NAME}"}).scalar_one()


def _replace_join(view_sql: str, pattern: re.Pattern[str], replacement: str) -> str:
    rewritten, count = pattern.subn(replacement, view_sql, count=1)
    if count != 1:
        raise RuntimeError(f"Could not find stores join in {SCHEMA}.{VIEW_NAME}")
    return rewritten


def _create_view(view_sql: str) -> None:
    op.execute(f"SET LOCAL search_path TO {SCHEMA}, public")
    op.execute(f"CREATE OR REPLACE VIEW {SCHEMA}.{VIEW_NAME} AS\n{view_sql}")


def upgrade() -> None:
    view_sql = _current_view_sql()
    _create_view(_replace_join(view_sql, OLD_JOIN_PATTERN, NEW_JOIN))


def downgrade() -> None:
    view_sql = _current_view_sql()
    _create_view(_replace_join(view_sql, NEW_JOIN_PATTERN, OLD_JOIN))
