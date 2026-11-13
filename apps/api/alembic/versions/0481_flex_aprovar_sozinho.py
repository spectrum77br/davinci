"""Flex: "Aprovar sozinho" nas Chaves do Flex e a marca "desligado à mão"

Vinicius, 13/11/2026: o anúncio com peça no .sp ficava em "Esperando
aprovação" até alguém clicar Aprovar — "sempre que ele ver que está tudo
certo, coisa que ele já faz, ele mesmo pode aprovar". Vale para Shopee e ML
(escolha dele, sabendo que o ML pede a ativação como decisão do vendedor).

- flex_ajuste.aprovar_sozinho: NULL = vale o .env (`flex_aprovar_sozinho`,
  falso). Nasce NULL: nada muda no deploy.
- flex_anuncio_estado.desligado_mao_em / desligado_mao_por: uma pessoa
  desligou o Flex do anúncio (no DaVinci ou no painel da plataforma) — o
  sistema não religa sozinho; espera alguém aprovar.

Revision ID: 0481_flex_aprovar_sozinho
Revises: 0480_atendimento_julgamentos
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0481_flex_aprovar_sozinho"
down_revision: str | None = "0480_atendimento_julgamentos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "flex_ajuste", sa.Column("aprovar_sozinho", sa.Boolean(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "flex_anuncio_estado",
        sa.Column("desligado_mao_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "flex_anuncio_estado",
        sa.Column("desligado_mao_por", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f("fk_flex_anuncio_estado_desligado_mao_por_users"),
        "flex_anuncio_estado",
        "users",
        ["desligado_mao_por"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_flex_anuncio_estado_desligado_mao_por_users"),
        "flex_anuncio_estado",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("flex_anuncio_estado", "desligado_mao_por", schema=SCHEMA)
    op.drop_column("flex_anuncio_estado", "desligado_mao_em", schema=SCHEMA)
    op.drop_column("flex_ajuste", "aprovar_sozinho", schema=SCHEMA)
