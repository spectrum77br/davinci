# ruff: noqa: E501
"""Cria as 3 tabelas da aba Kit em /importacao.

Matriz produto × variação de kit:
  * import_kit_variations — colunas (22 variações fixas: "8", "12+18",
    "8+12+20+24+a075+bp003+a076", …). UNIQUE em (ordem); code não é
    unique porque o Excel tem duplicata legítima nas posições 19/20
    (operador anotou "corrigir sku, separar por cor de mochila").
  * import_kit_bases — linhas (~106 famílias: M2 lisa b001 branca,
    P5 seta b099, mochila bp001 bege, etc). UNIQUE em sku_base.
  * import_kit_marks — célula no cruzamento (só existe se marcado "x"
    no Excel). UNIQUE em (base_id, variation_id) pra evitar duplicata.

Seed dos dados fixos vem em 0099_import_kit_seed.

Revision ID: 0098_import_kit_tables
Revises: 0097_vw_devolucoes_store_fallback
Create Date: 2026-05-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0098_import_kit_tables"
down_revision: str | None = "0097_vw_devolucoes_store_fallback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "import_kit_variations",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("highlight", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("obs", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("ordem", name="uq_import_kit_variations_ordem"),
        schema=SCHEMA,
    )

    op.create_table(
        "import_kit_bases",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("modelo_bling", sa.String(100), nullable=True),
        sa.Column("sku_base", sa.String(50), nullable=False),
        sa.Column("cor", sa.String(50), nullable=True),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("sku_base", name="uq_import_kit_bases_sku_base"),
        schema=SCHEMA,
    )

    op.create_table(
        "import_kit_marks",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "base_id", PG_UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.import_kit_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "variation_id", PG_UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.import_kit_variations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("base_id", "variation_id", name="uq_import_kit_marks_base_var"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_import_kit_marks_base", "import_kit_marks", ["base_id"], schema=SCHEMA,
    )
    op.create_index(
        "ix_import_kit_marks_variation", "import_kit_marks", ["variation_id"], schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_import_kit_marks_variation", table_name="import_kit_marks", schema=SCHEMA)
    op.drop_index("ix_import_kit_marks_base", table_name="import_kit_marks", schema=SCHEMA)
    op.drop_table("import_kit_marks", schema=SCHEMA)
    op.drop_table("import_kit_bases", schema=SCHEMA)
    op.drop_table("import_kit_variations", schema=SCHEMA)
