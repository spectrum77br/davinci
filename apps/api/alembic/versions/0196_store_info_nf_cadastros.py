# ruff: noqa: E501
"""store_info: FKs para os cadastros de NF (faturador/etiqueta/impressao)

Cada loja aponta pra um cadastro de Faturador / Etiqueta / Impressão do sistema
de notas fiscais automáticas. Na tela Lojas a coluna "Impressão" substitui a
coluna "Frete" (o campo `freight` continua no banco, só some da UI). FKs
nullable com ondelete SET NULL — apagar um cadastro não apaga a loja.

Revision ID: 0196_store_info_nf_cadastros
Revises: 0195_nf_etiqueta_impressao
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0196_store_info_nf_cadastros"
down_revision: str | None = "0195_nf_etiqueta_impressao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "store_info",
        sa.Column("nf_faturador_id", PG_UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "store_info",
        sa.Column("nf_etiqueta_id", PG_UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "store_info",
        sa.Column("nf_impressao_id", PG_UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_store_info_nf_faturador_id",
        "store_info",
        "nf_faturador",
        ["nf_faturador_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_store_info_nf_etiqueta_id",
        "store_info",
        "nf_etiqueta",
        ["nf_etiqueta_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_store_info_nf_impressao_id",
        "store_info",
        "nf_impressao",
        ["nf_impressao_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_store_info_nf_impressao_id", "store_info", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_constraint(
        "fk_store_info_nf_etiqueta_id", "store_info", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_constraint(
        "fk_store_info_nf_faturador_id", "store_info", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_column("store_info", "nf_impressao_id", schema=SCHEMA)
    op.drop_column("store_info", "nf_etiqueta_id", schema=SCHEMA)
    op.drop_column("store_info", "nf_faturador_id", schema=SCHEMA)
