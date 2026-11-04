"""Preserva os campos de certificações preenchidos ou corrigidos manualmente.

Revision ID: 0449_certificacoes_edicao_manual
Revises: 0448_marketing_lives
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0449_certificacoes_edicao_manual"
down_revision = "0448_marketing_lives"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column(
        "financeiro_suprimentos", sa.Column("campos_manuais", pg.JSONB(), nullable=True),
        schema="davinci",
    )
    # Cadastros ainda manuais já continham valores digitados antes desta versão.
    op.execute("""
        UPDATE davinci.financeiro_suprimentos AS s
        SET campos_manuais = (
            SELECT jsonb_agg(campo ORDER BY campo)
            FROM jsonb_each(to_jsonb(s)) AS dados(campo, valor)
            WHERE campo IN (
                'produto', 'modelo', 'nome_comercial', 'certificado',
                'numero', 'valor', 'inicio', 'fim'
            ) AND valor <> 'null'::jsonb AND valor <> '\"\"'::jsonb
        )
        WHERE anatel_numero IS NULL AND inmetro_chave IS NULL
    """)


def downgrade() -> None:
    op.drop_column("financeiro_suprimentos", "campos_manuais", schema="davinci")
