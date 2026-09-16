"""conferência do preço vivo depois do envio para a Amazon

Eduardo (16/09/2026): enviou R$ 445 pela Tabela de Preços, o DaVinci disse "ok",
a Amazon guardou 445 no atributo do anúncio — e continuou vendendo a R$ 599.
"Precisamos corrigir para não ficar os preços divergentes."

O envio não tem como forçar a Amazon a aplicar (ela aceita e depois decide,
tipicamente por regra de precificação automática ligada no anúncio). O que o
DaVinci pode garantir é não ficar calado: minutos depois de cada envio, um
conferente lê o preço VIVO na Amazon e, se divergir do enviado, avisa. Esta
tabela é o registro dessa conferência, um por envio.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0284_pricing_push_confirmacao"
down_revision: str | None = "0283_legenda_modelos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "pricing_push_confirmacao",
        sa.Column("push_key", sa.String(128), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("preco_enviado", sa.Numeric(12, 2), nullable=False),
        # confirmado | divergente | sem_leitura
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("conferidos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "divergentes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("conferido_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pricing_push_confirmacao_status",
        "pricing_push_confirmacao",
        ["status", "conferido_em"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pricing_push_confirmacao_status",
        table_name="pricing_push_confirmacao",
        schema=SCHEMA,
    )
    op.drop_table("pricing_push_confirmacao", schema=SCHEMA)
