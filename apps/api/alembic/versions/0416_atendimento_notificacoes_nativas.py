"""Estado inicial verificado e leitura nativa por conversa.

Revision ID: 0416_notificacoes_nativas
Revises: 0415_mail_admin
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.config import get_settings

revision = "0416_notificacoes_nativas"
down_revision = "0415_mail_admin"
branch_labels = None
depends_on = None
SCHEMA = get_settings().database_schema


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "atendimento_canais",
        sa.Column(
            "notificacoes_migracao",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "atendimento_conversas",
        sa.Column("notificacoes_corte_em", sa.DateTime(timezone=True)),
        schema=SCHEMA,
    )
    op.add_column(
        "atendimento_conversas",
        sa.Column(
            "notificacoes_iniciais",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "atendimento_conversas",
        sa.Column("notificacoes_visto_em", sa.DateTime(timezone=True)),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    for name in ("notificacoes_visto_em", "notificacoes_iniciais", "notificacoes_corte_em"):
        op.drop_column("atendimento_conversas", name, schema=SCHEMA)
    op.drop_column("atendimento_canais", "notificacoes_migracao", schema=SCHEMA)
