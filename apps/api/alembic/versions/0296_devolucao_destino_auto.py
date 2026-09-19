"""devolucao_rastreio.devolucao_destino_auto — pra onde vai a perna da devolução

Vinicius, 19/09/2026: pedidos ML 2000014922944893, 2000014722914581,
2000018302595310 e 2000014729924813 apareciam na tela Devoluções com
"Chegou em 18/09" enquanto o ML dizia "Devolução a caminho. Vamos revisar o
produto". O ML (`intermediate_check`) manda o pacote primeiro pro galpão dele
em Cajamar (`shipments[].destination.name = warehouse`) e só depois cria a
perna pra loja (`return_from_triage` → `seller_address`, com OUTRO rastreio).
"Entregue" na perna do galpão — no status do return, no 17track ou no pull
dos Correios do rastreio dessa perna — é o pacote no Mercado Livre, não aqui.

- `devolucao_destino_auto` (text, nulo): "warehouse" | "seller_address" |
  NULL (plataforma não separa ou ainda não sincronizou). O sync grava a cada
  rodada; com "warehouse" o rastreio não carimba `pacote_entregue_em` e o
  status "delivered" não vira "Chegou em".
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0296_devolucao_destino_auto"
down_revision: str | None = "0295_estoque_pedido_video"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "devolucao_rastreio",
        sa.Column("devolucao_destino_auto", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("devolucao_rastreio", "devolucao_destino_auto", schema=SCHEMA)
