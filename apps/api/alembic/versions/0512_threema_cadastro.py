"""Cadastro dos envios do Threema (tarefa 16, C3)

Cadastros › Envios Threema: as tabelas onde, nas fases seguintes, o banco
DECIDE quem recebe cada envio, se ele está ligado e a observação. Nesta fase
nenhum envio lê daqui (todos continuam com a configuração de hoje): nada
muda em quem recebe.

- threema_envio: uma linha por envio do catálogo (`services/threema_catalogo`).
  NENHUM INSERT aqui: quem cria e atualiza as linhas é a sincronização do
  catálogo (`services/threema_envios.sincronizar`, no startup do worker e no
  início de `semear`/`conferir`), que só escreve as colunas do código.
  `ativo` NULL = segue o interruptor de hoje; `semeado_em` NULL = legado.
- threema_contato: contatos externos (sem login no DaVinci); ID do Threema
  único e no formato de `services/threema._THREEMA_ID`.
- threema_grupo (nome único sem diferenciar maiúsculas) e
  threema_grupo_membro (usuário OU contato; CASCADE).
- threema_envio_destinatario: quem recebe cada envio, como referência a
  usuário, grupo (RESTRICT: grupo em uso não some) ou contato; a regra de
  status da Logística (item 1) tem lista própria em `logistica_status_id`.
  Três índices únicos por expressão (a regra ausente vira o UUID zero).
- threema_cadastro_estado: uma linha só (id=1, inserida aqui) com o botão de
  emergência `usar_legado` (false).

Só tabelas novas: sem ALTER em tabela existente. O gatilho do Histórico é
posto pelo worker (`historico.instalar`), como em toda tabela nova.

DOWNGRADE: apaga só as tabelas novas (o que foi cadastrado nelas se perde;
as fontes antigas — .env, threema_informar_config, ouvidoria_robos,
logistica_status — não são tocadas em nenhum sentido).

Revision ID: 0512_threema_cadastro
Revises: 0511_threema_envio_registro
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0512_threema_cadastro"
down_revision: str | None = "0511_threema_envio_registro"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de rascunho.
SCHEMA = "davinci"
# FIXOS (não importam as constantes vivas do modelo; o teste confere que batem).
FREQUENCIAS = ("evento", "horario", "manual", "misto")
ONDE_RODA = ("servidor", "mac", "externo")
ORIGENS_CONTATO = ("tela", "migracao", "semeadura")
TIPOS_MEMBRO = ("usuario", "contato")
TIPOS_DESTINATARIO = ("usuario", "grupo", "contato")
THREEMA_ID_REGEX = r"^([A-Z0-9]{8}|\*[A-Z0-9]{7})$"
SEM_REGRA = "00000000-0000-0000-0000-000000000000"


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    return f"{coluna} IN ({', '.join(repr(v) for v in valores)})"


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    # Só tabelas novas; o limite vale para as FKs em users e logistica_status.
    op.execute("SET LOCAL lock_timeout = '3s'")

    t = "threema_envio"
    op.create_table(
        t,
        sa.Column("chave", sa.String(60), nullable=False),
        sa.Column("painel", sa.String(40), nullable=False),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("descricao", sa.Text(), server_default="", nullable=False),
        sa.Column("frequencia_tipo", sa.String(10), nullable=False),
        sa.Column("frequencia_texto", sa.String(200), server_default="", nullable=False),
        sa.Column("onde_roda", sa.String(10), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=True),
        sa.Column("ativo_alterado_por", sa.String(120), nullable=True),
        sa.Column("ativo_alterado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("semeado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("descontinuado_em", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("chave", name=op.f(f"pk_{t}")),
        sa.CheckConstraint(
            _in("frequencia_tipo", FREQUENCIAS), name=op.f(f"ck_{t}_frequencia_tipo")
        ),
        sa.CheckConstraint(_in("onde_roda", ONDE_RODA), name=op.f(f"ck_{t}_onde_roda")),
        schema=SCHEMA,
    )

    t = "threema_contato"
    op.create_table(
        t,
        _uuid_pk(),
        sa.Column("nome", sa.String(80), nullable=False),
        sa.Column("threema_id", sa.String(8), nullable=False),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("origem", sa.String(12), nullable=False),
        sa.Column("observacao", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        sa.UniqueConstraint("threema_id", name=op.f(f"uq_{t}_threema_id")),
        sa.CheckConstraint(f"threema_id ~ '{THREEMA_ID_REGEX}'", name=op.f(f"ck_{t}_threema_id")),
        sa.CheckConstraint(_in("origem", ORIGENS_CONTATO), name=op.f(f"ck_{t}_origem")),
        schema=SCHEMA,
    )

    t = "threema_grupo"
    op.create_table(
        t,
        _uuid_pk(),
        sa.Column("nome", sa.String(80), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_threema_grupo_nome_lower", t, [sa.text("lower(nome)")], unique=True, schema=SCHEMA
    )

    t = "threema_grupo_membro"
    op.create_table(
        t,
        _uuid_pk(),
        sa.Column("grupo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipo", sa.String(10), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("contato_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ordem", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        sa.ForeignKeyConstraint(
            ["grupo_id"],
            [f"{SCHEMA}.threema_grupo.id"],
            name=op.f(f"fk_{t}_grupo_id_threema_grupo"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.users.id"],
            name=op.f(f"fk_{t}_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["contato_id"],
            [f"{SCHEMA}.threema_contato.id"],
            name=op.f(f"fk_{t}_contato_id_threema_contato"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(_in("tipo", TIPOS_MEMBRO), name=op.f(f"ck_{t}_tipo")),
        sa.CheckConstraint(
            "num_nonnulls(user_id, contato_id) = 1 "
            "AND (tipo = 'usuario') = (user_id IS NOT NULL) "
            "AND (tipo = 'contato') = (contato_id IS NOT NULL)",
            name=op.f(f"ck_{t}_referencia"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_threema_grupo_membro_usuario",
        t,
        ["grupo_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_threema_grupo_membro_contato",
        t,
        ["grupo_id", "contato_id"],
        unique=True,
        postgresql_where=sa.text("contato_id IS NOT NULL"),
        schema=SCHEMA,
    )

    t = "threema_envio_destinatario"
    op.create_table(
        t,
        _uuid_pk(),
        sa.Column("envio_chave", sa.String(60), nullable=False),
        sa.Column("logistica_status_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tipo", sa.String(10), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("grupo_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("contato_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ordem", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("criado_por", sa.String(120), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        sa.ForeignKeyConstraint(
            ["envio_chave"],
            [f"{SCHEMA}.threema_envio.chave"],
            name=op.f(f"fk_{t}_envio_chave_threema_envio"),
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["logistica_status_id"],
            [f"{SCHEMA}.logistica_status.id"],
            name=op.f(f"fk_{t}_logistica_status_id_logistica_status"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.users.id"],
            name=op.f(f"fk_{t}_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["grupo_id"],
            [f"{SCHEMA}.threema_grupo.id"],
            name=op.f(f"fk_{t}_grupo_id_threema_grupo"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contato_id"],
            [f"{SCHEMA}.threema_contato.id"],
            name=op.f(f"fk_{t}_contato_id_threema_contato"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(_in("tipo", TIPOS_DESTINATARIO), name=op.f(f"ck_{t}_tipo")),
        sa.CheckConstraint(
            "num_nonnulls(user_id, grupo_id, contato_id) = 1 "
            "AND (tipo = 'usuario') = (user_id IS NOT NULL) "
            "AND (tipo = 'grupo') = (grupo_id IS NOT NULL) "
            "AND (tipo = 'contato') = (contato_id IS NOT NULL)",
            name=op.f(f"ck_{t}_referencia"),
        ),
        schema=SCHEMA,
    )
    regra = sa.text(f"coalesce(logistica_status_id, '{SEM_REGRA}'::uuid)")
    for ref, nome in (("user_id", "usuario"), ("grupo_id", "grupo"), ("contato_id", "contato")):
        op.create_index(
            f"uq_threema_envio_destinatario_{nome}",
            t,
            ["envio_chave", regra, ref],
            unique=True,
            postgresql_where=sa.text(f"{ref} IS NOT NULL"),
            schema=SCHEMA,
        )

    t = "threema_cadastro_estado"
    op.create_table(
        t,
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("usar_legado", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("alterado_por", sa.String(120), nullable=True),
        sa.Column("alterado_em", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        sa.CheckConstraint("id = 1", name=op.f(f"ck_{t}_uma_linha")),
        schema=SCHEMA,
    )
    op.execute(f"INSERT INTO {SCHEMA}.{t} (id, usar_legado) VALUES (1, false)")  # noqa: S608


def downgrade() -> None:
    op.drop_table("threema_cadastro_estado", schema=SCHEMA)
    for nome in ("contato", "grupo", "usuario"):
        op.drop_index(
            f"uq_threema_envio_destinatario_{nome}",
            table_name="threema_envio_destinatario",
            schema=SCHEMA,
        )
    op.drop_table("threema_envio_destinatario", schema=SCHEMA)
    op.drop_index(
        "uq_threema_grupo_membro_contato", table_name="threema_grupo_membro", schema=SCHEMA
    )
    op.drop_index(
        "uq_threema_grupo_membro_usuario", table_name="threema_grupo_membro", schema=SCHEMA
    )
    op.drop_table("threema_grupo_membro", schema=SCHEMA)
    op.drop_index("ix_threema_grupo_nome_lower", table_name="threema_grupo", schema=SCHEMA)
    op.drop_table("threema_grupo", schema=SCHEMA)
    op.drop_table("threema_contato", schema=SCHEMA)
    op.drop_table("threema_envio", schema=SCHEMA)
