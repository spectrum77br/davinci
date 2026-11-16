"""Flex: as variações do anúncio da Shopee guardadas inteiras no estado

Eduardo, 16/11/2026: "parece que ativou anúncios de flex de volta que não
eram para estar ativos". O robô ligava e desligava os mesmos 8 anúncios de
kit (dg088/dg106+fone) a cada 1–3 h — 67 ligações desde 14/11. Esses
anúncios têm variações "xx1" sem vínculo no DaVinci (regra: sem Flex). A
importação da Shopee guarda UM modelo por anúncio (`listings` é única por
conta + anúncio; cada modelo grava por cima do anterior), então a regra só
via a "xx1" quando ela era a última gravada.

- flex_anuncio_estado.modelos: a lista inteira do `get_model_list`
  ([{"id", "sku", "estoque"}]); NULL = nunca lida (vale a importação).
- flex_anuncio_estado.modelos_em: quando foi lida.

Nascem NULL: o robô lê na primeira rodada os anúncios que a regra quer
ligados, e relê na hora antes de ligar qualquer um.

Revision ID: 0491_flex_modelos_shopee
Revises: 0490_devolucao_prazo_tiktok_reconsulta
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0491_flex_modelos_shopee"
down_revision: str | None = "0490_devolucao_prazo_tiktok_reconsulta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "flex_anuncio_estado",
        sa.Column("modelos", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "flex_anuncio_estado",
        sa.Column("modelos_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("flex_anuncio_estado", "modelos_em", schema=SCHEMA)
    op.drop_column("flex_anuncio_estado", "modelos", schema=SCHEMA)
