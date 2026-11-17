"""Sistema › Mudanças: pedidos de mudança no próprio DaVinci feitos pelo Claude

Eduardo, 17/11/2026: o chefe pede correções rápidas no sistema pelo Claude
dele (conector, `pedir_mudanca`); um robô no Mac faz a mudança numa cópia
separada e roda os testes; só Eduardo (heisenberg) e Thorfinn aprovam na
tela Sistema › Mudanças; aprovado, o robô publica.

- sistema_mudanca: um pedido, do "na fila" ao "no ar" (fora do Histórico:
  é escrita pelo robô e já é a trilha — `historico/sql.EXCLUIDAS`).
- sistema_mudanca_aprovador: quem aprova, por user_id; nasce com os admins
  ativos de nome heisenberg e thorfinn (o nome só é lido AQUI, uma vez —
  depois vale o id, como no `historico_acesso`).

Revision ID: 0493_sistema_mudancas
Revises: 0492_devolucao_fraude_reabre_dano
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0493_sistema_mudancas"
down_revision: str | None = "0492_devolucao_fraude_reabre_dano"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
STATUS = (
    "pedida",
    "fazendo",
    "pronta",
    "aprovada",
    "publicando",
    "publicada",
    "recusada",
    "precisa_equipe",
    "falhou",
)
APROVADORES = ("heisenberg", "thorfinn")


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.create_table(
        "sistema_mudanca",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("numero", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("pedido_por", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("titulo", sa.String(200), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("onde", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default=sa.text("'pedida'"), nullable=False),
        sa.Column("resumo", sa.Text(), nullable=True),
        sa.Column("testes", sa.Text(), nullable=True),
        sa.Column("diff", sa.Text(), nullable=True),
        sa.Column("diff_sha", sa.String(64), nullable=True),
        sa.Column("arquivos", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("branch", sa.Text(), nullable=True),
        sa.Column("commit", sa.String(64), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("robo", sa.String(120), nullable=True),
        sa.Column("pego_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prazo_robo", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tentativas", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("adiamentos", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("aprovado_por", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("aprovado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recusado_por", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recusado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("motivo_recusa", sa.Text(), nullable=True),
        sa.Column("publicado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["pedido_por"],
            [f"{SCHEMA}.users.id"],
            name=op.f("fk_sistema_mudanca_pedido_por_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["aprovado_por"],
            [f"{SCHEMA}.users.id"],
            name=op.f("fk_sistema_mudanca_aprovado_por_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["recusado_por"],
            [f"{SCHEMA}.users.id"],
            name=op.f("fk_sistema_mudanca_recusado_por_users"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in STATUS) + ")",
            name=op.f("ck_sistema_mudanca_status"),
        ),
        sa.UniqueConstraint("numero", name=op.f("uq_sistema_mudanca_numero")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_sistema_mudanca_status"), "sistema_mudanca", ["status"], schema=SCHEMA
    )
    op.create_table(
        "sistema_mudanca_aprovador",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.users.id"],
            name=op.f("fk_sistema_mudanca_aprovador_user_id_users"),
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "sistema_mudanca_robo",
        sa.Column("robo", sa.String(120), primary_key=True),
        sa.Column(
            "visto_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema=SCHEMA,
    )
    nomes = ", ".join(f"'{n}'" for n in APROVADORES)
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.sistema_mudanca_aprovador (user_id)
        SELECT id FROM {SCHEMA}.users
        WHERE lower(name) IN ({nomes}) AND role = 'admin' AND status = 'active'
        ON CONFLICT (user_id) DO NOTHING
        """  # noqa: S608 — nomes fixos da migration, sem entrada de fora
    )
    linhas = op.get_bind().execute(
        sa.text(
            f"SELECT u.name FROM {SCHEMA}.sistema_mudanca_aprovador a "  # noqa: S608
            f"JOIN {SCHEMA}.users u ON u.id = a.user_id ORDER BY 1"
        )
    )
    print("0493 aprovadores:", [r[0] for r in linhas])  # noqa: T201 — sai no log do deploy


def downgrade() -> None:
    op.drop_table("sistema_mudanca_robo", schema=SCHEMA)
    op.drop_table("sistema_mudanca_aprovador", schema=SCHEMA)
    op.drop_index(op.f("ix_sistema_mudanca_status"), table_name="sistema_mudanca", schema=SCHEMA)
    op.drop_table("sistema_mudanca", schema=SCHEMA)
