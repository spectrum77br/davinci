"""Marketing › Lives — tabela marketing_lives (03/11).

Eduardo, 03/11: gravar as lives da Shopee (e do TikTok: "shopee ou tiktok
tambem ne") que pessoas reais fazem, para medir o padrão de uma boa live e,
mais tarde, virar persona de IA. Alguém cadastra a live com o link de
compartilhar; o robô do Mac mini grava o stream com ffmpeg e devolve o status,
o caminho do arquivo e as métricas. Tabela nova, nada existente muda.

Revision ID: 0448_marketing_lives
Revises: 0447_consorcio_imposto1_manual
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0448_marketing_lives"
down_revision: str | None = "0447_consorcio_imposto1_manual"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABELA = "marketing_lives"


def _quando(nome: str, nullable: bool = True, agora: bool = False) -> sa.Column:
    return sa.Column(
        nome,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if agora else None,
        nullable=nullable,
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.create_table(
        TABELA,
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("plataforma", sa.String(16), nullable=False),
        sa.Column("link", sa.Text(), nullable=False),
        sa.Column("apresentador", sa.Text(), nullable=True),
        sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("loja", sa.Text(), nullable=True),
        sa.Column("produtos", sa.Text(), nullable=True),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), server_default=sa.text("'aguardando'"), nullable=False),
        sa.Column("titulo", sa.Text(), nullable=True),
        sa.Column("canal", sa.Text(), nullable=True),
        sa.Column("sala_id", sa.Text(), nullable=True),
        _quando("inicio_em"),
        _quando("fim_em"),
        sa.Column("duracao_s", sa.Integer(), nullable=True),
        sa.Column("arquivo", sa.Text(), nullable=True),
        sa.Column("tamanho_bytes", sa.BigInteger(), nullable=True),
        sa.Column("metricas", pg.JSONB(), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        _quando("robo_visto_em"),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=True),
        _quando("created_at", nullable=False, agora=True),
        _quando("updated_at", nullable=False, agora=True),
        sa.PrimaryKeyConstraint("id", name=f"pk_{TABELA}"),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            [f"{SCHEMA}.integrations.id"],
            name=f"fk_{TABELA}_integration_id_integrations",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name=f"fk_{TABELA}_created_by_users",
            ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    op.create_index(f"ix_{TABELA}_status", TABELA, ["status"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index(f"ix_{TABELA}_status", table_name=TABELA, schema=SCHEMA)
    op.drop_table(TABELA, schema=SCHEMA)
