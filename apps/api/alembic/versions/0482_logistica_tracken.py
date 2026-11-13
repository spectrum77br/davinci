"""Logística: o que a transportadora do Flex (TRACKen) diz do pacote

Vinicius, 13/11/2026: "depois do Bling podemos criar uma coluna Tracken,
somente na sub aba Flex" — o site da transportadora que entrega os Flex
(TM Logística). O DaVinci lê o rastreio público do Tracken (sem login: número
do pedido + CEP do cliente) de 30 em 30 min e guarda aqui.

Tabela nova, nada muda no deploy.

Revision ID: 0482_logistica_tracken
Revises: 0481_flex_aprovar_sozinho
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0482_logistica_tracken"
down_revision: str | None = "0481_flex_aprovar_sozinho"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.create_table(
        "logistica_tracken",
        sa.Column("logistica_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("achado", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("remessa", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("ultimo_evento", sa.Text(), nullable=True),
        sa.Column("ultimo_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("previsao", sa.Date(), nullable=True),
        sa.Column("entregue_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recebedor", sa.Text(), nullable=True),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("tentativas", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "historico",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("lido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["logistica_id"],
            [f"{SCHEMA}.logistica.id"],
            name=op.f("fk_logistica_tracken_logistica_id_logistica"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("logistica_id", name=op.f("pk_logistica_tracken")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("logistica_tracken", schema=SCHEMA)
