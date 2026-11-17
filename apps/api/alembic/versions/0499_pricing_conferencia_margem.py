"""margem de erro da conferência no servidor: a Geral por departamento e a de cada produto

Eduardo, 17/11/2026 (M-2 da Tabela de Preços): a faixa roxa sai e a barra ganha
"Margem 5% ▾" com [x] Geral [5] % e [x] Por produto (um % para cada produto do
departamento). "Quando um produto tem desconto, cadastro a margem dele aqui e
a tabela para de mostrar irregularidade nele." Decisão do aprovador: a margem
fica NO SERVIDOR e vale para todos (antes morava no navegador de cada
administrador, `precos.margemErroPct`). O (!) "anúncio X" e o cartão vermelho
(M-3) usam a margem do produto ou, sem ela, a Geral.

* `pricing_conferencia_margem`: uma linha por departamento (o mesmo slug do
  `?department=` da conferência). Sem linha = 5% e as duas caixas ligadas.
  `geral_ligada` desligada: produto sem margem própria não é conferido;
  `por_produto_ligada` desligada: as margens próprias são ignoradas.
* `pricing_conferencia_margem_produto`: a margem própria de uma linha da
  Tabela (`pricing_products.id`). Apagar a linha da Tabela leva a margem
  (CASCADE); apagar a margem volta o produto para a Geral.

Nascem vazias. Nenhum dado existente muda.

DOWNGRADE: apaga as duas tabelas (as margens gravadas se perdem).

Revision ID: 0499_pricing_conferencia_margem
Revises: 0498_carrefour_offer_write
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0499_pricing_conferencia_margem"
down_revision: str | None = "0498_carrefour_offer_write"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

# FIXO (não importa a constante viva do modelo): a margem é % de 0 a 100.
CHECK_MARGEM = "margem_pct BETWEEN 0 AND 100"


def _quem_e_quando() -> list[sa.Column]:
    return [
        sa.Column(
            "atualizado_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "pricing_conferencia_margem",
        sa.Column("department", sa.Text(), primary_key=True),
        sa.Column(
            "margem_pct", sa.Numeric(5, 2), nullable=False, server_default=sa.text("5")
        ),
        sa.Column(
            "geral_ligada", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "por_produto_ligada", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        *_quem_e_quando(),
        # Nome final (`op.f`), o mesmo que a convenção ck_<tabela>_<nome> dá ao modelo.
        sa.CheckConstraint(CHECK_MARGEM, name=op.f("ck_pricing_conferencia_margem_margem_pct")),
        schema=SCHEMA,
    )
    op.create_table(
        "pricing_conferencia_margem_produto",
        sa.Column(
            "pricing_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.pricing_products.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("margem_pct", sa.Numeric(5, 2), nullable=False),
        *_quem_e_quando(),
        sa.CheckConstraint(
            CHECK_MARGEM, name=op.f("ck_pricing_conferencia_margem_produto_margem_pct")
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("pricing_conferencia_margem_produto", schema=SCHEMA)
    op.drop_table("pricing_conferencia_margem", schema=SCHEMA)
