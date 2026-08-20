"""nf_etiqueta_horario: quando as etiquetas de cada plataforma são impressas.

Uma linha por PLATAFORMA (mesma granularidade da aba Etiqueta), valendo pra
todas as lojas dela:

- modo 'continuo'  -> imprime assim que a NF fecha (Shopee/TikTok).
- modo 'horario'   -> só nos horários cadastrados, em BRT (ML/Amazon).

Só cadastro por enquanto — nenhum worker lê a tabela ainda.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0221_nf_etiqueta_horario"
down_revision: str | None = "0220_logistica_status_datas"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "nf_etiqueta_horario",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("plataforma", sa.Text(), nullable=False),
        sa.Column("modo", sa.Text(), nullable=False, server_default=sa.text("'horario'")),
        sa.Column(
            "horarios",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("nf_etiqueta_horario", schema=SCHEMA)
