"""Conciliação — fretes pagos fora da plataforma (Melhor Envio) + venda_frete_pago (15/10/2026).

Envio próprio da Amazon: o frete de verdade é pago no Melhor Envio. Casado com o pedido
do Bling pela etiqueta `mi:reference_code`; entra no Sobrou, não no Líquido. Sem o
frete, o pedido próprio fica "falta frete (Melhor Envio)" em vez de "bateu".

Revision ID: 0407_conciliacao_fretes_pagos
Revises: 0406_configuracoes_seguras
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0407_conciliacao_fretes_pagos"
down_revision: str | None = "0406_configuracoes_seguras"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conciliacao_fretes_pagos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("davinci.integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fonte", sa.String(20), nullable=False, server_default="melhor_envio"),
        sa.Column("envio_id", sa.String(80), nullable=False),
        sa.Column("pedido_bling", sa.String(40), nullable=True),
        sa.Column("pedido_marketplace", sa.String(40), nullable=True),
        sa.Column("protocolo", sa.String(60), nullable=True),
        sa.Column("status", sa.String(30), nullable=True),
        sa.Column("servico", sa.String(80), nullable=True),
        sa.Column("rastreio", sa.String(60), nullable=True),
        sa.Column("preco", sa.Numeric(14, 2), nullable=True),
        sa.Column("desconto", sa.Numeric(14, 2), nullable=True),
        sa.Column("conferencia", sa.Numeric(14, 2), nullable=True),
        sa.Column("frete_pago", sa.Numeric(14, 2), nullable=True),
        sa.Column("pago_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("postado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entregue_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "raw",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("fonte", "envio_id", name="uq_conciliacao_frete_envio"),
        schema="davinci",
    )
    op.create_index(
        "ix_conciliacao_fretes_pagos_integration_id",
        "conciliacao_fretes_pagos",
        ["integration_id"],
        schema="davinci",
    )
    op.create_index(
        "ix_conciliacao_fretes_pagos_pedido_bling",
        "conciliacao_fretes_pagos",
        ["pedido_bling"],
        schema="davinci",
    )
    op.add_column(
        "conciliacao_pedidos",
        sa.Column("venda_frete_pago", sa.Numeric(14, 2), nullable=True),
        schema="davinci",
    )


def downgrade() -> None:
    op.drop_column("conciliacao_pedidos", "venda_frete_pago", schema="davinci")
    op.drop_index(
        "ix_conciliacao_fretes_pagos_pedido_bling",
        table_name="conciliacao_fretes_pagos",
        schema="davinci",
    )
    op.drop_index(
        "ix_conciliacao_fretes_pagos_integration_id",
        table_name="conciliacao_fretes_pagos",
        schema="davinci",
    )
    op.drop_table("conciliacao_fretes_pagos", schema="davinci")
