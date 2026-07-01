"""add 'ingest_bling_order' to background_job_type enum

Dá ao webhook de pedido o mesmo registro durável que o webhook de produto já
tem: cada ingest de pedido passa a gravar um BackgroundJob(type=ingest_bling_order),
para que uma falha terminal fique visível (failed_jobs_alert_scan) e seja
re-dirigida pelo cron ingest_orders_retry_sweep — em vez de sumir em silêncio
quando o arq esgota os retries.

Revision ID: 0163_ingest_bling_order_job_type
Revises: 0162_pricing_product_variant_match
Create Date: 2026-07-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0163_ingest_bling_order_job_type"
down_revision: str | None = "0162_pricing_product_variant_match"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f'ALTER TYPE "{SCHEMA}".background_job_type '
        f"ADD VALUE IF NOT EXISTS 'ingest_bling_order'"
    )


def downgrade() -> None:
    # Postgres não suporta remover valor de enum; no-op (espelha 0041).
    pass
