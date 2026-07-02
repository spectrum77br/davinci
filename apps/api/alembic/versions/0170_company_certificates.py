# ruff: noqa: E501, S608
"""company_certificates: certificados digitais (.p12/.pfx) cifrados por empresa

Tabela nova pra guardar certificados digitais das empresas. `blob` e
`password_enc` são AES-GCM (nonce || ct, chave CREDENTIALS_KEY) — nunca em
claro no banco. Vários certificados por empresa (histórico A1/A3). Acesso só
admin (rotas em app/routers/company_certificates.py). FK ON DELETE CASCADE:
excluir a empresa apaga os certificados junto.

Revision ID: 0170_company_certificates
Revises: 0169_companies_operacao_contabilidade
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0170_company_certificates"
down_revision: str | None = "0169_companies_operacao_contabilidade"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "company_certificates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("password_enc", sa.LargeBinary(), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], [f"{SCHEMA}.companies.id"],
            ondelete="CASCADE", name="fk_company_certificates_company_id_companies",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], [f"{SCHEMA}.users.id"],
            ondelete="SET NULL", name="fk_company_certificates_uploaded_by_users",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_company_certificates_company_id",
        "company_certificates",
        ["company_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_company_certificates_company_id", table_name="company_certificates", schema=SCHEMA)
    op.drop_table("company_certificates", schema=SCHEMA)
