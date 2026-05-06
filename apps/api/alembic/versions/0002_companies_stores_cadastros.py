"""companies, stores, cadastros + reserved tables (Fase 1.6)

Revision ID: 0002_companies_stores_cadastros
Revises: 0001_baseline_auth
Create Date: 2026-05-05

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_companies_stores_cadastros"
down_revision: Union[str, None] = "0001_baseline_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"

MARKETPLACES = ("ml", "shopee", "amazon", "aliexpress", "temu", "tiktok", "shein", "magalu", "site")
STORE_STATUSES = ("active", "inactive", "closing", "banned", "pending", "under_review")
CADASTRO_TIPOS = ("fone", "email", "dominio")
CADASTRO_STATUSES = ("active", "inactive", "excluded")


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    marketplace = postgresql.ENUM(*MARKETPLACES, name="marketplace", schema=SCHEMA)
    marketplace.create(op.get_bind(), checkfirst=True)
    store_status = postgresql.ENUM(*STORE_STATUSES, name="store_status", schema=SCHEMA)
    store_status.create(op.get_bind(), checkfirst=True)
    cadastro_tipo = postgresql.ENUM(*CADASTRO_TIPOS, name="cadastro_tipo", schema=SCHEMA)
    cadastro_tipo.create(op.get_bind(), checkfirst=True)
    cadastro_status = postgresql.ENUM(*CADASTRO_STATUSES, name="cadastro_status", schema=SCHEMA)
    cadastro_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("razao_social", sa.Text(), nullable=False),
        sa.Column("apelido", sa.Text(), nullable=False),
        sa.Column("responsavel_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uf", sa.String(2), nullable=True),
        sa.Column("cnpj", sa.String(14), nullable=True),
        sa.Column("inscricao_estadual", sa.Text(), nullable=True),
        sa.Column("site_url", sa.Text(), nullable=True),
        sa.Column("obs", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["responsavel_id"], [f"{SCHEMA}.users.id"],
            ondelete="SET NULL", name="fk_companies_responsavel_id_users",
        ),
        sa.UniqueConstraint("cnpj", name="uq_companies_cnpj"),
        schema=SCHEMA,
    )
    op.create_index("ix_companies_apelido", "companies", ["apelido"], schema=SCHEMA)

    op.create_table(
        "stores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "marketplace",
            postgresql.ENUM(*MARKETPLACES, name="marketplace", schema=SCHEMA, create_type=False),
            nullable=False,
        ),
        sa.Column("apelido_override", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(*STORE_STATUSES, name="store_status", schema=SCHEMA, create_type=False),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        # FK to integrations added in a later migration (Fase 2). Plain UUID for now.
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("bling_store_id", sa.BigInteger(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], [f"{SCHEMA}.companies.id"],
            ondelete="CASCADE", name="fk_stores_company_id_companies",
        ),
        sa.UniqueConstraint("company_id", "marketplace", name="uq_stores_company_id_marketplace"),
        sa.UniqueConstraint("integration_id", name="uq_stores_integration_id"),
        schema=SCHEMA,
    )
    op.create_index("ix_stores_integration_id", "stores", ["integration_id"], schema=SCHEMA)
    op.create_index("ix_stores_bling_store_id", "stores", ["bling_store_id"], schema=SCHEMA)
    op.create_index("ix_stores_status", "stores", ["status"], schema=SCHEMA)

    op.create_table(
        "cadastros",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "tipo",
            postgresql.ENUM(*CADASTRO_TIPOS, name="cadastro_tipo", schema=SCHEMA, create_type=False),
            nullable=False,
        ),
        sa.Column("provedor", sa.Text(), nullable=True),
        sa.Column("responsavel_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("codigo", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(*CADASTRO_STATUSES, name="cadastro_status", schema=SCHEMA, create_type=False),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("obs", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["responsavel_id"], [f"{SCHEMA}.users.id"],
            ondelete="SET NULL", name="fk_cadastros_responsavel_id_users",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_cadastros_tipo_codigo", "cadastros", ["tipo", "codigo"], schema=SCHEMA)

    op.create_table(
        "cadastros_stores",
        sa.Column("cadastro_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.Text(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("cadastro_id", "store_id", name="pk_cadastros_stores"),
        sa.ForeignKeyConstraint(
            ["cadastro_id"], [f"{SCHEMA}.cadastros.id"],
            ondelete="CASCADE", name="fk_cadastros_stores_cadastro_id_cadastros",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], [f"{SCHEMA}.stores.id"],
            ondelete="CASCADE", name="fk_cadastros_stores_store_id_stores",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_cadastros_stores_store_id", "cadastros_stores", ["store_id"], schema=SCHEMA)

    # Reserved tables: only id + created_at, used as FK targets later.
    for tbl in ("margens", "conciliacao_frete"):
        op.create_table(
            tbl,
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.text("now()"), nullable=False),
            schema=SCHEMA,
        )

    for tbl in ("companies", "stores", "cadastros"):
        op.execute(f"""
            CREATE TRIGGER trg_{tbl}_updated_at
            BEFORE UPDATE ON "{SCHEMA}".{tbl}
            FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at();
        """)


def downgrade() -> None:
    for tbl in ("companies", "stores", "cadastros"):
        op.execute(f'DROP TRIGGER IF EXISTS trg_{tbl}_updated_at ON "{SCHEMA}".{tbl}')

    op.drop_table("conciliacao_frete", schema=SCHEMA)
    op.drop_table("margens", schema=SCHEMA)
    op.drop_index("ix_cadastros_stores_store_id", table_name="cadastros_stores", schema=SCHEMA)
    op.drop_table("cadastros_stores", schema=SCHEMA)
    op.drop_index("ix_cadastros_tipo_codigo", table_name="cadastros", schema=SCHEMA)
    op.drop_table("cadastros", schema=SCHEMA)
    op.drop_index("ix_stores_status", table_name="stores", schema=SCHEMA)
    op.drop_index("ix_stores_bling_store_id", table_name="stores", schema=SCHEMA)
    op.drop_index("ix_stores_integration_id", table_name="stores", schema=SCHEMA)
    op.drop_table("stores", schema=SCHEMA)
    op.drop_index("ix_companies_apelido", table_name="companies", schema=SCHEMA)
    op.drop_table("companies", schema=SCHEMA)

    op.execute(f'DROP TYPE IF EXISTS "{SCHEMA}".cadastro_status')
    op.execute(f'DROP TYPE IF EXISTS "{SCHEMA}".cadastro_tipo')
    op.execute(f'DROP TYPE IF EXISTS "{SCHEMA}".store_status')
    op.execute(f'DROP TYPE IF EXISTS "{SCHEMA}".marketplace')
