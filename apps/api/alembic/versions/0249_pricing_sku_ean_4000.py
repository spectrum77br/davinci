# ruff: noqa: E501
"""Tabela de preços: SKU e EAN para 4000 caracteres — Eduardo, 2026-09-04.

"agora juntamos e teremos que enxutar... m1 lisa 12+18 vai ficar junto com todos
os modelos m1 m2 m3 m4 m5 m6, os skus vao ficar todos juntos tbm, e os ean tbm,
so tem que aumentar o limite de caracteres".

A aba Produtos do segmento Mala deixa de ter uma linha por modelo e passa a ter
uma por tamanho: M1..M6 viram ABS, P1..P6 viram PP, P7/P8 viram PP (expansor e
roda removível), ME1/ME2 viram Executiva; acessórios ficam como estão. Cada
linha nova carrega os SKUs e os EANs de todos os modelos que absorveu.

Medido na base antes da mudança (soma por grupo + tamanho, pior caso):
  SKU  1.200 caracteres  (limite era 2.048 — cabia, mas sem folga pra crescer)
  EAN  1.840 caracteres  (limite era 1.000 — NÃO cabia, o cadastro seria recusado)
4.000 dá mais de duas vezes o pior caso de hoje e cobre modelo novo entrando no
grupo. O EAN já tinha subido de 60 para 1.000 na 0238, pelo mesmo motivo.

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


# O trigger que espelha variante escuta UPDATE OF sku, então o Postgres recusa
# alterar o tipo da coluna enquanto ele existir ("trigger ... depends on column
# sku"). Derruba e recria igual — a FUNÇÃO pricing_product_variant_sync() não é
# tocada, só o gatilho.
_TRIGGER = "trg_pricing_product_variant_sync"
_CRIA_TRIGGER = f"""
    CREATE TRIGGER {_TRIGGER}
    AFTER INSERT OR DELETE OR UPDATE OF sku, segment_id ON davinci.pricing_products
    FOR EACH ROW EXECUTE FUNCTION pricing_product_variant_sync()
"""


def upgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON davinci.pricing_products")
    op.alter_column(
        "pricing_products",
        "sku",
        existing_type=sa.String(2048),
        type_=sa.String(4000),
        existing_nullable=False,
        schema="davinci",
    )
    op.alter_column(
        "pricing_products",
        "ean",
        existing_type=sa.String(1000),
        type_=sa.String(4000),
        existing_nullable=True,
        schema="davinci",
    )
    op.execute(_CRIA_TRIGGER)


def downgrade() -> None:
    # Só volta se nenhum valor tiver passado do limite antigo — encurtar
    # truncaria SKU/EAN de produto real.
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON davinci.pricing_products")
    op.alter_column(
        "pricing_products",
        "ean",
        existing_type=sa.String(4000),
        type_=sa.String(1000),
        existing_nullable=True,
        schema="davinci",
    )
    op.alter_column(
        "pricing_products",
        "sku",
        existing_type=sa.String(4000),
        type_=sa.String(2048),
        existing_nullable=False,
        schema="davinci",
    )
    op.execute(_CRIA_TRIGGER)
