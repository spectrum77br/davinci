"""Registro de cada envio do Threema (tarefa 16, C1b)

Cadastros › Envios Threema, primeiro passo que grava: uma linha por
destinatário e por tentativa, escrita por `ThreemaClient.send_to_all` através
de `services/threema_registro.py` (melhor esforço, nunca derruba o envio).
Nada muda em quem recebe: é só o registro, para haver linha de base por envio
antes de qualquer migração para o Cadastro.

- threema_envio_registro: quando, de qual envio (`envio_chave`, sem FK: o
  catálogo mora no código), remetente (`contexto`/`canal`), origem
  (automatico | manual | teste), quem disparou (`ator_*`, `req_id`), para quem
  (`destinatario_*`), se chegou (`ok`, `http_status`, `erro`, `message_id`),
  a 1ª linha da mensagem (`titulo`) e o tamanho. Nunca o segredo nem o texto
  inteiro. `ator_id` → users SET NULL.
- Índices: (envio_chave, criado_em DESC), (criado_em DESC) e o parcial das
  falhas (criado_em DESC) WHERE NOT ok.

Fora do Histórico (`historico/sql.EXCLUIDAS`). Limpeza de 180 dias na
`alerts_cleanup` do worker. Só tabela nova: sem ALTER em tabela existente.

DOWNGRADE: apaga a tabela (o registro gravado se perde; nada mais depende dela).

Revision ID: 0504_threema_envio_registro
Revises: 0503_pricing_ads
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0504_threema_envio_registro"
down_revision: str | None = "0503_pricing_ads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"
TABELA = "threema_envio_registro"
# FIXOS (não importam a constante viva do modelo; o teste confere que batem).
ORIGENS = ("automatico", "manual", "teste")
VIAS = ("cadastro", "legado", "cache", "forcado", "env")
ONDE_RODA = ("servidor", "mac")


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    return f"{coluna} IN ({', '.join(repr(v) for v in valores)})"


def upgrade() -> None:
    # Só tabela nova; o limite vale para a FK em users.
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.create_table(
        TABELA,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("lote_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("envio_chave", sa.String(60), nullable=True),
        sa.Column("escopo", sa.String(80), nullable=True),
        sa.Column("contexto", sa.String(20), nullable=True),
        sa.Column("canal", sa.String(20), nullable=True),
        sa.Column("onde_roda", sa.String(10), server_default=sa.text("'servidor'"), nullable=False),
        sa.Column("origem", sa.String(12), nullable=False),
        sa.Column("via", sa.String(10), nullable=True),
        sa.Column("ator_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ator_nome", sa.String(120), nullable=True),
        sa.Column("req_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("destinatario_id", sa.String(8), nullable=True),
        sa.Column("destinatario_nome", sa.String(120), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("contato_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("erro", sa.String(200), nullable=True),
        sa.Column("message_id", sa.String(32), nullable=True),
        sa.Column("titulo", sa.String(160), nullable=True),
        sa.Column("bytes", sa.Integer(), nullable=True),
        sa.Column("host", sa.String(60), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABELA}")),
        sa.ForeignKeyConstraint(
            ["ator_id"],
            [f"{SCHEMA}.users.id"],
            name=op.f(f"fk_{TABELA}_ator_id_users"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(_in("origem", ORIGENS), name=op.f(f"ck_{TABELA}_origem")),
        sa.CheckConstraint(f"via IS NULL OR {_in('via', VIAS)}", name=op.f(f"ck_{TABELA}_via")),
        sa.CheckConstraint(_in("onde_roda", ONDE_RODA), name=op.f(f"ck_{TABELA}_onde_roda")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_threema_envio_registro_chave_criado",
        TABELA,
        ["envio_chave", sa.text("criado_em DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_threema_envio_registro_criado", TABELA, [sa.text("criado_em DESC")], schema=SCHEMA
    )
    op.create_index(
        "ix_threema_envio_registro_falhas",
        TABELA,
        [sa.text("criado_em DESC")],
        postgresql_where=sa.text("NOT ok"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_threema_envio_registro_falhas", table_name=TABELA, schema=SCHEMA)
    op.drop_index("ix_threema_envio_registro_criado", table_name=TABELA, schema=SCHEMA)
    op.drop_index("ix_threema_envio_registro_chave_criado", table_name=TABELA, schema=SCHEMA)
    op.drop_table(TABELA, schema=SCHEMA)
