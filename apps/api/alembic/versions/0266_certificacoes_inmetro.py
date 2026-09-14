"""Consulta Inmetro por certificado/modelo e histórico com origem explícita."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0266_certificacoes_inmetro"
down_revision: str | None = "0265_certificacoes_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def _historico():
    return sa.table(
        "certificacoes_sync_historico", sa.column("anatel_numero"),
        sa.column("chave_fonte"), schema=SCHEMA,
    )


def upgrade() -> None:
    for column in (
        sa.Column("inmetro_chave", sa.Text(), nullable=True),
        sa.Column("inmetro_dados", postgresql.JSONB(), nullable=True),
        sa.Column("inmetro_consultado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("inmetro_encontrado", sa.Boolean(), nullable=True),
    ):
        op.add_column("financeiro_suprimentos", column, schema=SCHEMA)
    op.create_unique_constraint(
        "uq_financeiro_suprimentos_inmetro_chave", "financeiro_suprimentos",
        ["inmetro_chave"], schema=SCHEMA,
    )
    op.add_column(
        "certificacoes_sync_historico",
        sa.Column("fonte", sa.Text(), server_default="anatel", nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "certificacoes_sync_historico", sa.Column("chave_fonte", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    historico = _historico()
    op.execute(historico.update().where(historico.c.chave_fonte.is_(None)).values(
        chave_fonte=historico.c.anatel_numero,
    ))
    op.alter_column("certificacoes_sync_historico", "anatel_numero", nullable=True, schema=SCHEMA)


def downgrade() -> None:
    historico = _historico()
    if op.get_bind().scalar(sa.select(sa.exists().where(historico.c.anatel_numero.is_(None)))):
        raise RuntimeError(
            "O histórico Inmetro precisa ser preservado antes de reverter a migração."
        )
    op.alter_column("certificacoes_sync_historico", "anatel_numero", nullable=False, schema=SCHEMA)
    op.drop_column("certificacoes_sync_historico", "chave_fonte", schema=SCHEMA)
    op.drop_column("certificacoes_sync_historico", "fonte", schema=SCHEMA)
    op.drop_constraint(
        "uq_financeiro_suprimentos_inmetro_chave", "financeiro_suprimentos",
        type_="unique", schema=SCHEMA,
    )
    for name in ("inmetro_encontrado", "inmetro_consultado_em", "inmetro_dados", "inmetro_chave"):
        op.drop_column("financeiro_suprimentos", name, schema=SCHEMA)
