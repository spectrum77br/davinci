"""denuncia_robo_config — janela da noite do robô de denúncia (início e fim)

Vinicius, 16/10/2026: "precisamos criar alguma regra horario inicio e fim. agora ele ja devia estar
parado para o robo de chamado funcionar" — o Ativos/inativos começou 02:23 e às 07:50 ocupava o
perfil 50. A agenda só tinha hora de começar. Tabela de configuração por chave; começa com a janela
`janela_noite` = 18:00 → 07:00, ligada (escolha dele, editável na aba Robô).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0410_denuncia_robo_janela"
down_revision: str | None = "0409_conciliacao_colunas_largas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    tabela = op.create_table(
        "denuncia_robo_config",
        sa.Column("chave", sa.Text(), primary_key=True),
        sa.Column(
            "valor", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("atualizado_por", sa.Text()),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.bulk_insert(
        tabela,
        [{"chave": "janela_noite", "valor": {"ligado": True, "inicio": "18:00", "fim": "07:00"},
          "atualizado_por": "migração 0410"}],
    )


def downgrade() -> None:
    op.drop_table("denuncia_robo_config", schema=SCHEMA)
