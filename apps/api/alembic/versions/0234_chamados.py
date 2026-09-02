"""Chamados — aba nova em Pós-venda que centraliza os chamados abertos nas
plataformas (origem Margem / Logística / Devolução).

Eduardo (01/09/2026, aba `Chamados` do chamados.xlsx): "vamos criar uma aba
nova daquele mesmo estilo do excel, em pós-vendas depois de notas fiscais,
chamados, que é o que vamos englobar todos os chamados ali dentro".

Três tabelas:
- `chamados`: 1 linha por chamado — dados do pedido (espelho do bling_orders
  no momento da criação), origem, nº do chamado/protocolo, canal de envio
  (api = mediação do ML via API; robo = formulário web/protocolo, fila do
  robô; manual = só registro), alterar status Bling, monitoramento, réplica
  automática (mensagem + a cada N dias + ligada) e resolvido.
- `chamado_mensagem`: histórico (quem/quando/o quê) — réplicas manuais e
  automáticas enviadas, respostas recebidas e eventos do sistema.
- `chamado_anexo`: fotos (blob no banco, como logistica_status_anexo). Com
  `mensagem_id` = anexo daquela réplica; sem = anexo da réplica automática.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0234_chamados"
down_revision: str | None = "0233_segment_special_dates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # A tabela `logistica` NASCEU como `chamados` (0181) e foi renomeada em
    # 0182 — mas a PK continuou se chamando `pk_chamados`, e o índice da PK
    # tem o mesmo nome. Criar a tabela nova com a naming convention
    # (`pk_chamados`) batia em "relation pk_chamados already exists". Renomeia
    # a PK legada pro nome que ela deveria ter; guardado por EXISTS pra banco
    # novo (create_all dos testes) não quebrar.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_namespace n ON n.oid = c.connamespace
                WHERE n.nspname = '{SCHEMA}' AND c.conname = 'pk_chamados'
                  AND c.conrelid = '{SCHEMA}.logistica'::regclass
            ) THEN
                ALTER TABLE {SCHEMA}.logistica RENAME CONSTRAINT pk_chamados TO pk_logistica;
            END IF;
        END $$;
        """
    )
    op.create_table(
        "chamados",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("data", sa.Date(), nullable=True),
        sa.Column("pedido_bling", sa.Text(), nullable=True),
        sa.Column("pedido_marketplace", sa.Text(), nullable=True),
        sa.Column("plataforma", sa.Text(), nullable=True),
        sa.Column("conta", sa.Text(), nullable=True),
        sa.Column("produto", sa.Text(), nullable=True),
        sa.Column("sku", sa.Text(), nullable=True),
        sa.Column("status_bling", sa.Text(), nullable=True),
        sa.Column("origem", sa.Text(), nullable=False),
        sa.Column("origem_ref", sa.Text(), nullable=True),
        sa.Column("chamado", sa.Text(), nullable=True),
        sa.Column("chamado_url", sa.Text(), nullable=True),
        sa.Column("canal", sa.Text(), nullable=False, server_default="manual"),
        sa.Column("alterar_status_bling", sa.Text(), nullable=True),
        sa.Column("monitoramento", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("auto_ligada", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("auto_dias", sa.Integer(), nullable=True),
        sa.Column("auto_mensagem", sa.Text(), nullable=True),
        sa.Column("auto_ultimo_envio_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolvido", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("resolvido_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_chamados_pedido_bling", "chamados", ["pedido_bling"], schema=SCHEMA)
    op.create_index("ix_chamados_resolvido", "chamados", ["resolvido"], schema=SCHEMA)

    op.create_table(
        "chamado_mensagem",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "chamado_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.chamados.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("direcao", sa.Text(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("canal", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("autor_nome", sa.Text(), nullable=True),
        sa.Column(
            "autor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("enviada_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chamado_mensagem_chamado_id", "chamado_mensagem", ["chamado_id"], schema=SCHEMA
    )

    op.create_table(
        "chamado_anexo",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "chamado_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.chamados.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mensagem_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.chamado_mensagem.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_chamado_anexo_chamado_id", "chamado_anexo", ["chamado_id"], schema=SCHEMA)
    op.create_index("ix_chamado_anexo_mensagem_id", "chamado_anexo", ["mensagem_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_chamado_anexo_mensagem_id", table_name="chamado_anexo", schema=SCHEMA)
    op.drop_index("ix_chamado_anexo_chamado_id", table_name="chamado_anexo", schema=SCHEMA)
    op.drop_table("chamado_anexo", schema=SCHEMA)
    op.drop_index("ix_chamado_mensagem_chamado_id", table_name="chamado_mensagem", schema=SCHEMA)
    op.drop_table("chamado_mensagem", schema=SCHEMA)
    op.drop_index("ix_chamados_resolvido", table_name="chamados", schema=SCHEMA)
    op.drop_index("ix_chamados_pedido_bling", table_name="chamados", schema=SCHEMA)
    op.drop_table("chamados", schema=SCHEMA)
