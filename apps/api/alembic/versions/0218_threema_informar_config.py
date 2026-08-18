"""threema_informar_config: cadastro de destinatários dos botões INFORMAR.

Botões admin-only na Logística e no Controle de Estoque que mandam um resumo
via Threema (pedidos acompanhados / pedidos em Aguardando Cancelamento por
falta de estoque). Uma linha por contexto; `recipients` = IDs Threema em CSV,
escolhidos no modal a partir do diretório do `.env`.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0218_threema_informar_config"
down_revision: str | None = "0217_logistica_status_desconsiderar"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "threema_informar_config",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("contexto", sa.Text(), nullable=False),
        sa.Column("recipients", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("contexto", name="uq_threema_informar_config_contexto"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("threema_informar_config", schema=SCHEMA)
