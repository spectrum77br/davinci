# ruff: noqa: E501
"""logistica: carimbo da Mensagem Bling (qual regra já escreveu nas Observações)

Vinicius, 13/10/2026, Amazon 701-7714736-5237839 (Bling 303203): a regra
"Cancelado" + "Em aberto" mandou o Threema e mudou o pedido pra Cancelado, mas
nada ficou escrito no pedido. A Mensagem Bling da regra só rodava pelo botão;
agora o motor escreve sozinho, uma vez por regra, ANTES da troca de status.

  • `mensagem_bling_regra_id` UUID — a regra da aba Status cuja Mensagem Bling
    já foi escrita no pedido (pelo motor ou pelo botão). Por REGRA e não um
    carimbo só (como o `threema_enviado_at`): a mesma linha pode passar por
    duas regras com mensagem (Shopee "Devolução solicitada" e depois
    "Concluído") e cada uma escreve a sua. Sem FK: regra apagada não apaga o
    histórico da linha.
  • `mensagem_bling_at` TIMESTAMPTZ — quando foi escrita.

Só colunas novas e anuláveis, sem padrão: o ADD COLUMN não reescreve a tabela.

Revision ID: 0394_logistica_mensagem_bling_feita
Revises: 0393_criativos_duracao_ia
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0394_logistica_mensagem_bling_feita"
down_revision: str | None = "0393_criativos_duracao_ia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "logistica",
        sa.Column("mensagem_bling_regra_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "logistica",
        sa.Column("mensagem_bling_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("logistica", "mensagem_bling_at", schema=SCHEMA)
    op.drop_column("logistica", "mensagem_bling_regra_id", schema=SCHEMA)
