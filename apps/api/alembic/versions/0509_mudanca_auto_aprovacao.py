"""Sistema › Mudanças: pedidos do Harry aprovados sozinhos

Eduardo, 24/11/2026: "todas as tarefas que o harry jogar em mudança é para
fazer, está automaticamente aprovado".

- sistema_mudanca_auto_aprovacao: de quem o pedido já nasce aprovado
  (`pedido_por`) e quem deixou (`autorizado_por`), por user_id. Quando o robô
  termina (pronta), o pedido vira aprovada em nome de quem autorizou — só
  enquanto essa pessoa continua aprovadora ativa. Nasce com o admin ativo de
  nome "harry potter", autorizado por heisenberg (o nome só é lido AQUI, uma
  vez — depois vale o id, como na 0493). Fica COM o gatilho do Histórico:
  quem ganhou ou perdeu a aprovação automática é o que ele serve para mostrar.
- sistema_mudanca.aprovado_automatico: a aprovação veio da regra (ninguém
  clicou). A tela e o conector dizem "automático".

Revision ID: 0509_mudanca_auto_aprovacao
Revises: 0508_produto_cadastros
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0509_mudanca_auto_aprovacao"
down_revision: str | None = "0508_produto_cadastros"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
PEDE = "harry potter"
AUTORIZA = "heisenberg"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "sistema_mudanca",
        sa.Column(
            "aprovado_automatico",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "sistema_mudanca_auto_aprovacao",
        sa.Column("pedido_por", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("autorizado_por", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("pedido_por", name=op.f("pk_sistema_mudanca_auto_aprovacao")),
        sa.ForeignKeyConstraint(
            ["pedido_por"],
            [f"{SCHEMA}.users.id"],
            name=op.f("fk_sistema_mudanca_auto_aprovacao_pedido_por_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["autorizado_por"],
            [f"{SCHEMA}.users.id"],
            name=op.f("fk_sistema_mudanca_auto_aprovacao_autorizado_por_users"),
            ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.sistema_mudanca_auto_aprovacao (pedido_por, autorizado_por)
        SELECT p.id, a.id
        FROM {SCHEMA}.users p
        JOIN {SCHEMA}.users a
          ON lower(a.name) = '{AUTORIZA}' AND a.role = 'admin' AND a.status = 'active'
        JOIN {SCHEMA}.sistema_mudanca_aprovador ap ON ap.user_id = a.id
        WHERE lower(p.name) = '{PEDE}' AND p.role = 'admin' AND p.status = 'active'
        ON CONFLICT (pedido_por) DO NOTHING
        """  # noqa: S608 — nomes fixos da migration, sem entrada de fora
    )
    linhas = op.get_bind().execute(
        sa.text(
            f"SELECT p.name, a.name FROM {SCHEMA}.sistema_mudanca_auto_aprovacao r "  # noqa: S608
            f"JOIN {SCHEMA}.users p ON p.id = r.pedido_por "
            f"LEFT JOIN {SCHEMA}.users a ON a.id = r.autorizado_por ORDER BY 1"
        )
    )
    print("0509 aprovação automática:", [tuple(r) for r in linhas])  # noqa: T201 — log do deploy


def downgrade() -> None:
    op.drop_table("sistema_mudanca_auto_aprovacao", schema=SCHEMA)
    op.drop_column("sistema_mudanca", "aprovado_automatico", schema=SCHEMA)
