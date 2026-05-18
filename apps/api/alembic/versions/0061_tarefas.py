# ruff: noqa: E501
"""create tarefas table

Admin assigns tasks to users via responsavel_id. Admin sees everything,
regular users see only their own (filtered in the router). Spec said
`integer` for the user FKs, but users.id is UUID in this codebase so we
follow the convention.

Revision ID: 0061_tarefas
Revises: 0060_vw_conciliacao_margens_freight_coalesce
Create Date: 2026-05-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0061_tarefas"
down_revision: str | None = "0060_vw_conciliacao_margens_freight_coalesce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "tarefas",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        # RESTRICT: tarefas are work assignments, not user-owned data — deleting
        # an active user should force an admin reassignment, not silent loss.
        sa.Column(
            "responsavel_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("data_inicio", sa.Date(), nullable=False),
        sa.Column("data_conclusao", sa.Date(), nullable=True),
        sa.Column("tarefa", sa.Text(), nullable=False),
        sa.Column("observacao", sa.Text(), nullable=True),
        # SET NULL so a deleted admin doesn't break their old assignments.
        sa.Column(
            "created_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_tarefas_responsavel_id", "tarefas", ["responsavel_id"], schema=SCHEMA)
    # Default ordering: pending (data_conclusao IS NULL) first, then by data_inicio DESC.
    op.create_index("ix_tarefas_pending_first", "tarefas", ["data_conclusao", "data_inicio"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_tarefas_pending_first", table_name="tarefas", schema=SCHEMA)
    op.drop_index("ix_tarefas_responsavel_id", table_name="tarefas", schema=SCHEMA)
    op.drop_table("tarefas", schema=SCHEMA)
