"""Conciliação — de onde vem o custo do pedido e o custo zerado à mão (22/10).

22/10 (Vinicius): o custo do produto no Sobrou passa a seguir a situação do Bling —
Manutenção (o produto ainda é nosso) e Cancelado zeram, o resto conta, inclusive
Resolvido ("no resolvido acontece de tudo"). Ao finalizar, a pessoa pode zerar o
custo com motivo (produto que voltou pro estoque); o recálculo respeita.

- `custo_regra`: cheio | manutencao | cancelado | manual. Quando muda entre cheio e
  manutencao num pedido finalizado, o pedido reabre ("custo voltou: Bling em
  Perdimento"). Vazio nas linhas antigas: a primeira rodada só preenche, não reabre.
- `custo_zerado_por` / `custo_zerado_em` / `custo_zerado_motivo`: quem zerou à mão.

Revision ID: 0425_conciliacao_custo_zerado
Revises: 0424_mail_pastas_categoria
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0425_conciliacao_custo_zerado"
down_revision: str | None = "0424_mail_pastas_categoria"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conciliacao_pedidos",
        sa.Column("custo_regra", sa.String(20), nullable=True),
        schema="davinci",
    )
    op.add_column(
        "conciliacao_pedidos",
        sa.Column("custo_zerado_por", sa.String(120), nullable=True),
        schema="davinci",
    )
    op.add_column(
        "conciliacao_pedidos",
        sa.Column("custo_zerado_em", sa.DateTime(timezone=True), nullable=True),
        schema="davinci",
    )
    op.add_column(
        "conciliacao_pedidos",
        sa.Column("custo_zerado_motivo", sa.Text(), nullable=True),
        schema="davinci",
    )


def downgrade() -> None:
    for coluna in ("custo_zerado_motivo", "custo_zerado_em", "custo_zerado_por", "custo_regra"):
        op.drop_column("conciliacao_pedidos", coluna, schema="davinci")
