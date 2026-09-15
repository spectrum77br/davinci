"""logistica — Amazon: canal (DBA × Envio próprio), datas de entrega, avisos e
mensagens ao cliente

Vinicius, 15/09/2026 ("projeto Amazon"): a aba Amazon do painel Logística passa
a separar Delivery by Amazon (DBA) de Envio próprio, a mostrar a previsão de
entrega dos Correios (objeto de postagem do Bling) e a data máxima de entrega
da Amazon (LatestDeliveryDate da SP-API), a avisar no Threema 3 dias antes do
prazo e a mandar e-mail ao cliente (endereço de retransmissão da Amazon) em
problema, atraso e entrega.

Colunas novas em `logistica`:
- amazon_canal: 'dba' | 'proprio' | 'fba' (classificação persistida).
- servico_envio / postagem_data / previsao_correios: objeto de postagem do Bling.
- prazo_entrega_amazon: LatestDeliveryDate (data em Brasília).
- entregue_em: 17track "Delivered" (Correios) ou EasyShip "Delivered".
- problema_correios / problema_correios_em: ocorrência grave dos Correios.
- cliente_nome / cliente_email: contato do Bling (e-mail relay da Amazon).
- aviso_*_at: carimbos dos avisos Threema (um por pedido).
- bling_enriquecido_em: última leitura do objeto de postagem no Bling.

Tabelas novas: `logistica_mensagem_cliente` (histórico do que foi mandado ao
cliente, um por pedido × evento) e `logistica_mensagem_template` (textos
editáveis na tela).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0272_logistica_amazon_prazos_mensagens"
down_revision: str | None = "0271_situacao_bling_lista_manual"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

def _colunas() -> list[sa.Column]:
    return [
    sa.Column("amazon_canal", sa.Text(), nullable=True),
    sa.Column("servico_envio", sa.Text(), nullable=True),
    sa.Column("postagem_data", sa.Date(), nullable=True),
    sa.Column("previsao_correios", sa.Date(), nullable=True),
    sa.Column("prazo_entrega_amazon", sa.Date(), nullable=True),
    sa.Column("entregue_em", sa.DateTime(timezone=True), nullable=True),
    sa.Column("problema_correios", sa.Text(), nullable=True),
    sa.Column("problema_correios_em", sa.DateTime(timezone=True), nullable=True),
    sa.Column("cliente_nome", sa.Text(), nullable=True),
    sa.Column("cliente_email", sa.Text(), nullable=True),
    sa.Column("aviso_previsao_correios_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("aviso_prazo_amazon_3d_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("aviso_prazo_amazon_vencido_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("bling_enriquecido_em", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    for col in _colunas():
        op.add_column("logistica", col, schema=SCHEMA)
    op.create_index(
        "ix_logistica_amazon_canal", "logistica", ["amazon_canal"], schema=SCHEMA
    )

    op.create_table(
        "logistica_mensagem_template",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("evento", sa.Text(), nullable=False),
        sa.Column("assunto", sa.Text(), nullable=False, server_default=""),
        sa.Column("corpo", sa.Text(), nullable=False, server_default=""),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("evento", name="uq_logistica_mensagem_template_evento"),
        schema=SCHEMA,
    )

    op.create_table(
        "logistica_mensagem_cliente",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "logistica_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.logistica.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evento", sa.Text(), nullable=False),
        sa.Column("destinatario", sa.Text(), nullable=False, server_default=""),
        sa.Column("assunto", sa.Text(), nullable=False, server_default=""),
        sa.Column("corpo", sa.Text(), nullable=False, server_default=""),
        sa.Column("enviado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "logistica_id", "evento", name="uq_logistica_mensagem_cliente_logistica_id_evento"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_logistica_mensagem_cliente_logistica_id",
        "logistica_mensagem_cliente",
        ["logistica_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_logistica_mensagem_cliente_logistica_id",
        table_name="logistica_mensagem_cliente",
        schema=SCHEMA,
    )
    op.drop_table("logistica_mensagem_cliente", schema=SCHEMA)
    op.drop_table("logistica_mensagem_template", schema=SCHEMA)
    op.drop_index("ix_logistica_amazon_canal", table_name="logistica", schema=SCHEMA)
    for col in reversed(_colunas()):
        op.drop_column("logistica", col.name, schema=SCHEMA)
