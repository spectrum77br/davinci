# ruff: noqa: E501
"""Segmentos: "Datas Especiais" vira "Condição Especial" (período opcional + nome/SKU).

Pedido de 10/09/2026: além do período, poder abrir exceção de margem por
produto — "todo anúncio que tiver M3 no nome pode aprovar até 6%" / "todo SKU
a001 pode aprovar com 6%". A tabela `segment_special_dates` continua a mesma
(regra por segmento, vale para os subsegmentos); ganha `nome_contem` e
`sku_prefixo`, e o período passa a ser opcional. Uma condição precisa ter
pelo menos uma das três partes (CHECK), e as datas vêm em par.

Revision ID: 0256_segment_condicao_especial
Revises: 0255_devolucao_observacao
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0256_segment_condicao_especial"
down_revision: str | None = "0255_devolucao_observacao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABLE = "segment_special_dates"
CK_CONDICAO = "ck_segment_special_dates_condicao"
CK_PERIODO = "ck_segment_special_dates_periodo_par"
# A naming convention do metadata ("ck_%(table_name)s_%(constraint_name)s")
# prefixa de novo o nome passado ao create_check_constraint — o nome que fica
# no banco é este (visto em produção, 10/09); o downgrade tem que usá-lo.
CK_CONDICAO_EFETIVO = f"ck_{TABLE}_{CK_CONDICAO}"
CK_PERIODO_EFETIVO = f"ck_{TABLE}_{CK_PERIODO}"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column(TABLE, sa.Column("nome_contem", sa.Text(), nullable=True))
    op.add_column(TABLE, sa.Column("sku_prefixo", sa.Text(), nullable=True))
    op.alter_column(TABLE, "date_start", existing_type=sa.Date(), nullable=True)
    op.alter_column(TABLE, "date_end", existing_type=sa.Date(), nullable=True)
    # Pelo menos uma condição; datas sempre em par.
    op.create_check_constraint(
        CK_CONDICAO,
        TABLE,
        "date_start IS NOT NULL OR nome_contem IS NOT NULL OR sku_prefixo IS NOT NULL",
    )
    op.create_check_constraint(
        CK_PERIODO,
        TABLE,
        "(date_start IS NULL) = (date_end IS NULL)",
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_constraint(CK_PERIODO_EFETIVO, TABLE, type_="check")
    op.drop_constraint(CK_CONDICAO_EFETIVO, TABLE, type_="check")
    # Condições só por nome/SKU não existem no modelo antigo: saem.
    op.execute(f"DELETE FROM {TABLE} WHERE date_start IS NULL OR date_end IS NULL")  # noqa: S608 — nome de tabela constante
    op.alter_column(TABLE, "date_end", existing_type=sa.Date(), nullable=False)
    op.alter_column(TABLE, "date_start", existing_type=sa.Date(), nullable=False)
    op.drop_column(TABLE, "sku_prefixo")
    op.drop_column(TABLE, "nome_contem")
