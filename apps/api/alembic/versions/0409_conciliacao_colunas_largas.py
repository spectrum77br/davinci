"""Conciliação — colunas de id/texto do movimento mais largas (16/10/2026).

Na 1ª madrugada com todas as lojas, 6 contas do ML (Poofy, barbosa, vita, marquezini,
mega, forpaper) falharam a ingestão inteira do extrato do Mercado Pago: "value too
long for type character varying(40)" — EXTERNAL_REFERENCE / PRODUCT_SKU / ITEM_ID
dessas contas passam do tamanho. Alarga as colunas (e o leitor corta por segurança).

Revision ID: 0409_conciliacao_colunas_largas
Revises: 0408_conciliacao_todas_as_lojas
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0409_conciliacao_colunas_largas"
down_revision: str | None = "0408_conciliacao_todas_as_lojas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUNAS = {
    "sub_tipo": 120,
    "pagamento_id": 120,
    "pedido_marketplace": 120,
    "pack_id": 120,
    "shipping_id": 120,
    "item_id": 120,
    "sku": 500,
}
_ANTIGAS = {
    "sub_tipo": 40,
    "pagamento_id": 40,
    "pedido_marketplace": 40,
    "pack_id": 40,
    "shipping_id": 40,
    "item_id": 40,
    "sku": 120,
}


def upgrade() -> None:
    for coluna, tamanho in _COLUNAS.items():
        op.alter_column(
            "conciliacao_movimentos",
            coluna,
            type_=sa.String(tamanho),
            existing_type=sa.String(_ANTIGAS[coluna]),
            existing_nullable=True,
            schema="davinci",
        )


def downgrade() -> None:
    for coluna, tamanho in _ANTIGAS.items():
        op.alter_column(
            "conciliacao_movimentos",
            coluna,
            type_=sa.String(tamanho),
            existing_type=sa.String(_COLUNAS[coluna]),
            existing_nullable=True,
            schema="davinci",
        )
