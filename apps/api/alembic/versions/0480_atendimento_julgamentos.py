"""O julgamento de cada decisão sobre o rascunho da IA e o retrato da régua (13/11/2026)

A régua do envio sozinho (80% por tipo de pergunta, 90% nos sensíveis — o Eduardo,
13/11/2026) mede a aprovação da equipe: o rascunho mandado igual ou mexido só na forma é
acerto. Esta migration só cria a tabela; quem a enche é o cron `atendimento_julgar_edicoes`
(services/atendimento/juiz_edicao.py). Sem texto de cliente: só o código e uma linha do porquê.
A segunda, `atendimento_regua`, é o retrato por canal e tipo que o cron `atendimento_regua`
refaz (services/atendimento/regua.py).

Revision ID: 0480_atendimento_julgamentos
Revises: 0479_atendimento_respostas
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.config import get_settings

revision: str = "0480_atendimento_julgamentos"
down_revision: str | None = "0479_atendimento_respostas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = get_settings().database_schema
TABELA = "atendimento_julgamentos"
REGUA = "atendimento_regua"


def upgrade() -> None:
    op.create_table(
        TABELA,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("avaliacao_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("rascunho_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("plataforma", sa.String(16), nullable=False),
        sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("tipo", sa.String(32), nullable=False),
        sa.Column("julgamento", sa.String(16), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("pelo_codigo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("modelo", sa.String(64), nullable=True),
        sa.Column("custo_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("decidido_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["avaliacao_id"],
            [f"{SCHEMA}.atendimento_avaliacoes.id"],
            name="fk_atendimento_julgamentos_avaliacao_id_atendimento_avaliacoes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rascunho_id"],
            [f"{SCHEMA}.atendimento_rascunhos.id"],
            name="fk_atendimento_julgamentos_rascunho_id_atendimento_rascunhos",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            [f"{SCHEMA}.integrations.id"],
            name="fk_atendimento_julgamentos_integration_id_integrations",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_julgamentos"),
        sa.UniqueConstraint("avaliacao_id", name="uq_atendimento_julgamentos_avaliacao_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_julgamentos_tipo_loja",
        TABELA,
        ["tipo", "integration_id", "decidido_em"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_julgamentos_plataforma",
        TABELA,
        ["plataforma", "tipo", "decidido_em"],
        schema=SCHEMA,
    )
    op.create_table(
        REGUA,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("canal_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("plataforma", sa.String(16), nullable=False),
        sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("tipo", sa.String(32), nullable=False),
        sa.Column("decisoes", sa.Integer(), nullable=False),
        sa.Column("acertos", sa.Integer(), nullable=False),
        sa.Column("escopo", sa.String(16), nullable=False),
        sa.Column("limiar", sa.Float(), nullable=False),
        sa.Column("liberado", sa.Boolean(), nullable=False),
        sa.Column("liberado_hoje", sa.Boolean(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["canal_id"],
            [f"{SCHEMA}.atendimento_canais.id"],
            name="fk_atendimento_regua_canal_id_atendimento_canais",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_regua"),
        sa.UniqueConstraint("canal_id", "tipo", name="uq_atendimento_regua_canal_id_tipo"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table(REGUA, schema=SCHEMA)
    op.drop_table(TABELA, schema=SCHEMA)
