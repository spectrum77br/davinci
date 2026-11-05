"""Controle de Estoque: botão "Transferir estoque" (troca a loja Centro de Distribuição)

Eduardo (05/11/2026): a equipe passava estoque de um lote para outro com
pedido de R$ 0,01 na loja interna "Centro de Distribuição" do Bling
(quantidade positiva = saída, negativa = entrada; ex. 307266: sai 200 a004.cd,
entra 200 a004.pi). Agora o DaVinci lança direto no estoque do Bling, sem
pedido: SAÍDA na origem e, depois dela, ENTRADA no destino.

- `estoque_transferencias`: quem, quando, motivo e a chave de idempotência
  vinda da tela (clique duplo não lança duas vezes).
- `estoque_transferencia_linhas`: origem → destino × quantidade, com o status
  de cada perna (saída/entrada) e o movimento do extrato que a confirmou.

As duas ficam COM o gatilho do Histórico (o daily do worker instala): quem
transferiu e quem resolveu à mão é o que o Histórico serve para mostrar.

Revision ID: 0453_estoque_transferencias
Revises: 0452_chamados_dias_tiktok
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0453_estoque_transferencias"
down_revision: str | None = "0452_chamados_dias_tiktok"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def _tempo() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.execute(
        sa.schema.CreateSequence(sa.Sequence("estoque_transferencias_numero_seq", schema=SCHEMA))
    )
    op.create_table(
        "estoque_transferencias",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "numero",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text(f"nextval('{SCHEMA}.estoque_transferencias_numero_seq')"),
        ),
        sa.Column("idem_key", sa.Text(), nullable=False),
        sa.Column(
            "criado_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("criado_por_nome", sa.Text(), nullable=True),
        sa.Column("observacao", sa.Text(), nullable=True),
        *_tempo(),
        sa.UniqueConstraint("numero", name="uq_estoque_transferencias_numero"),
        sa.UniqueConstraint("idem_key", name="uq_estoque_transferencias_idem_key"),
        schema=SCHEMA,
    )
    op.execute(
        f"ALTER SEQUENCE {SCHEMA}.estoque_transferencias_numero_seq "
        f"OWNED BY {SCHEMA}.estoque_transferencias.numero"
    )
    op.create_table(
        "estoque_transferencia_linhas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "transferencia_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.estoque_transferencias.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordem", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("origem_sku", sa.Text(), nullable=False),
        sa.Column("origem_bling_id", sa.BigInteger(), nullable=False),
        sa.Column("destino_sku", sa.Text(), nullable=False),
        sa.Column("destino_bling_id", sa.BigInteger(), nullable=False),
        sa.Column("quantidade", sa.Integer(), nullable=False),
        sa.Column("custo", sa.Numeric(14, 4), nullable=True),
        sa.Column("saida_status", sa.Text(), nullable=False, server_default=sa.text("'pendente'")),
        sa.Column("saida_erro", sa.Text(), nullable=True),
        sa.Column("saida_tentativa_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("saida_lancada_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("saida_mov_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "entrada_status", sa.Text(), nullable=False, server_default=sa.text("'aguardando'")
        ),
        sa.Column("entrada_erro", sa.Text(), nullable=True),
        sa.Column("entrada_tentativa_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entrada_lancada_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entrada_mov_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entrada_auto_tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolvido_manual", sa.Text(), nullable=True),
        *_tempo(),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_estoque_transferencia_linhas_transferencia_id",
        "estoque_transferencia_linhas",
        ["transferencia_id"],
        schema=SCHEMA,
    )
    # O worker só olha as pernas em andamento: índice parcial pequeno.
    op.create_index(
        "ix_estoque_transferencia_linhas_pendentes",
        "estoque_transferencia_linhas",
        ["saida_status", "entrada_status"],
        schema=SCHEMA,
        postgresql_where=sa.text(
            "saida_status IN ('pendente','enviando','conferindo') "
            "OR entrada_status IN ('pendente','enviando','conferindo','falhou')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_estoque_transferencia_linhas_pendentes",
        table_name="estoque_transferencia_linhas",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_estoque_transferencia_linhas_transferencia_id",
        table_name="estoque_transferencia_linhas",
        schema=SCHEMA,
    )
    op.drop_table("estoque_transferencia_linhas", schema=SCHEMA)
    op.drop_table("estoque_transferencias", schema=SCHEMA)
