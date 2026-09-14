"""Consulta automática das certificações Anatel da Makisa, com histórico."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0265_certificacoes_sync"
down_revision: str | None = "0264_suprimentos_pdf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    for column in (
        sa.Column("anatel_numero", sa.Text(), nullable=True),
        sa.Column("anatel_dados", postgresql.JSONB(), nullable=True),
        sa.Column("anatel_consultado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anatel_encontrado", sa.Boolean(), nullable=True),
    ):
        op.add_column("financeiro_suprimentos", column, schema=SCHEMA)
    op.create_unique_constraint(
        "uq_financeiro_suprimentos_anatel_numero",
        "financeiro_suprimentos",
        ["anatel_numero"],
        schema=SCHEMA,
    )
    op.create_table(
        "certificacoes_sync_state",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("ultima_tentativa_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_sucesso_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proxima_tentativa_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_sha256", sa.Text(), nullable=True),
        sa.Column("resumo", postgresql.JSONB(), nullable=True),
        schema=SCHEMA,
    )
    op.create_table(
        "certificacoes_sync_historico",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "suprimento_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.financeiro_suprimentos.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("anatel_numero", sa.Text(), nullable=False),
        sa.Column("ocorrido_em", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("evento", sa.Text(), nullable=False),
        sa.Column("antes", postgresql.JSONB(), nullable=True),
        sa.Column("depois", postgresql.JSONB(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_certificacoes_sync_historico_suprimento_id", "certificacoes_sync_historico",
        ["suprimento_id"], schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("certificacoes_sync_historico", schema=SCHEMA)
    op.drop_table("certificacoes_sync_state", schema=SCHEMA)
    op.drop_constraint(
        "uq_financeiro_suprimentos_anatel_numero", "financeiro_suprimentos",
        type_="unique", schema=SCHEMA,
    )
    for name in ("anatel_encontrado", "anatel_consultado_em", "anatel_dados", "anatel_numero"):
        op.drop_column("financeiro_suprimentos", name, schema=SCHEMA)
