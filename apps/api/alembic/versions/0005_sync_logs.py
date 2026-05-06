"""sync_logs partitioned by month (Fase 4a)

Revision ID: 0005_sync_logs
Revises: 0004_products_links_jobs
Create Date: 2026-05-06

Declarative partitioning on `created_at`. We pre-create the partition for the
current month plus the next two; a cron job (Fase 5 — `sync_logs_partition_gc`)
keeps creating future partitions and dropping old ones.
"""

from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op

revision: str = "0005_sync_logs"
down_revision: Union[str, None] = "0004_products_links_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"

SYNC_LOG_ACTIONS = (
    "refresh_bling",
    "update_stock",
    "update_price",
    "store_status_change",
    "auto_link",
    "test_connection",
)


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    return start, end


def _partition_name(year: int, month: int) -> str:
    return f"sync_logs_y{year:04d}m{month:02d}"


def _create_partition(year: int, month: int) -> None:
    name = _partition_name(year, month)
    start, end = _month_bounds(year, month)
    op.execute(
        f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".{name} '
        f'PARTITION OF "{SCHEMA}".sync_logs '
        f"FOR VALUES FROM ('{start}') TO ('{end}')"
    )


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    sync_log_action_values = ", ".join(f"'{v}'" for v in SYNC_LOG_ACTIONS)
    op.execute(f'CREATE TYPE "{SCHEMA}".sync_log_action AS ENUM ({sync_log_action_values})')

    op.execute(
        f"""
        CREATE TABLE "{SCHEMA}".sync_logs (
            id            UUID         NOT NULL DEFAULT gen_random_uuid(),
            user_id       UUID         NOT NULL REFERENCES "{SCHEMA}".users(id) ON DELETE CASCADE,
            job_id        UUID         NULL     REFERENCES "{SCHEMA}".background_jobs(id) ON DELETE SET NULL,
            product_id    UUID         NULL     REFERENCES "{SCHEMA}".products(id) ON DELETE SET NULL,
            product_link_id UUID       NULL     REFERENCES "{SCHEMA}".product_links(id) ON DELETE SET NULL,
            integration_id  UUID       NULL     REFERENCES "{SCHEMA}".integrations(id) ON DELETE SET NULL,
            store_id      UUID         NULL     REFERENCES "{SCHEMA}".stores(id) ON DELETE SET NULL,
            platform      "{SCHEMA}".integration_platform NULL,
            action        "{SCHEMA}".sync_log_action NOT NULL,
            status        "{SCHEMA}".link_sync_status   NOT NULL,
            qty_before    INTEGER      NULL,
            qty_after     INTEGER      NULL,
            error_code    TEXT         NULL,
            error_detail  TEXT         NULL,
            payload       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )

    op.execute(
        f'CREATE INDEX ix_sync_logs_user_created '
        f'ON "{SCHEMA}".sync_logs (user_id, created_at DESC)'
    )
    op.execute(
        f'CREATE INDEX ix_sync_logs_platform_status_created '
        f'ON "{SCHEMA}".sync_logs (platform, status, created_at DESC)'
    )
    op.execute(
        f'CREATE INDEX ix_sync_logs_product_created '
        f'ON "{SCHEMA}".sync_logs (product_id, created_at DESC)'
    )
    op.execute(
        f'CREATE INDEX ix_sync_logs_job '
        f'ON "{SCHEMA}".sync_logs (job_id) WHERE job_id IS NOT NULL'
    )

    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    for offset in range(3):
        m = month + offset
        y = year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        _create_partition(y, m)


def downgrade() -> None:
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    for offset in range(3):
        m = month + offset
        y = year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        op.execute(f'DROP TABLE IF EXISTS "{SCHEMA}".{_partition_name(y, m)}')

    op.execute(f'DROP TABLE IF EXISTS "{SCHEMA}".sync_logs')
    op.execute(f'DROP TYPE IF EXISTS "{SCHEMA}".sync_log_action')
