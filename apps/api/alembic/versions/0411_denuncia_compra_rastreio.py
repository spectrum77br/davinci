"""denuncia_casos_extra — status e localização do pedido da compra de prova (lidos pelo robô)

Vinicius, 16/10/2026 (aba Casos): "ele precisaria ir nesses casos que ta aguardando produto…
colocar a previsao de entrega e depois da previsao de entrega colocar um campo como observacao ou
localizacao. dai ele pega no pedido a ultima localizacao e status." O passo 1 do robô lê o pedido
na conta compradora do perfil 50 (Shopee e Mercado Livre) e manda pra cá.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0411_denuncia_compra_rastreio"
down_revision: str | None = "0410_denuncia_robo_janela"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
COLUNAS = (
    ("compra_situacao", sa.Text()),
    ("compra_localizacao", sa.Text()),
    ("compra_evento_em", sa.DateTime(timezone=True)),
    ("compra_rastreio", sa.Text()),
    ("compra_previsao_texto", sa.Text()),
    ("compra_entregue_em", sa.Date()),
    ("compra_lido_em", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    for nome, tipo in COLUNAS:
        op.add_column("denuncia_casos_extra", sa.Column(nome, tipo), schema=SCHEMA)


def downgrade() -> None:
    for nome, _ in reversed(COLUNAS):
        op.drop_column("denuncia_casos_extra", nome, schema=SCHEMA)
