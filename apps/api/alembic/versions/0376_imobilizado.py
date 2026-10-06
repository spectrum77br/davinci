"""imobilizado + imobilizado_historico — cadastro dos bens da empresa

Projeto "Cadastro de Imobilizado no DaVinci" v0.1 (06/10/2026): número do
patrimônio (único, fixo), descrição, valor e responsável (usuário ativo). Item
não é excluído: sai por baixa com data e motivo. Toda troca de descrição,
valor, responsável ou status grava uma linha em imobilizado_historico.
Tabelas novas e vazias: nada existente muda.

Revision ID: 0376_imobilizado
Revises: 0375_denuncia_caso_status_manual
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0376_imobilizado"
down_revision: str | None = "0375_denuncia_caso_status_manual"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = "davinci"


def _user_fk(nome: str, *, nullable: bool) -> sa.Column:
    return sa.Column(
        nome,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"),
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "imobilizado",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("numero", sa.String(20), nullable=False),
        sa.Column("descricao", sa.String(200), nullable=False),
        sa.Column("valor", sa.Numeric(15, 2), nullable=False),
        _user_fk("responsavel_id", nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="ativo"),
        sa.Column("baixa_data", sa.Date(), nullable=True),
        sa.Column("baixa_motivo", sa.String(200), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _user_fk("criado_por", nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=True),
        _user_fk("atualizado_por", nullable=True),
        sa.UniqueConstraint("numero", name="uq_imobilizado_numero"),
        sa.CheckConstraint("valor > 0", name="valor_positivo"),
        sa.CheckConstraint("status IN ('ativo', 'baixado')", name="status_valido"),
        sa.CheckConstraint(
            "(status = 'baixado') = (baixa_data IS NOT NULL AND baixa_motivo IS NOT NULL)",
            name="baixa_completa",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_imobilizado_responsavel", "imobilizado", ["responsavel_id"], schema=SCHEMA)
    op.create_index("ix_imobilizado_status", "imobilizado", ["status"], schema=SCHEMA)

    op.create_table(
        "imobilizado_historico",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "imobilizado_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.imobilizado.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("campo", sa.String(30), nullable=False),
        sa.Column("valor_anterior", sa.Text(), nullable=True),
        sa.Column("valor_novo", sa.Text(), nullable=True),
        sa.Column(
            "alterado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _user_fk("alterado_por", nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_imobilizado_historico_item",
        "imobilizado_historico",
        ["imobilizado_id", "alterado_em"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("imobilizado_historico", schema=SCHEMA)
    op.drop_table("imobilizado", schema=SCHEMA)
