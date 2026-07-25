# ruff: noqa: E501
"""create nf_etiqueta + nf_impressao tables

Cadastros de ETIQUETA (onde a NF é inserida na plataforma) e IMPRESSÃO (como a
etiqueta é impressa) do sistema de notas fiscais automáticas. Espelham as
seções R14–R22 e R26–R29 da aba NF de `tarefa 25.xlsx`. Listas extensíveis.

Revision ID: 0195_nf_etiqueta_impressao
Revises: 0194_nf_faturador
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0195_nf_etiqueta_impressao"
down_revision: str | None = "0194_nf_faturador"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "nf_etiqueta",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("plataforma", sa.Text(), nullable=False),
        sa.Column("modo", sa.Text(), nullable=True),
        sa.Column("ads_power", sa.Text(), nullable=True),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )

    op.create_table(
        "nf_impressao",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("visualizacao", sa.Text(), nullable=True),
        sa.Column("usa_etiqueta", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("usa_declaracao", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("usa_nota", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("nf_impressao", schema=SCHEMA)
    op.drop_table("nf_etiqueta", schema=SCHEMA)
