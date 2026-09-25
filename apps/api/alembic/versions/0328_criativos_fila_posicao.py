"""marketing_creatives.fila_posicao — o vídeo que fura a fila do robô

Eduardo, 25/09/2026: "em criativos deveria dar pra ordenar a sequencia dos
videos pq dai eu ia querer um video mais antigo rodasse antes".

Até aqui a ordem do robô de autopostagem era uma só: o aprovado MAIS RECENTE
que ainda não saiu na conta ("tacamos do mais recente"). Isso continua sendo o
padrão. Esta coluna é a exceção escolhida à mão:

  NULL   ordem normal, pela data (a imensa maioria)
  1, 2…  furou a fila: sai ANTES de qualquer NULL, o menor primeiro

Nasce NULL em tudo — no dia do deploy o robô escolhe exatamente o que
escolhia ontem. O índice (marca_id, fila_posicao) é o caminho da pergunta
que o robô e a tela fazem: "a fila desta marca".

Revision ID: 0328_criativos_fila_posicao
Revises: 0327_chamados_consulta_portal
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0328_criativos_fila_posicao"
down_revision: str | None = "0327_chamados_consulta_portal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "marketing_creatives",
        sa.Column("fila_posicao", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_creatives_marca_fila",
        "marketing_creatives",
        ["marca_id", "fila_posicao"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_marketing_creatives_marca_fila", "marketing_creatives", schema=SCHEMA)
    op.drop_column("marketing_creatives", "fila_posicao", schema=SCHEMA)
