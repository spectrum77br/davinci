"""Flex: as contas em que o robô mexe passam a ser escolhidas na tela
(flex_ajuste.contas)

Vinicius, 12/11/2026: a conta atlas do Mercado Livre foi ligada no DaVinci para
fazer "a mesma coisa que na Shopee", mas a lista de contas do Flex
(`flex_contas` do .env) só o Eduardo mexe — "Sim, na tela". Mesma ideia das
outras chaves (0474, 0475): admin e o gerente trocam na aba Flex.

- flex_ajuste.contas: integration_id das contas em que o robô mexe. NULL = vale
  o .env. Nasce NULL: nada muda no deploy.

Revision ID: 0477_flex_ajuste_contas
Revises: 0476_mail_usuarios_pastas
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0477_flex_ajuste_contas"
down_revision: str | None = "0476_mail_usuarios_pastas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "flex_ajuste",
        sa.Column("contas", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("flex_ajuste", "contas", schema=SCHEMA)
