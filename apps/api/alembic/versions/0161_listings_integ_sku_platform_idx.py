# ruff: noqa: E501, S608
"""Índice em listings(integration_id, sku, platform, updated_at) p/ o listing_match

A view de margem (vw_conciliacao_margens_marketplace / ln) faz, para CADA item,
um LATERAL em `listings` filtrando por (integration_id, sku, platform) e ordenando
por updated_at DESC LIMIT 1 (o "listing_match"). Não existia índice cobrindo essas
colunas — só (user_id, sku) — então o planner fazia scan + sort por linha. No
EXPLAIN ANALYZE do rebuild completo esse LATERAL aparecia rodando ~4.5k vezes
(~57s do tempo total). Este índice o transforma em index scan (sem sort).

Índice é semanticamente transparente: NÃO muda qual linha é retornada (mesma
ORDER BY updated_at DESC LIMIT 1), só acelera a busca. Margem/faturamento/
valuation/refunds/devoluções não mudam de resultado.

IF NOT EXISTS porque o índice já foi criado ad-hoc (CONCURRENTLY) em prod durante
o diagnóstico do incidente 30/06; aqui só formaliza no histórico de migrations.

Revision ID: 0161_listings_integ_sku_platform_idx
Revises: 0160_refunds_conferido_at
Create Date: 2026-06-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0161_listings_integ_sku_platform_idx"
down_revision: str | None = "0160_refunds_conferido_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
INDEX = "ix_listings_integ_sku_platform"


def upgrade() -> None:
    op.execute(
        f'CREATE INDEX IF NOT EXISTS {INDEX} '
        f'ON "{SCHEMA}".listings (integration_id, sku, platform, updated_at DESC NULLS LAST)'
    )


def downgrade() -> None:
    op.execute(f'DROP INDEX IF EXISTS "{SCHEMA}".{INDEX}')
