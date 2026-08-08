# ruff: noqa: E501
"""Simulação ↔ lote de importação + DI.

- financeiro_simulacao: vincula ao lote (`lote_id` FK import_lotes, SET NULL),
  Taxa BL vira campo editável em BRL (`taxa_bl_brl`, antes derivada 9% do CIF)
  e as 7 taxas fixas brasileiras são RENOMEADAS de `*_usd` → `*_brl` (os
  valores da spec — SISCOMEX 154,23; armazenagem 3.644,18 etc. — são reais,
  não dólares; rename preserva os dados).
- import_lotes: PDF congelado da simulação anexada, nº da DI, PDF da DI
  anexado manualmente e o estado do lançamento de pagamento no Bling.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0214_simulacao_lote_di"
down_revision: str | None = "0213_pricing_accounts_offer"
branch_labels = None
depends_on = None

SCHEMA = "davinci"

_RENAMES = [
    ("taxa_siscomex_usd", "taxa_siscomex_brl"),
    ("armazenagem_usd", "armazenagem_brl"),
    ("despachante_sda_usd", "despachante_sda_brl"),
    ("despachante_honorarios_usd", "despachante_honorarios_brl"),
    ("corretagem_cambio_usd", "corretagem_cambio_brl"),
    ("inspecao_usd", "inspecao_brl"),
    ("outras_taxas_usd", "outras_taxas_brl"),
]


def upgrade() -> None:
    # financeiro_simulacao
    for old, new in _RENAMES:
        op.alter_column(
            "financeiro_simulacao", old, new_column_name=new, schema=SCHEMA
        )
    op.add_column(
        "financeiro_simulacao",
        sa.Column("taxa_bl_brl", sa.Numeric(14, 2), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "financeiro_simulacao",
        sa.Column("lote_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_financeiro_simulacao_lote_id_import_lotes",
        "financeiro_simulacao",
        "import_lotes",
        ["lote_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )

    # import_lotes
    op.add_column(
        "import_lotes",
        sa.Column("simulacao_pdf", sa.LargeBinary(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_lotes", sa.Column("di_numero", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "import_lotes",
        sa.Column("di_pdf", sa.LargeBinary(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_lotes",
        sa.Column("di_pdf_filename", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_lotes",
        sa.Column("di_pagamento", postgresql.JSONB(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("import_lotes", "di_pagamento", schema=SCHEMA)
    op.drop_column("import_lotes", "di_pdf_filename", schema=SCHEMA)
    op.drop_column("import_lotes", "di_pdf", schema=SCHEMA)
    op.drop_column("import_lotes", "di_numero", schema=SCHEMA)
    op.drop_column("import_lotes", "simulacao_pdf", schema=SCHEMA)

    op.drop_constraint(
        "fk_financeiro_simulacao_lote_id_import_lotes",
        "financeiro_simulacao",
        schema=SCHEMA,
    )
    op.drop_column("financeiro_simulacao", "lote_id", schema=SCHEMA)
    op.drop_column("financeiro_simulacao", "taxa_bl_brl", schema=SCHEMA)
    for old, new in _RENAMES:
        op.alter_column(
            "financeiro_simulacao", new, new_column_name=old, schema=SCHEMA
        )
