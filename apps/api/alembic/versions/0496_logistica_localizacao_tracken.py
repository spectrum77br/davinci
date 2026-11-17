"""Flex: a regra "Localização contém" olha o texto da coluna Tracken

Vinicius, 17/11/2026 (Shopee Flex 309413): a regra "Shopee Flex · Processado
· Em digitação · Localização contém coletado → Em andamento" não casava. Ela
olhava a Localização da Shopee (que a aba Flex nem mostra); o "COLETADO" estava
na coluna Tracken (a transportadora do Flex).

- logistica.localizacao_tracken: "status · último evento" do Tracken — o que
  a coluna mostra. É a Localização das regras para pedido Flex
  (`logistica_match.localizacao_da_linha`). Gravada por `tracken.gravar` a cada
  leitura; NULL = a transportadora ainda não tem o pedido.

Preenche agora os pedidos já lidos, com a mesma conta de
`tracken.localizacao_para_regra`.

Revision ID: 0496_logistica_localizacao_tracken
Revises: 0495_conciliacao_temu
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0496_logistica_localizacao_tracken"
down_revision: str | None = "0495_conciliacao_temu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "logistica",
        sa.Column("localizacao_tracken", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.logistica AS l
           SET localizacao_tracken = NULLIF(
                 concat_ws(' · ', NULLIF(btrim(t.status), ''), NULLIF(btrim(t.ultimo_evento), '')),
                 ''
               )
          FROM {SCHEMA}.logistica_tracken AS t
         WHERE t.logistica_id = l.id
           AND t.achado
        """
    )


def downgrade() -> None:
    op.drop_column("logistica", "localizacao_tracken", schema=SCHEMA)
