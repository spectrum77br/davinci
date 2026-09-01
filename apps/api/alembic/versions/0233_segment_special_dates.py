"""Datas Especiais por segmento — exceção da triagem de margem.

Eduardo (01/09/2026): "em segmentos, que as margens que utilizamos em
margens, vamos colocar um novo campo chamado datas especiais, que é a regra
que vamos aprovar, para exceção, por exemplo está com margem negativa,
aprova". Cada linha é uma janela (date_start..date_end, inclusiva, datas
BRT) em que pedidos do segmento — e de todos os descendentes — não são
travados por margem baixa: min_margin NULL = aprova qualquer margem;
preenchido = piso especial em FRAÇÃO (-0.15 = -15%), mesma escala de
segments.min_margin. Lida por _MARGEM_DATA_ESPECIAL_SQL (routers/margens.py);
o auto-hold herda a exceção por import.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0233_segment_special_dates"
down_revision: str | None = "0232_user_threema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "segment_special_dates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "segment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.segments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date_start", sa.Date(), nullable=False),
        sa.Column("date_end", sa.Date(), nullable=False),
        sa.Column("min_margin", sa.Numeric(6, 4), nullable=True),
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
        schema=SCHEMA,
    )
    op.create_index(
        "ix_segment_special_dates_segment_id",
        "segment_special_dates",
        ["segment_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_segment_special_dates_segment_id",
        table_name="segment_special_dates",
        schema=SCHEMA,
    )
    op.drop_table("segment_special_dates", schema=SCHEMA)
