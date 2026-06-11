"""bling_notas: contas Bling dedicadas a emissão de NF

Tabela separada de `integrations` de propósito — são apps OAuth do Bling
diferentes da conta principal de sync. Guarda o par client_id/secret em
base64 (header Basic do refresh token) e os tokens OAuth correntes.

Idempotente: a tabela foi criada direto no prod em 2026-06-11 (checkout
local estava atrás do head de migrations), então o upgrade só executa se
ela ainda não existir.

Revision ID: 0140_bling_notas
Revises: 0139_refunds_chamado_tracking
Create Date: 2026-06-11

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0140_bling_notas"
down_revision: Union[str, None] = "0139_refunds_chamado_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    exists = op.get_bind().execute(
        sa.text(f"SELECT to_regclass('{SCHEMA}.bling_notas')")
    ).scalar()
    if exists is not None:
        return

    op.create_table(
        "bling_notas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("basic_auth_b64", sa.Text(), nullable=False),
        sa.Column("authorization_code", sa.Text(), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("nome", name="uq_bling_notas_nome"),
        schema=SCHEMA,
    )

    op.execute(f"""
        CREATE TRIGGER trg_bling_notas_updated_at
        BEFORE UPDATE ON "{SCHEMA}".bling_notas
        FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at();
    """)


def downgrade() -> None:
    op.execute(f'DROP TRIGGER IF EXISTS trg_bling_notas_updated_at ON "{SCHEMA}".bling_notas')
    op.drop_table("bling_notas", schema=SCHEMA)
