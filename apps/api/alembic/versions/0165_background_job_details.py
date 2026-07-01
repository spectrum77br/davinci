"""background_job_details — progresso de job fora da linha quente.

O array JSONB `background_jobs.details` virou quadrático no "Sincronizar Todos":
cada append reescrevia o array inteiro (~800KB no fim) sob row-lock, e as 8
sub-tarefas paralelas (SYNC_ALL_CONCURRENCY) batiam todas na MESMA linha do job
→ serializavam no lock e a tela congelava. Cada entrada de progresso agora vira
UMA linha barata nesta tabela filha: append O(1), sem reescrita, sem contenção
entre tarefas; e a leitura pagina a cauda por `id` (cursor) em vez de mandar o
array inteiro a cada poll.

A coluna `background_jobs.details` é mantida (jobs antigos), mas os writers
(sync_orchestrator, auto_link, refresh_bling_stock) param de escrever nela — a
leitura nova vem daqui.

Revision ID: 0165_background_job_details
Revises: 0164_kit_celular_backfill_missing_bases
Create Date: 2026-07-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0165_background_job_details"
down_revision = "0164_kit_celular_backfill_missing_bases"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "background_job_details",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("entry", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            [f"{SCHEMA}.background_jobs.id"],
            name="fk_background_job_details_job_id_background_jobs",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    # (job_id, id): lookups por job + slice incremental (id > after ORDER BY id).
    op.create_index(
        "ix_background_job_details_job_id_id",
        "background_job_details",
        ["job_id", "id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_background_job_details_job_id_id",
        table_name="background_job_details",
        schema=SCHEMA,
    )
    op.drop_table("background_job_details", schema=SCHEMA)
