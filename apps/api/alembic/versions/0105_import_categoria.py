"""import: coluna categoria (mala/eletro/celular) nas tabelas import_*

Adiciona `categoria` (default 'mala' pra não quebrar dados existentes) +
index + CHECK constraint nas 8 tabelas que o selector top-level filtra.
import_config (singleton), import_lote_items e cotacao_valores NÃO
recebem — o contexto vem da FK pro pai.

Revision ID: 0105_import_categoria
Revises: 0104_devolution_troca_fields
Create Date: 2026-05-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0105_import_categoria"
down_revision = "0104_devolution_troca_fields"
branch_labels = None
depends_on = None

_TABLES = (
    "import_products",
    "import_lotes",
    "import_resumo",
    "cotacao_fabricantes",
    "cotacao_produtos",
    "import_kit_variations",
    "import_kit_bases",
    "import_kit_marks",
)


def upgrade() -> None:
    for t in _TABLES:
        op.add_column(
            t,
            sa.Column(
                "categoria", sa.String(20),
                nullable=False, server_default="mala",
            ),
        )
        op.create_index(f"ix_{t}_categoria", t, ["categoria"])
        op.create_check_constraint(
            f"ck_{t}_categoria",
            t,
            "categoria IN ('mala','eletro','celular')",
        )


def downgrade() -> None:
    for t in _TABLES:
        op.drop_constraint(f"ck_{t}_categoria", t, type_="check")
        op.drop_index(f"ix_{t}_categoria", table_name=t)
        op.drop_column(t, "categoria")
