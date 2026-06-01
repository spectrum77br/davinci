"""bling_situacao_audit: trilha de auditoria das mudanças de situação no Bling

Revision ID: 0114_bling_situacao_audit
Revises: 0113_vw_bling_pedidos_qty_weighted_proportion

Registra cada mudança de situação de pedido no Bling feita PELO app
(origem 'margens' | 'devolucao' | 'job_envio'), com quando, quem (ou sistema)
e a transição antiga → nova. Mudanças feitas direto no painel do Bling não
entram aqui (a API do Bling não expõe o autor).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0114_bling_situacao_audit"
down_revision: str | None = "0113_vw_bling_pedidos_qty_weighted_proportion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    op.create_table(
        "bling_situacao_audit",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("pedido_bling", sa.Text(), nullable=False),
        sa.Column("bling_id", sa.Text(), nullable=True),
        sa.Column("sku", sa.Text(), nullable=True),
        sa.Column("situacao_antiga", sa.Text(), nullable=True),
        sa.Column("situacao_nova", sa.Text(), nullable=False),
        sa.Column("origem", sa.Text(), nullable=False),
        sa.Column(
            "mudado_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_bling_situacao_audit_created_at",
        "bling_situacao_audit",
        ["created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_bling_situacao_audit_pedido_bling",
        "bling_situacao_audit",
        ["pedido_bling"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_bling_situacao_audit_bling_id",
        "bling_situacao_audit",
        ["bling_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_bling_situacao_audit_mudado_por",
        "bling_situacao_audit",
        ["mudado_por"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_index(
        "ix_bling_situacao_audit_mudado_por",
        table_name="bling_situacao_audit",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_bling_situacao_audit_bling_id",
        table_name="bling_situacao_audit",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_bling_situacao_audit_pedido_bling",
        table_name="bling_situacao_audit",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_bling_situacao_audit_created_at",
        table_name="bling_situacao_audit",
        schema=SCHEMA,
    )
    op.drop_table("bling_situacao_audit", schema=SCHEMA)
