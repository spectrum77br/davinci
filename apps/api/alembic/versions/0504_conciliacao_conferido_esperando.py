"""conciliacao_conferido_esperando — Conferido tira o pedido do "Onde agir" até ter novidade

Vinicius (23/11/2026): "fica esses amarelos do lado como pendência, eu não gosto. Eu
marco como conferido, não era pra sumir?" → "Sim, faz isso". O "Marcar conferido" só
punha um ✓; o cartão amarelo do Onde agir (e o Threema da manhã) continuava cobrando
todo dia pedido que só está esperando (produto voltando, plataforma que vai pagar).

Agora conferido = "já vi, estou esperando": o pedido sai do amarelo e vai pra lista
recolhida "Conferidos, esperando". Volta sozinho quando muda alguma coisa do que a
pessoa viu (situação, etapa, recebido ou custo — `conferido_foto`) ou quando chega a
data `conferido_ate` (padrão: 7 dias).

* `conferido_ate` (date): olhar de novo neste dia.
* `conferido_motivo` (text): o que está esperando, em uma linha (opcional).
* `conferido_foto` (jsonb): situação, etapa, recebido, custo, diferença e último
  movimento no momento do conferido.

Os pedidos em pendência já marcados conferido (2 da ATV em 23/11) ganham a foto de
agora e a volta em 7 dias — somem do amarelo como ele esperava.

Revision ID: 0504_conciliacao_conferido_esperando
Revises: 0503_pricing_ads
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0504_conciliacao_conferido_esperando"
down_revision: str | None = "0503_pricing_ads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "conciliacao_pedidos",
        sa.Column("conferido_ate", sa.Date(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "conciliacao_pedidos",
        sa.Column("conferido_motivo", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "conciliacao_pedidos",
        sa.Column("conferido_foto", JSONB(), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        """
        UPDATE davinci.conciliacao_pedidos
        SET conferido_foto = jsonb_build_object(
                'situacao', situacao,
                'etapa', etapa,
                'recebido', recebido::text,
                'custo', custo::text,
                'dif', dif_previsto_recebido::text,
                'ultimo_movimento_em', ultimo_movimento_em
            ),
            conferido_ate = GREATEST(
                (conferido_em AT TIME ZONE 'America/Sao_Paulo')::date,
                (now() AT TIME ZONE 'America/Sao_Paulo')::date
            ) + 7
        WHERE conferido_em IS NOT NULL
          AND conferido_foto IS NULL
          AND etapa IN ('pendencia', 'reaberto')
        """
    )


def downgrade() -> None:
    op.drop_column("conciliacao_pedidos", "conferido_foto", schema=SCHEMA)
    op.drop_column("conciliacao_pedidos", "conferido_motivo", schema=SCHEMA)
    op.drop_column("conciliacao_pedidos", "conferido_ate", schema=SCHEMA)
