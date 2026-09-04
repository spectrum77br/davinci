# ruff: noqa: E501
"""Tabela de preços: EAN para 4000 caracteres — Eduardo, 2026-09-04.

"agora juntamos e teremos que enxutar... m1 lisa 12+18 vai ficar junto com todos
os modelos m1 m2 m3 m4 m5 m6, os skus vao ficar todos juntos tbm, e os ean tbm,
so tem que aumentar o limite de caracteres".

A aba Produtos do segmento Mala deixa de ter uma linha por modelo e passa a ter
uma por tamanho: M1..M6 viram ABS, P1..P6 viram PP, P7/P8 viram PP (expansor e
roda removível), ME1/ME2 viram Executiva; acessórios ficam como estão. Cada
linha nova carrega os SKUs e os EANs de todos os modelos que absorveu.

Medido na base antes da mudança (soma por grupo + tamanho, pior caso):
  SKU  1.200 caracteres  — cabe nos 2.048 atuais, fica como está
  EAN  1.840 caracteres  — NÃO cabia nos 1.000, o cadastro seria recusado

Só o EAN muda. Alargar o `sku` exigiria derrubar e recriar a
vw_conciliacao_margens_marketplace (o Postgres recusa ALTER TYPE numa coluna que
uma view referencia), e essa view é a base do cálculo de margem — risco alto
para um campo que ainda tem 40% de folga. Quando faltar, aí sim vale o trabalho
de recriar a view junto. O EAN já tinha subido de 60 para 1.000 na 0238.

Revision ID: 0249_pricing_sku_ean_4000
Revises: 0248_chamado_juridico
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0249_pricing_sku_ean_4000"
down_revision: str | None = "0248_chamado_juridico"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "pricing_products",
        "ean",
        existing_type=sa.String(1000),
        type_=sa.String(4000),
        existing_nullable=True,
        schema="davinci",
    )


def downgrade() -> None:
    # Só volta se nenhum EAN tiver passado de 1.000 — encurtar truncaria
    # código de barras de produto real.
    op.alter_column(
        "pricing_products",
        "ean",
        existing_type=sa.String(4000),
        type_=sa.String(1000),
        existing_nullable=True,
        schema="davinci",
    )
