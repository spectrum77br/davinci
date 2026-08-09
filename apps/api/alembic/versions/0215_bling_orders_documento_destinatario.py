# ruff: noqa: E501
"""bling_orders.documento_destinatario: CPF/CNPJ do cliente do pedido.

O `contato.numeroDocumento` já chega no payload do Bling (o ingest usa
`get_order`, que devolve o pedido completo) mas era descartado — o davinci
não guardava o documento em lugar nenhum. Persistir aqui serve dois donos:

1. o casador de etiquetas em lote (`nf_etiqueta_lote`), que precisa casar a
   fatia do PDF pelo CPF impresso na declaração de conteúdo — chave bem mais
   específica que nome + SKU;
2. a planilha do Upseller (`nf_emissao_gerar`), que hoje faz um `get_order`
   por pedido só pra descobrir o documento.

NÃO reusar `numero_documento`: essa coluna guarda `numeroPedidoCompra` e é
lida por `marketplace_financials._external_order_id` como fallback do
`numeroloja` (encher de CPF viraria busca de settlement por CPF).
NULL = pedido antigo/sem contato.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0215_bling_orders_documento_destinatario"
down_revision: str | None = "0214_simulacao_lote_di"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "bling_orders",
        sa.Column("documento_destinatario", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("bling_orders", "documento_destinatario", schema=SCHEMA)
