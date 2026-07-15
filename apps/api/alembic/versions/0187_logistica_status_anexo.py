# ruff: noqa: E501
"""logistica_status_anexo: imagens anexadas à mensagem do chamado

Guarda a imagem como blob (bytea) no próprio banco — o app não tem storage
externo. Servida de volta por endpoint autenticado (cookie) pra o <img>.

Revision ID: 0187_logistica_status_anexo
Revises: 0186_logistica_status_abrir_reembolso
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0187_logistica_status_anexo"
down_revision: str | None = "0186_logistica_status_abrir_reembolso"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "logistica_status_anexo",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("status_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("created_by", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["status_id"], [f"{SCHEMA}.logistica_status.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], [f"{SCHEMA}.users.id"], ondelete="SET NULL"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_logistica_status_anexo_status_id",
        "logistica_status_anexo",
        ["status_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_logistica_status_anexo_status_id",
        table_name="logistica_status_anexo",
        schema=SCHEMA,
    )
    op.drop_table("logistica_status_anexo", schema=SCHEMA)
