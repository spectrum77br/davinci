# ruff: noqa: E501
"""Logística: carimbo da abertura AUTOMÁTICA de chamado — Eduardo, 2026-09-07.

"em logística lembra da regra de status? de abrir chamado automático? se tá no
status e o status da plataforma está batendo, correto, só que não está abrindo
chamado automático e nem mandando a mensagem no Threema".

Até aqui a aba Status era só o playbook: o motor do recarregar aplicava sozinho
apenas a troca de situação no Bling; abrir chamado e Threema ficavam nos botões.
Agora o motor também abre o chamado (mediação do ML) e manda o Threema. Pra não
martelar a API do ML a cada 5 minutos num pedido que ainda não tem reclamação
do comprador, a linha guarda QUANDO tentou e o motivo da recusa; a próxima
tentativa espera algumas horas. O Threema já tinha o próprio carimbo
(`threema_enviado_at`).

Revision ID: 0250_logistica_chamado_auto
Revises: 0249_pricing_sku_ean_4000
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0250_logistica_chamado_auto"
down_revision: str | None = "0249_pricing_sku_ean_4000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column(
        "logistica", sa.Column("chamado_auto_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("logistica", sa.Column("chamado_auto_erro", sa.Text(), nullable=True))


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_column("logistica", "chamado_auto_erro")
    op.drop_column("logistica", "chamado_auto_at")
