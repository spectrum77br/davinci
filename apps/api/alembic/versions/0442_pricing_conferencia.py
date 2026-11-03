"""conferência de preços da Tabela de Preços: o último preço lido de cada célula

Eduardo, 02/11/2026 (tarefa "Melhorias na Tabela de Preços"): botão só de
administrador com "% margem de erro" que compara o preço da Tabela com o preço
do anúncio em cada conta e marca (!) na célula quando passa da margem.

* `background_job_type` ganha 'conferir_precos' — o job da conferência (fila
  davinci_sync), no padrão da 0163.
* `pricing_conferencia`: uma linha por célula (conta × linha da tabela) com o
  resultado da ÚLTIMA conferência do departamento — o preço da Tabela, a
  situação (conferida / sem_anuncio / erro_conta / nao_suportada) e o preço
  lido de cada anúncio (`anuncios`, JSONB). A margem fica na tela: mudar a %
  não exige conferir de novo. Nasce vazia; apagar a conta ou a linha da tabela
  leva as linhas dela.

DOWNGRADE: apaga os jobs 'conferir_precos' (os background_job_details vão
junto, CASCADE) e a tabela. O valor do enum fica (o Postgres não remove valor
de enum), como na 0163. Os jobs saem porque o código de antes não conhece o
tipo: o BackgroundJobType antigo dá LookupError ao carregar a linha
(/api/jobs, /api/dashboard e o failed_jobs_alert_scan quebram). Reverter só o
CÓDIGO, sem rodar este downgrade, exige o mesmo DELETE antes de subir o
código antigo:
    DELETE FROM davinci.background_jobs WHERE type = 'conferir_precos';

Revision ID: 0442_pricing_conferencia
Revises: 0441_equipes_londres_israel_marrocos
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0442_pricing_conferencia"
down_revision: str | None = "0441_equipes_londres_israel_marrocos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

# FIXO (não importa a constante viva do modelo).
SITUACOES = ("conferida", "sem_anuncio", "erro_conta", "nao_suportada")


def upgrade() -> None:
    op.execute(
        f"ALTER TYPE \"{SCHEMA}\".background_job_type ADD VALUE IF NOT EXISTS 'conferir_precos'"
    )
    situacoes = ", ".join(f"'{s}'" for s in SITUACOES)
    op.create_table(
        "pricing_conferencia",
        sa.Column(
            "pricing_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.pricing_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "pricing_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.pricing_products.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("department", sa.Text(), nullable=False),
        sa.Column("preco_tabela", sa.Numeric(12, 2), nullable=True),
        sa.Column("situacao", sa.Text(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column(
            "anuncios",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.background_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "conferido_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Nome final (`op.f`), o mesmo que a convenção ck_<tabela>_<nome> dá ao modelo.
        sa.CheckConstraint(
            f"situacao IN ({situacoes})", name=op.f("ck_pricing_conferencia_situacao")
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pricing_conferencia_department",
        "pricing_conferencia",
        ["department"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Antes da tabela (o job_id dela é SET NULL): o código de antes não lê
    # job do tipo 'conferir_precos' (ver o topo).
    op.execute(
        f"DELETE FROM \"{SCHEMA}\".background_jobs WHERE type = 'conferir_precos'"  # noqa: S608 — SCHEMA é constante
    )
    op.drop_index(
        "ix_pricing_conferencia_department",
        table_name="pricing_conferencia",
        schema=SCHEMA,
    )
    op.drop_table("pricing_conferencia", schema=SCHEMA)
