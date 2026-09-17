"""marcas.dm_contexto — o que o robô de DM pode afirmar sobre cada marca

Eduardo, 17/09/2026: "não é só fazer ele responder quando recebe do hook lá".

É o contexto que o modelo lê pra escrever a resposta — não é a resposta
pronta. Fica por marca porque cada uma afirma coisas diferentes: a Charlots
pode falar de fechadura TSA e casco rígido; a Uranyx não pode afirmar bateria
nem Anatel, que ninguém verificou.

O que NÃO entra aqui, e é regra, não estilo: preço, prazo de entrega, frete e
prazo de garantia. O que se diz em canal de atendimento vincula o fornecedor
(CDC art. 30 e 35) — e o validador em `services/dm_ia.py` recusa a resposta
que contiver qualquer um deles, mande o contexto o que mandar.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0290_dm_contexto_marca"
down_revision: str | None = "0289_devolucao_reembolso"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "marcas", sa.Column("dm_contexto", sa.Text(), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_column("marcas", "dm_contexto", schema=SCHEMA)
