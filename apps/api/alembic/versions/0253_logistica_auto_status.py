# ruff: noqa: E501
"""Logística: memória da última situação aplicada pelo robô (`auto_status`) — Eduardo, 2026-09-09.

"esse pedido novamente o status já foi mudado para entregue 3x porque está isso
na plataforma e o nosso sistema está jogando para aguardando" (292291, Shopee):
a regra "Devolução solicitada + Entregue → Aguardando Devolução" reaplicava a
cada rodada porque o estado de partida voltava a casar. Agora a linha guarda a
última transição automática (alvo, origem, assinatura da plataforma, quando) e
o robô só reaplica quando a assinatura da plataforma MUDAR.

Revision ID: 0253_logistica_auto_status
Revises: 0252_devolucao_prazo_contestacao
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0253_logistica_auto_status"
down_revision: str | None = "0252_devolucao_prazo_contestacao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column(
        "logistica",
        sa.Column("auto_status", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_column("logistica", "auto_status")
