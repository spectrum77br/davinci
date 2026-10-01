"""denuncia_robo_tratadas — ocorrência do robô marcada como tratada na aba Robô

Vinicius, 01/10/2026: a caixa vermelha da aba Robô mostrava problemas de
ontem que já tinham se resolvido ("esse vermelho não tô entendendo… igual
fizemos no robô da Ouvidoria"). A aba virou Passos + Ocorrências; o botão
"Tratado" grava a chave aqui e a ocorrência sai da lista. Quando o problema
tem chave própria no robô, o mini também grava a resolução no
_canal/PROBLEMAS.jsonl (comando "resolver").
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0351_denuncia_robo_tratadas"
down_revision: str | None = "0350_denuncia_robo_comandos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "denuncia_robo_tratadas",
        sa.Column("chave", sa.Text(), primary_key=True),
        sa.Column("titulo", sa.Text()),
        sa.Column("tratada_por", sa.Text()),
        sa.Column(
            "tratada_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("denuncia_robo_tratadas", schema=SCHEMA)
