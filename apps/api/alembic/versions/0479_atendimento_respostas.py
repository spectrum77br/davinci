"""O banco de respostas do agente: pergunta do cliente × resposta de pessoa (13/11/2026)

O Eduardo, 13/11/2026: "no banco entra so as q a pessoa respondeu ou ajustou". Esta
migration só cria a tabela `atendimento_respostas` (e os índices); quem a enche é o
cron `atendimento_indexar_banco` (services/atendimento/banco_respostas.py), que lê as
conversas de todo o período e grava só a versão mascarada e generalizada — nada de
nome, pedido, rastreio ou contato. Gravar aqui não muda o agente: por enquanto quem lê
é só a cópia do Obsidian (GET /api/atendimento/robo/banco-obsidian).

Tamanho: ~6 mil linhas na carga (colheita de 13/11) e algumas dezenas por dia; poucos MB.

Revision ID: 0479_atendimento_respostas
Revises: 0478_atlas_ml_loja_bling
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.config import get_settings

revision: str = "0479_atendimento_respostas"
down_revision: str | None = "0478_atlas_ml_loja_bling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = get_settings().database_schema
TABELA = "atendimento_respostas"


def _quando(nome: str, *, nullable: bool = True, agora: bool = False) -> sa.Column:
    return sa.Column(
        nome,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()") if agora else None,
    )


def upgrade() -> None:
    op.create_table(
        TABELA,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chave", sa.String(32), nullable=False),
        sa.Column("conversa_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("resposta_msg_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("plataforma", sa.String(16), nullable=False),
        sa.Column("canal", sa.String(16), nullable=False),
        sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("loja", sa.Text(), nullable=True),
        sa.Column("item_id", sa.String(64), nullable=True),
        sa.Column("anuncio_titulo", sa.Text(), nullable=True),
        sa.Column("modelos", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("tipo", sa.String(32), nullable=False),
        sa.Column("pergunta", sa.Text(), nullable=False),
        sa.Column("resposta", sa.Text(), nullable=False),
        sa.Column("fonte", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'ativa'"), nullable=False),
        sa.Column("substituida_por", sa.BigInteger(), nullable=True),
        _quando("perguntado_em"),
        _quando("respondido_em", nullable=False),
        sa.Column("cliente_depois", sa.String(16), nullable=True),
        sa.Column("tsv", pg.TSVECTOR(), nullable=False),
        _quando("created_at", nullable=False, agora=True),
        _quando("updated_at", nullable=False, agora=True),
        sa.ForeignKeyConstraint(
            ["conversa_id"],
            [f"{SCHEMA}.atendimento_conversas.id"],
            name="fk_atendimento_respostas_conversa_id_atendimento_conversas",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            [f"{SCHEMA}.integrations.id"],
            name="fk_atendimento_respostas_integration_id_integrations",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_respostas"),
        sa.UniqueConstraint("chave", name="uq_atendimento_respostas_chave"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_respostas_tsv", TABELA, ["tsv"], postgresql_using="gin", schema=SCHEMA
    )
    op.create_index(
        "ix_atendimento_respostas_tipo_plataforma",
        TABELA,
        ["tipo", "plataforma", "integration_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_respostas_respondido_em", TABELA, ["respondido_em"], schema=SCHEMA
    )
    op.create_index("ix_atendimento_respostas_conversa_id", TABELA, ["conversa_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table(TABELA, schema=SCHEMA)
