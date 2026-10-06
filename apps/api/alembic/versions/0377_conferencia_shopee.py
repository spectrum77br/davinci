"""Conferência Shopee: lojas, execuções, coletas e saldos de Ads

Relatório de marketing da Shopee de terça (semana fechada) e quinta (parcial
seg–qua), com as lojas em Mala · Celular · Eletro, que substitui o
`conferencia.js` (COMO-MONTA-O-RELATORIO.md, 06/10/2026). Doc para a equipe:
docs/conferencia-shopee.md.

O que cria (só tabelas novas):
  • `conferencia_shopee_conta` — a lista de lojas, já com as 20 do documento
    (VR, Eron e Lucas MEI desativadas). Editada por pessoas na tela: fica COM o
    gatilho do Histórico (o cron diário `historico_manutencao` põe).
  • `conferencia_shopee_execucao`, `conferencia_shopee_coleta` e
    `conferencia_shopee_saldo` — da máquina (historico/sql.EXCLUIDAS).
  • a linha `conferencia_shopee` do cadastro "Quem recebe" do Informar
    (threema_informar_config), vazia: ninguém recebe até alguém escolher.

Valores fechados em TEXT com CHECK (nada de enum do Postgres). Os seeds são
idempotentes (WHERE NOT EXISTS). `tests/test_conferencia_shopee_migration.py`
roda esta migration num schema descartável e compara o catálogo com o model.

O downgrade apaga as quatro tabelas (o histórico de saldos se perde) e a linha
do Informar se ninguém mexeu nela.

Revision ID: 0377_conferencia_shopee
Revises: 0376_imobilizado
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0377_conferencia_shopee"
down_revision: str | None = "0376_imobilizado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
CONTEXTO = "conferencia_shopee"

# Mesmos valores de app/models/conferencia_shopee.py (a migration não importa o
# app: o model pode mudar depois, a migration publicada não).
_GRUPOS = ("mala", "celular")
_TIPOS = ("semanal", "parcial")
_ORIGENS = ("agenda", "manual")
_EXECUCAO_STATUS = ("coletando", "pronto", "cancelado")
_COLETA_STATUS = (
    "pendente",
    "coletando",
    "ok",
    "parcial",
    "deslogada",
    "perfil_em_uso",
    "sem_automacao",
    "bloqueada",
    "interrompida",
    "erro",
    "expirada",
)

# (adspower_user_id, nome, grupo, ativo, conta_key). Chave = id do perfil no
# AdsPower: o número do perfil (profile_no) é renumerado, o id não.
_CONTAS = (
    ("k1doe92i", "Inova", "mala", True, "inova"),
    ("k1dkfd6r", "KFA", "mala", True, "kfa"),
    ("k1dp7f2n", "Minas", "mala", True, "minas"),
    ("k1dohvrc", "Poofy", "mala", True, "poofy"),
    ("k1dpc1bl", "Aguiar", "celular", True, "aguiar"),
    ("k1dkfg0k", "Atlas", "celular", True, "atlas"),
    ("k1docw53", "Atv", "celular", True, "atv"),
    ("k1dkeaxv", "Barbosa", "celular", True, "barbosa"),
    ("k1dof8ph", "Fiore", "celular", True, "fiore"),
    ("k1dodnll", "Injox", "celular", True, "injox"),
    ("k1dofott", "Luno", "celular", True, "luno"),
    ("k1dpnde9", "Mega", "celular", True, "mega"),
    ("k1dkf1le", "Mini", "celular", True, "mini"),
    ("k16yrimx", "Oliveira", "celular", True, "oliveira"),
    ("k1dkelj1", "Victor", "celular", True, "victor mei"),
    ("k1do5vfx", "Vita", "celular", True, "vita"),
    ("k1dofh5v", "Vortan", "celular", True, "vortan"),
    ("k1hnnecn", "VR", "celular", False, "vr"),
    ("k1gxjuj7", "Eron", "celular", False, "eron"),
    ("k1doffcx", "Lucas MEI", "celular", False, "lucas mei"),
)


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    lista = ", ".join(f"'{v}'" for v in valores)
    return f"{coluna} IN ({lista})"


def _agora() -> sa.TextClause:
    return sa.text("now()")


def _uuid() -> sa.Column:
    return sa.Column(
        "id",
        pg.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def upgrade() -> None:
    # Nomes de constraint com op.f(): já são os finais da convenção do model
    # (ck_<tabela>_<nome>); sem isso o alembic passaria de novo por ela.

    # ---- conferencia_shopee_conta ------------------------------------------
    op.create_table(
        "conferencia_shopee_conta",
        _uuid(),
        sa.Column("adspower_user_id", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("grupo", sa.Text(), nullable=False),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("ordem", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("conta_key", sa.Text(), nullable=True),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False
        ),
        sa.CheckConstraint(_in("grupo", _GRUPOS), name=op.f("ck_conferencia_shopee_conta_grupo")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conferencia_shopee_conta")),
        sa.UniqueConstraint(
            "adspower_user_id", name=op.f("uq_conferencia_shopee_conta_adspower_user_id")
        ),
        schema=SCHEMA,
    )

    # ---- conferencia_shopee_execucao ---------------------------------------
    op.create_table(
        "conferencia_shopee_execucao",
        _uuid(),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("origem", sa.Text(), nullable=False),
        sa.Column("criado_por", sa.Text(), nullable=True),
        sa.Column("semanas", pg.JSONB(), nullable=False),
        sa.Column("afiliados_ate", sa.Date(), nullable=False),
        sa.Column("esperar_afiliados_ate", sa.DateTime(timezone=True), nullable=False),
        sa.Column("corte", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prazo", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'coletando'"), nullable=False),
        sa.Column("relatorio", pg.JSONB(), nullable=True),
        sa.Column("finalizado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("threema_enviado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.CheckConstraint(_in("tipo", _TIPOS), name=op.f("ck_conferencia_shopee_execucao_tipo")),
        sa.CheckConstraint(
            _in("origem", _ORIGENS), name=op.f("ck_conferencia_shopee_execucao_origem")
        ),
        sa.CheckConstraint(
            _in("status", _EXECUCAO_STATUS), name=op.f("ck_conferencia_shopee_execucao_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conferencia_shopee_execucao")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conferencia_shopee_execucao_criado_em",
        "conferencia_shopee_execucao",
        [sa.text("criado_em DESC")],
        schema=SCHEMA,
    )

    # ---- conferencia_shopee_coleta -----------------------------------------
    op.create_table(
        "conferencia_shopee_coleta",
        _uuid(),
        sa.Column("execucao_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("conta_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("adspower_user_id", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("grupo", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pendente'"), nullable=False),
        sa.Column("tentativas", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("adiamentos", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "adiamentos_perfil", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("disponivel_apos", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agente", sa.Text(), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("dados", pg.JSONB(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.CheckConstraint(
            _in("status", _COLETA_STATUS), name=op.f("ck_conferencia_shopee_coleta_status")
        ),
        sa.ForeignKeyConstraint(
            ["execucao_id"],
            [f"{SCHEMA}.conferencia_shopee_execucao.id"],
            name=op.f("fk_conferencia_shopee_coleta_execucao"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conta_id"],
            [f"{SCHEMA}.conferencia_shopee_conta.id"],
            name=op.f("fk_conferencia_shopee_coleta_conta"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conferencia_shopee_coleta")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conferencia_shopee_coleta_execucao_id",
        "conferencia_shopee_coleta",
        ["execucao_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conferencia_shopee_coleta_status_disponivel_apos",
        "conferencia_shopee_coleta",
        ["status", "disponivel_apos"],
        schema=SCHEMA,
    )

    # ---- conferencia_shopee_saldo ------------------------------------------
    op.create_table(
        "conferencia_shopee_saldo",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("adspower_user_id", sa.Text(), nullable=False),
        sa.Column("lido_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valor", sa.Numeric(14, 2), nullable=False),
        sa.Column("execucao_id", pg.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conferencia_shopee_saldo")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conferencia_shopee_saldo_adspower_user_id_lido_em",
        "conferencia_shopee_saldo",
        ["adspower_user_id", "lido_em"],
        schema=SCHEMA,
    )

    _semear()


def _semear() -> None:
    """As 20 lojas e a linha do Informar — idempotente (o teste roda duas vezes)."""
    valores = ",\n".join(
        f"('{uid}', '{nome}', '{grupo}', {'true' if ativo else 'false'}, '{chave}')"
        for uid, nome, grupo, ativo, chave in _CONTAS
    )
    op.execute(
        f'INSERT INTO "{SCHEMA}".conferencia_shopee_conta '  # noqa: S608
        "(id, adspower_user_id, nome, grupo, ativo, conta_key) "
        "SELECT gen_random_uuid(), v.adspower_user_id, v.nome, v.grupo, v.ativo, v.conta_key "
        f"FROM (VALUES {valores}) AS v(adspower_user_id, nome, grupo, ativo, conta_key) "
        f'WHERE NOT EXISTS (SELECT 1 FROM "{SCHEMA}".conferencia_shopee_conta c '
        "WHERE c.adspower_user_id = v.adspower_user_id)"
    )
    op.execute(
        f'INSERT INTO "{SCHEMA}".threema_informar_config (id, contexto, recipients) '  # noqa: S608
        f"SELECT gen_random_uuid(), '{CONTEXTO}', '' "
        f'WHERE NOT EXISTS (SELECT 1 FROM "{SCHEMA}".threema_informar_config '
        f"WHERE contexto = '{CONTEXTO}')"
    )


def downgrade() -> None:
    op.execute(
        f'DELETE FROM "{SCHEMA}".threema_informar_config '  # noqa: S608
        f"WHERE contexto = '{CONTEXTO}' AND recipients = ''"
    )
    op.drop_index(
        "ix_conferencia_shopee_saldo_adspower_user_id_lido_em",
        table_name="conferencia_shopee_saldo",
        schema=SCHEMA,
    )
    op.drop_table("conferencia_shopee_saldo", schema=SCHEMA)
    op.drop_index(
        "ix_conferencia_shopee_coleta_status_disponivel_apos",
        table_name="conferencia_shopee_coleta",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_conferencia_shopee_coleta_execucao_id",
        table_name="conferencia_shopee_coleta",
        schema=SCHEMA,
    )
    op.drop_table("conferencia_shopee_coleta", schema=SCHEMA)
    op.drop_index(
        "ix_conferencia_shopee_execucao_criado_em",
        table_name="conferencia_shopee_execucao",
        schema=SCHEMA,
    )
    op.drop_table("conferencia_shopee_execucao", schema=SCHEMA)
    # As contas do seed somem com a tabela (é ela que esta migration cria).
    op.drop_table("conferencia_shopee_conta", schema=SCHEMA)
