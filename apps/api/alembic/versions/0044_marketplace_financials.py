# ruff: noqa: E501
"""marketplace financial ledger tables

Revision ID: 0044_marketplace_financials
Revises: 0043_store_info_extra_cols
Create Date: 2026-05-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0044_marketplace_financials"
down_revision: str | None = "0043_store_info_extra_cols"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(
        f"""
        CREATE TABLE "{SCHEMA}".marketplace_order_financials (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            platform            "{SCHEMA}".integration_platform NOT NULL,
            integration_id      UUID        NULL REFERENCES "{SCHEMA}".integrations(id) ON DELETE SET NULL,
            store_id            UUID        NULL REFERENCES "{SCHEMA}".stores(id) ON DELETE SET NULL,
            bling_id            BIGINT      NULL,
            pedido_bling        TEXT        NULL,
            external_order_id   TEXT        NOT NULL,
            status              VARCHAR(32) NOT NULL DEFAULT 'pending',
            currency            VARCHAR(8)  NOT NULL DEFAULT 'BRL',
            gross_amount        NUMERIC(14,2) NULL,
            fee_amount          NUMERIC(14,2) NULL,
            freight_amount      NUMERIC(14,2) NULL,
            rebate_amount       NUMERIC(14,2) NULL,
            discount_amount     NUMERIC(14,2) NULL,
            refund_amount       NUMERIC(14,2) NULL,
            tax_amount          NUMERIC(14,2) NULL,
            adjustment_amount   NUMERIC(14,2) NULL,
            net_amount          NUMERIC(14,2) NULL,
            raw                 JSONB       NOT NULL DEFAULT '{{}}'::jsonb,
            fetched_at          TIMESTAMPTZ NULL,
            next_retry_at       TIMESTAMPTZ NULL,
            attempts            INTEGER     NOT NULL DEFAULT 0,
            last_error          TEXT        NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_marketplace_order_financials_platform_integration_order
                UNIQUE (platform, integration_id, external_order_id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE "{SCHEMA}".marketplace_financial_events (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            order_financial_id  UUID        NOT NULL REFERENCES "{SCHEMA}".marketplace_order_financials(id) ON DELETE CASCADE,
            platform            "{SCHEMA}".integration_platform NOT NULL,
            integration_id      UUID        NULL REFERENCES "{SCHEMA}".integrations(id) ON DELETE SET NULL,
            store_id            UUID        NULL REFERENCES "{SCHEMA}".stores(id) ON DELETE SET NULL,
            bling_id            BIGINT      NULL,
            external_order_id   TEXT        NOT NULL,
            event_type          TEXT        NOT NULL,
            amount              NUMERIC(14,2) NOT NULL,
            currency            VARCHAR(8)  NOT NULL DEFAULT 'BRL',
            posted_at           TIMESTAMPTZ NULL,
            settlement_id       TEXT        NULL,
            status              VARCHAR(32) NOT NULL DEFAULT 'posted',
            raw                 JSONB       NOT NULL DEFAULT '{{}}'::jsonb,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f'CREATE INDEX ix_marketplace_order_financials_bling_id '
        f'ON "{SCHEMA}".marketplace_order_financials (bling_id)'
    )
    op.execute(
        f'CREATE INDEX ix_marketplace_order_financials_retry '
        f'ON "{SCHEMA}".marketplace_order_financials (status, next_retry_at) '
        f"WHERE next_retry_at IS NOT NULL"
    )
    op.execute(
        f'CREATE INDEX ix_marketplace_order_financials_store_fetched '
        f'ON "{SCHEMA}".marketplace_order_financials (store_id, fetched_at DESC)'
    )
    op.execute(
        f'CREATE INDEX ix_marketplace_financial_events_order '
        f'ON "{SCHEMA}".marketplace_financial_events (order_financial_id)'
    )
    op.execute(
        f'CREATE INDEX ix_marketplace_financial_events_bling_type '
        f'ON "{SCHEMA}".marketplace_financial_events (bling_id, event_type)'
    )


def downgrade() -> None:
    op.execute(f'DROP TABLE IF EXISTS "{SCHEMA}".marketplace_financial_events')
    op.execute(f'DROP TABLE IF EXISTS "{SCHEMA}".marketplace_order_financials')
