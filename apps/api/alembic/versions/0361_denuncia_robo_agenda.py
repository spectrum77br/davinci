"""denuncia_robo_agenda — liga/desliga e horários de cada passo do robô de denúncia

Vinicius, 02/10/2026 (aba Robô): "colocar um botão igual fizemos no robô dentro da ouvidoria, onde
eu deixaria ligado e desligado… alguma forma de colocar os horários, tipo 6 da manhã roda passo 1, 2
e 3, eles vão esperando vendo a fila… passo 9 quero cadastrar para as 23 horas". Some o horário fixo
das rodadas (06/12/18h, tudo junto): cada passo tem a sua chave e os seus horários, e o despertador
do agente no mini segue esta agenda (vai por comando "agenda" a cada mudança). Uma linha por passo.
Começa como combinado: 0 às 05:45; 1, 2, 3 e 4 às 06:00; 9 às 23:00; 5, 6 e 8 desligados.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0361_denuncia_robo_agenda"
down_revision: str | None = "0360_marketplace_netshoes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

INICIAL = [
    ("checagem", True, ["05:45"]),
    ("ciclo_emails", True, ["06:00"]),
    ("procura", True, ["06:00"]),
    ("denuncias", True, ["06:00"]),
    ("anatel", True, ["06:00"]),
    ("compras", False, []),
    ("juridico", False, []),
    ("capa_perguntas", False, []),
    ("ativos_inativos", True, ["23:00"]),
]


def upgrade() -> None:
    tabela = op.create_table(
        "denuncia_robo_agenda",
        sa.Column("acao", sa.Text(), primary_key=True),
        sa.Column("ligado", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "horarios", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
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
        [{"acao": a, "ligado": lig, "horarios": hs, "atualizado_por": "migração 0361"}
         for a, lig, hs in INICIAL],
    )


def downgrade() -> None:
    op.drop_table("denuncia_robo_agenda", schema=SCHEMA)
