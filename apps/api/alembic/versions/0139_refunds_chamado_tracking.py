"""refunds: tracking de URL e resolução de chamados

Revision ID: 0139_refunds_chamado_tracking
Revises: 0138_import_lote_item_target_sku

Adiciona campos usados pela automação Hermes que monitora respostas dos
chamados de frete no Mercado Livre.
"""

from alembic import op
import sqlalchemy as sa

revision = "0139_refunds_chamado_tracking"
down_revision = "0138_import_lote_item_target_sku"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "refunds",
        sa.Column("chamado_url", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "refunds",
        sa.Column(
            "chamado_resolvido",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_refunds_chamado_monitor",
        "refunds",
        ["plataforma", "tipo", "chamado_resolvido"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_refunds_chamado_monitor", table_name="refunds", schema=SCHEMA)
    op.drop_column("refunds", "chamado_resolvido", schema=SCHEMA)
    op.drop_column("refunds", "chamado_url", schema=SCHEMA)
