"""logistica.status_lido_em — quando o Status Plataforma foi LIDO pela última vez

Eduardo, 15/09/2026: "não está atualizando 100% automático". A coluna mostra a
data em que o status MUDOU (status_datas), não quando foi consultado — sem
esse carimbo não dá pra provar que o motor passou. Gravado por todo enrich
(ML/Shopee/TikTok/Amazon) e pelos sweeps de pós-venda que atualizam a
assinatura; a tela mostra "lido há X min" embaixo do status.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0274_logistica_status_lido_em"
down_revision: str | None = "0273_logistica_amazon_canal_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "logistica",
        sa.Column("status_lido_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("logistica", "status_lido_em", schema=SCHEMA)
