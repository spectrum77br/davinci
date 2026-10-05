"""denuncia_relatorios — o relatório do dia do robô de denúncia

Vinicius, 05/10/2026: "precisamos criar… um relatório no final do dia, mostrando quantos anúncios
ele achou, quantos denunciou na loja, quantos abriu reclamação na Anatel" — no DaVinci, em Robô ›
Ocorrências, dia do calendário (0h–24h) e com Excel. Um por dia: `anotacoes` vai sendo preenchida a
cada resumo do mini (passos, ocorrências, minutos sem notícia); `numeros` são as contas da cópia do
banco, congeladas depois da meia-noite (`fechado_em`). `lido_em` tira o relatório das Ocorrências.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0364_denuncia_relatorios"
down_revision: str | None = "0363_denuncia_robo_agenda_diversos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "denuncia_relatorios",
        sa.Column("dia", sa.Date(), primary_key=True),
        sa.Column(
            "anotacoes", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("numeros", postgresql.JSONB()),
        sa.Column("fechado_em", sa.DateTime(timezone=True)),
        sa.Column("lido_em", sa.DateTime(timezone=True)),
        sa.Column("lido_por", sa.Text()),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("denuncia_relatorios", schema=SCHEMA)
