"""Conciliação — dia reaberto com rastro e motivo do fechamento à mão (16/10, revisão).

`reaberto_em/por/motivo`: quem reabriu o dia (pessoa ou robô) e por quê — o robô não
refecha o que a pessoa reabriu, e "linha nova em dia fechado" reabre sozinho com o
motivo. `fechado_motivo`: o botão "Fechar o dia" exige motivo quando o saldo não bate.
DOWNGRADE: tira as quatro colunas.

Revision ID: 0413_conciliacao_dia_reaberto
Revises: 0412_conciliacao_kfa_upload
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0413_conciliacao_dia_reaberto"
down_revision: str | None = "0412_conciliacao_kfa_upload"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conciliacao_dias", sa.Column("reaberto_em", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("conciliacao_dias", sa.Column("reaberto_por", sa.String(120), nullable=True))
    op.add_column("conciliacao_dias", sa.Column("reaberto_motivo", sa.Text(), nullable=True))
    op.add_column("conciliacao_dias", sa.Column("fechado_motivo", sa.Text(), nullable=True))


def downgrade() -> None:
    for col in ("fechado_motivo", "reaberto_motivo", "reaberto_por", "reaberto_em"):
        op.drop_column("conciliacao_dias", col)
