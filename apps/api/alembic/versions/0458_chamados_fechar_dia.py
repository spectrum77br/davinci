"""Chamados: Fechar dia no painel Meses

Cairo (06/11/2026): em 05/11 ele conferiu tudo, saiu, e os chamados abertos depois
deixaram o dia como "falta conferir 6". O botão Fechar dia grava quem encerrou e
quando; o dia fica verde e o número segue na coluna Pendentes.

Ele pediu o 05/11 já fechado na hora da última atualização dele. Não dá pelo botão,
que grava a hora do clique: vai aqui, com a última marca do dia (16:32:53, pedido
299783; ele lembrava "acho que 16:40").

Revision ID: 0458_chamados_fechar_dia
Revises: 0457_pricing_certificacao
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0458_chamados_fechar_dia"
down_revision: str | None = "0457_pricing_certificacao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def _fechar_05_11(bind, schema: str = SCHEMA) -> int:
    r = bind.execute(
        sa.text(
            f"UPDATE {schema}.chamado_revisao_dias SET "  # noqa: S608
            "  fechado_at = TIMESTAMPTZ '2026-11-05 16:32:53-03', "
            "  fechado_por_nome = 'cairo sa', "
            f"  fechado_por_id = (SELECT id FROM {schema}.users "
            "    WHERE email = 'sa.geral@tutamail.com') "
            "WHERE dia = DATE '2026-11-05' AND fechado_at IS NULL"
        )
    )
    return r.rowcount


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "chamado_revisao_dias",
        sa.Column("fechado_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "chamado_revisao_dias",
        sa.Column("fechado_por_nome", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "chamado_revisao_dias",
        sa.Column(
            "fechado_por_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    print(f"0458: dia 05/11 fechado ({_fechar_05_11(op.get_bind())} linha)")


def downgrade() -> None:
    op.drop_column("chamado_revisao_dias", "fechado_por_id", schema=SCHEMA)
    op.drop_column("chamado_revisao_dias", "fechado_por_nome", schema=SCHEMA)
    op.drop_column("chamado_revisao_dias", "fechado_at", schema=SCHEMA)
