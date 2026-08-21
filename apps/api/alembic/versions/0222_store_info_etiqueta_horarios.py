"""Horário de etiqueta vira campo da LOJA (some o cadastro por plataforma).

A 0221 criou `nf_etiqueta_horario` como cadastro por PLATAFORMA (aba na tela
NF — Cadastros). O lugar certo é a tela Lojas, ao lado de Etiqueta/Impressão:
cada loja diz em que horários as etiquetas dela são impressas.

`store_info.etiqueta_horarios` = "HH:MM" separados por vírgula, em BRT
(ex. "10:00, 14:00"). NULL/vazio = contínuo (imprime assim que a NF fecha).

A tabela 0221 estava vazia — nada a migrar.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0222_store_info_etiqueta_horarios"
down_revision: str | None = "0221_nf_etiqueta_horario"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "store_info",
        sa.Column("etiqueta_horarios", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.drop_table("nf_etiqueta_horario", schema=SCHEMA)


def downgrade() -> None:
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
    op.drop_column("store_info", "etiqueta_horarios", schema=SCHEMA)
