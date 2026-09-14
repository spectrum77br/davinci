"""prioridade_estoque_movimentos — compensação de estoque do robô de prioridade (kits)

Eduardo, 14/09/2026: com a prioridade SP, o robô troca dg053.ci+a001.ci por
dg053.sp+a001.sp no pedido, mas o Bling continua baixando os componentes do
kit ANTIGO na NF (produto simples ele baixa certo). O robô passa a lançar
ENTRADA nos componentes antigos e SAÍDA nos novos (POST /estoques, como a
correção de estoque das Devoluções) e registra cada lançamento aqui — em
transação própria, antes do POST — pra retentar falhas, avisar incertezas e
estornar se o pedido for cancelado/excluído antes de sair.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0268_prioridade_estoque_movimentos"
down_revision: str | None = "0267_refunds_reembolso_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "prioridade_estoque_movimentos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("pedido_bling", sa.Text(), nullable=False),
        sa.Column("bling_id", sa.BigInteger(), nullable=True),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("bling_product_id", sa.BigInteger(), nullable=True),
        sa.Column("operacao", sa.Text(), nullable=False),
        sa.Column("quantidade", sa.Integer(), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pendente'")),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("lancado_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revertido_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("avisado_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_prioridade_estoque_movimentos_pedido_bling",
        "prioridade_estoque_movimentos",
        ["pedido_bling"],
        schema=SCHEMA,
    )
    # Trava: um lançamento vivo por (pedido, componente, operação). O INSERT
    # do robô usa ON CONFLICT DO NOTHING nesse índice.
    op.create_index(
        "uq_prioridade_estoque_mov_vivo",
        "prioridade_estoque_movimentos",
        ["pedido_bling", "sku", "operacao"],
        unique=True,
        postgresql_where=sa.text("revertido_at IS NULL"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_prioridade_estoque_mov_vivo",
        table_name="prioridade_estoque_movimentos",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_prioridade_estoque_movimentos_pedido_bling",
        table_name="prioridade_estoque_movimentos",
        schema=SCHEMA,
    )
    op.drop_table("prioridade_estoque_movimentos", schema=SCHEMA)
