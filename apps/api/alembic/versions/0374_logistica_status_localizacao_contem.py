"""logistica_status.localizacao_contem — condição da regra pela Localização

Vinicius, 06/10/2026: pacote "Objeto apreendido por órgão de fiscalização"
(ML 300064) seguia "Pago | Enviado" no marketplace e sumia do painel junto com
os pedidos normais dessa chave. A regra da aba Status ganha uma condição
opcional: palavras (separadas por ";") que precisam aparecer na Localização do
pedido. Regra com condição que casa passa na frente das regras sem condição da
mesma chave. Coluna nova vazia = nenhuma regra muda de comportamento.

Revision ID: 0374_logistica_status_localizacao_contem
Revises: 0373_denuncia_relatorio_threema
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0374_logistica_status_localizacao_contem"
down_revision: str | None = "0373_denuncia_relatorio_threema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "logistica_status",
        sa.Column("localizacao_contem", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("logistica_status", "localizacao_contem", schema=SCHEMA)
