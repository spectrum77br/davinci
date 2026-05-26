# ruff: noqa: E501
"""Merge: 0094_cotacao_tables + 0094_bling_orders_endereco_destino.

Duas branches divergiram em 0093_merge_bling_sync_devolucoes:
  * 0094_cotacao_tables (cotação tab)
  * 0094_bling_orders_endereco_destino (endereço destino em bling_orders)

Merge estrutural — sem DDL.

Revision ID: 0095_merge_cotacao_endereco
Revises: 0094_cotacao_tables, 0094_bling_orders_endereco_destino
Create Date: 2026-05-26
"""

from collections.abc import Sequence

revision: str = "0095_merge_cotacao_endereco"
down_revision: tuple[str, ...] = (
    "0094_cotacao_tables",
    "0094_bling_orders_endereco_destino",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
