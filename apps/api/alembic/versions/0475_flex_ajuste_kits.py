"""Flex: "Kits entram" passa a ser trocado na tela (flex_ajuste.kits)

Vinicius, 12/11/2026: na Shopee quase todo celular é vendido em kit (celular +
fone/relógio/AirTag) — 148 dos 206 anúncios de celular das lojas prontas — e o
`flex_kits` do .env (só o Eduardo mexe) deixava todos fora: "os kit tem que
entrar, desde que tenha tudo em .sp do kit". O motor já trata o kit (o saldo é
o do kit .sp no Bling, que é o da peça mais escassa; sem kit .sp cadastrado
nunca liga) — falta a chave na tela, junto das outras três (0474).

- flex_ajuste.kits: NULL = vale o .env. Nasce NULL: nada muda no deploy.

Revision ID: 0475_flex_ajuste_kits
Revises: 0474_flex_ajuste
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0475_flex_ajuste_kits"
down_revision: str | None = "0474_flex_ajuste"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column("flex_ajuste", sa.Column("kits", sa.Boolean(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("flex_ajuste", "kits", schema=SCHEMA)
