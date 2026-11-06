"""garantia_sac — prazo único por produto e o SAC do site no Painel de Garantia

Especificação do Eduardo (05/11/2026, "Painel de Garantia: alterações"):

* Prazo da garantia: "cada produto tem um prazo único de garantia, informado
  na Tabela de Preços → Produtos". `pricing_products.garantia_meses` (1 a 120,
  NULL = sem prazo) e `pricing_garantia_prazo`, a vigência + o log de cada
  mudança ("se o prazo aumentar, vale para todas as vendas do produto; se
  diminuir, vale só para as vendas feitas a partir da mudança").
* `garantias.prazo_meses` / `fim_garantia`: o prazo da venda e o fim único
  (entrega + prazo). `fim_hardware`/`fim_software` ficam (a IA do Atendimento
  lê os dois). Nada é semeado: produto sem prazo → "Prazo não cadastrado".
* `garantia_sac`: um caso por conversa de protocolo do formulário
  uranyx.com.br/sac — a ligação com a garantia, quem confirmou ("Garantia
  acionada") e quem encerrou. Tem CPF e endereço em `campos`: fica fora do
  Histórico geral (historico/sql.py, EXCLUIDAS).
* `garantia_log`: as ações `confirmou_atendimento`, `encerrou_atendimento` e
  `vinculou_sac`.

Revision ID: 0459_garantia_sac
Revises: 0458_chamados_fechar_dia
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0459_garantia_sac"
down_revision: str | None = "0458_chamados_fechar_dia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = "davinci"

# Listas FIXAS (não importam a constante viva do modelo).
ACOES_ANTES = (
    "cadastrou",
    "alterou",
    "consultou",
    "registrou_atendimento",
    "recalculou",
    "buscou_cpf",
    "criou_automatico",
    "descartou",
    "reativou",
    "pedido_automatico",
    "marcou_pedido",
    "tirou_pedido",
)
ACOES_NOVAS = ("confirmou_atendimento", "encerrou_atendimento", "vinculou_sac")
ACOES_DEPOIS = (*ACOES_ANTES, *ACOES_NOVAS)
VINCULOS = ("pedido", "nf", "cpf_produto", "manual")
ENCERRAMENTOS = ("pessoa", "terminou")


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def upgrade() -> None:
    # As tabelas quentes (pricing_products, garantias, garantia_log) levam
    # ACCESS EXCLUSIVE: se o worker estiver no meio de uma volta, desiste em
    # 5 s em vez de enfileirar a API inteira atrás da migration (como 0449/0451).
    op.execute("SET LOCAL lock_timeout = '5s'")
    # ── Tabela de Preços: o prazo do produto e a vigência ─────────────────────
    op.add_column(
        "pricing_products",
        sa.Column("garantia_meses", sa.SmallInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "garantia_meses",
        "pricing_products",
        "garantia_meses IS NULL OR garantia_meses BETWEEN 1 AND 120",
        schema=SCHEMA,
    )
    op.create_table(
        "pricing_garantia_prazo",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "pricing_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.pricing_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("meses", sa.SmallInteger(), nullable=True),
        sa.Column("meses_anterior", sa.SmallInteger(), nullable=True),
        sa.Column(
            "vale_desde", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("user_nome", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pricing_garantia_prazo_pricing_product_id_vale_desde",
        "pricing_garantia_prazo",
        ["pricing_product_id", "vale_desde"],
        schema=SCHEMA,
    )

    # ── Garantia: o prazo da venda e o fim único ──────────────────────────────
    op.add_column(
        "garantias", sa.Column("prazo_meses", sa.SmallInteger(), nullable=True), schema=SCHEMA
    )
    op.add_column("garantias", sa.Column("fim_garantia", sa.Date(), nullable=True), schema=SCHEMA)
    op.create_check_constraint(
        "fim_garantia_ok",
        "garantias",
        "fim_garantia IS NULL OR (data_inicio IS NOT NULL AND prazo_meses IS NOT NULL"
        " AND fim_garantia > data_inicio)",
        schema=SCHEMA,
    )
    op.create_index("ix_garantias_fim_garantia", "garantias", ["fim_garantia"], schema=SCHEMA)

    # ── O caso do SAC ─────────────────────────────────────────────────────────
    op.create_table(
        "garantia_sac",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "conversa_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.atendimento_conversas.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("mensagem_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("protocolo", sa.Text(), nullable=False),
        sa.Column("recebido_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "campos",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "garantia_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.garantias.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("vinculo", sa.Text(), nullable=True),
        sa.Column("vinculado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "vinculado_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confirmado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "confirmado_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("encerrado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "encerrado_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("encerrado_motivo", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            f"vinculo IS NULL OR vinculo IN ({_lista(VINCULOS)})", name="vinculo_valido"
        ),
        sa.CheckConstraint(
            f"encerrado_motivo IS NULL OR encerrado_motivo IN ({_lista(ENCERRAMENTOS)})",
            name="encerrado_motivo_valido",
        ),
        sa.CheckConstraint(
            "confirmado_em IS NULL OR garantia_id IS NOT NULL", name="confirmado_com_garantia"
        ),
        sa.CheckConstraint(
            "encerrado_em IS NULL OR confirmado_em IS NOT NULL", name="encerrado_confirmado"
        ),
        sa.CheckConstraint(
            "(encerrado_em IS NULL) = (encerrado_motivo IS NULL)", name="encerrado_com_motivo"
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_garantia_sac_garantia_id", "garantia_sac", ["garantia_id"], schema=SCHEMA)

    # ── As ações novas do log ─────────────────────────────────────────────────
    op.drop_constraint("ck_garantia_log_acao_valida", "garantia_log", schema=SCHEMA)
    op.create_check_constraint(
        "acao_valida", "garantia_log", f"acao IN ({_lista(ACOES_DEPOIS)})", schema=SCHEMA
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    # O log só recebe linha nova (gatilho garantia_so_insercao): a volta tira
    # as ações novas com a marca de expurgo, senão a regra antiga não volta.
    op.execute("SET LOCAL davinci.garantia_expurgo = 'sim'")
    op.execute(
        f"DELETE FROM {SCHEMA}.garantia_log"  # noqa: S608
        f" WHERE acao IN ({_lista(ACOES_NOVAS)})"
    )
    op.drop_constraint("ck_garantia_log_acao_valida", "garantia_log", schema=SCHEMA)
    op.create_check_constraint(
        "acao_valida", "garantia_log", f"acao IN ({_lista(ACOES_ANTES)})", schema=SCHEMA
    )
    op.drop_index("ix_garantia_sac_garantia_id", "garantia_sac", schema=SCHEMA)
    op.drop_table("garantia_sac", schema=SCHEMA)
    op.drop_index("ix_garantias_fim_garantia", "garantias", schema=SCHEMA)
    op.drop_constraint("ck_garantias_fim_garantia_ok", "garantias", schema=SCHEMA)
    op.drop_column("garantias", "fim_garantia", schema=SCHEMA)
    op.drop_column("garantias", "prazo_meses", schema=SCHEMA)
    op.drop_index(
        "ix_pricing_garantia_prazo_pricing_product_id_vale_desde",
        "pricing_garantia_prazo",
        schema=SCHEMA,
    )
    op.drop_table("pricing_garantia_prazo", schema=SCHEMA)
    op.drop_constraint("ck_pricing_products_garantia_meses", "pricing_products", schema=SCHEMA)
    op.drop_column("pricing_products", "garantia_meses", schema=SCHEMA)
