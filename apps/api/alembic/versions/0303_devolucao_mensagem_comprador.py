"""Mensagem ao comprador por causa da devolução (Shopee: pedir a senha do produto travado)

Vinicius, 22/09/2026: "quando o pessoal faz o lançamento no painel Devoluções e
coloca motivo Bloqueado, além de abrir chamado, também envie mensagem para o
cliente solicitando a senha".

Uma linha por (pedido, conta, evento) — a UNIQUE é o que segura o kit (várias
linhas de devolução do mesmo pedido) e o gancho do router, que roda no create,
em todo patch de motivo e em TODO upload de foto.

`devolution_id` e `chamado_id` são SET NULL de propósito: a lixeira da aba
Devoluções apaga a linha e o chamado em cascata, e a prova de que o comprador
JÁ recebeu a mensagem não pode sumir junto (foi assim que o 293460 passou a
dizer que a disputa tinha sido aberta "fora do DaVinci"). O snapshot de
pedido/conta fica na própria tabela.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0303_devolucao_mensagem_comprador"
down_revision: str | None = "0302_ouvidoria_indices_e_contas_legadas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "devolucao_mensagem_comprador",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "devolution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.devolutions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "chamado_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.chamados.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pedido_bling", sa.Text(), nullable=False),
        sa.Column("pedido_marketplace", sa.Text(), nullable=True),
        sa.Column("conta", sa.Text(), nullable=False),
        sa.Column("plataforma", sa.Text(), nullable=True),
        sa.Column("evento", sa.Text(), nullable=False, server_default="senha"),
        sa.Column("destinatario_id", sa.Text(), nullable=True),
        sa.Column("conversa_id", sa.Text(), nullable=True),
        sa.Column("mensagem_id", sa.Text(), nullable=True),
        sa.Column("texto", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "anexo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.devolucao_anexo.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="pendente"),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enviada_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resposta_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resposta_texto", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("pedido_bling", "conta", "evento", name="uq_dev_msg_comprador"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_msg_comprador_status",
        "devolucao_mensagem_comprador",
        ["status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_msg_comprador_pedido",
        "devolucao_mensagem_comprador",
        ["pedido_bling"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_msg_comprador_devolution",
        "devolucao_mensagem_comprador",
        ["devolution_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_msg_comprador_chamado",
        "devolucao_mensagem_comprador",
        ["chamado_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dev_msg_comprador_chamado", "devolucao_mensagem_comprador", schema=SCHEMA
    )
    op.drop_index(
        "ix_dev_msg_comprador_devolution", "devolucao_mensagem_comprador", schema=SCHEMA
    )
    op.drop_index(
        "ix_dev_msg_comprador_pedido", "devolucao_mensagem_comprador", schema=SCHEMA
    )
    op.drop_index(
        "ix_dev_msg_comprador_status", "devolucao_mensagem_comprador", schema=SCHEMA
    )
    op.drop_table("devolucao_mensagem_comprador", schema=SCHEMA)
