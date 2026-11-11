"""Jurídico: processos e movimentações (Ouvidoria › Jurídico)

Vinicius (11/11/2026): painel Jurídico no padrão da Conciliação/Chamados — data, nº do
processo, pedido/referência, origem (Denúncia / Logística / à mão), autor, réu, advogado,
assunto, última movimentação, Jusbrasil e situação (Opção B, com Raio-X).

Só cria as tabelas. Os casos da Denúncia que já foram ao advogado entram sozinhos na
primeira vez que o painel abre (o nº e o link do Jusbrasil já digitados no caso vêm junto).

Revision ID: 0466_juridico_processos
Revises: 0465_chamados_revisao_encerrar
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0466_juridico_processos"
down_revision: str | None = "0465_chamados_revisao_encerrar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "juridico_processos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("origem", sa.Text(), nullable=False),
        sa.Column("denuncia_caso_id", sa.BigInteger(), nullable=True),
        sa.Column("chamado_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pedido", sa.Text(), nullable=True),
        sa.Column("encaminhado_em", sa.Date(), nullable=True),
        sa.Column("numero", sa.Text(), nullable=True),
        sa.Column("lado", sa.Text(), server_default="autor", nullable=False),
        sa.Column("autor", sa.Text(), nullable=True),
        sa.Column("autor_obs", sa.Text(), nullable=True),
        sa.Column("reu", sa.Text(), nullable=True),
        sa.Column("reu_obs", sa.Text(), nullable=True),
        sa.Column("advogado", sa.Text(), nullable=True),
        sa.Column("advogado_escritorio", sa.Text(), nullable=True),
        sa.Column("assunto", sa.Text(), nullable=True),
        sa.Column("situacao", sa.Text(), server_default="aguardando_numero", nullable=False),
        sa.Column("vara", sa.Text(), nullable=True),
        sa.Column("valor_causa", sa.Numeric(12, 2), nullable=True),
        sa.Column("jusbrasil_link", sa.Text(), nullable=True),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("criado_por", sa.Text(), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("atualizado_por", sa.Text(), nullable=True),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_juridico_processos")),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_juridico_processos_caso",
        "juridico_processos",
        ["denuncia_caso_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("denuncia_caso_id IS NOT NULL"),
    )
    op.create_index(
        "uq_juridico_processos_chamado",
        "juridico_processos",
        ["chamado_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("chamado_id IS NOT NULL"),
    )
    op.create_table(
        "juridico_movimentacoes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("processo_id", sa.BigInteger(), nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("fonte", sa.Text(), server_default="manual", nullable=False),
        sa.Column("criado_por", sa.Text(), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["processo_id"],
            [f"{SCHEMA}.juridico_processos.id"],
            name=op.f("fk_juridico_movimentacoes_processo_id_juridico_processos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_juridico_movimentacoes")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_juridico_movimentacoes_processo",
        "juridico_movimentacoes",
        ["processo_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_juridico_movimentacoes_processo", table_name="juridico_movimentacoes", schema=SCHEMA
    )
    op.drop_table("juridico_movimentacoes", schema=SCHEMA)
    op.drop_index("uq_juridico_processos_chamado", table_name="juridico_processos", schema=SCHEMA)
    op.drop_index("uq_juridico_processos_caso", table_name="juridico_processos", schema=SCHEMA)
    op.drop_table("juridico_processos", schema=SCHEMA)
