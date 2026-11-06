"""garantia_sem_garantia — prazo 0 na Tabela de Preços = produto sem garantia

Decisão do Eduardo (06/11/2026): "se for avulso não tem garantia". A coluna
Garantia (meses) da aba Produtos passa a aceitar 0: o produto é vendido SEM
GARANTIA (o painel mostra "Sem garantia", sem data final). NULL continua sendo
"sem prazo cadastrado".

* `pricing_products.garantia_meses`: o CHECK vai de 1..120 para 0..120.
* `garantias.prazo_meses` e `pricing_garantia_prazo.meses` não têm CHECK de
  faixa (o 0 já cabe); o `ck_garantias_fim_garantia_ok` também não barra o 0
  (prazo 0 = fim NULL).

A volta troca o 0 por NULL nas linhas da Tabela (o CHECK antigo não aceita o
0): esses produtos voltam a "sem prazo cadastrado".

Revision ID: 0460_garantia_sem_garantia
Revises: 0459_garantia_sac
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0460_garantia_sem_garantia"
down_revision: str | None = "0459_garantia_sac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = "davinci"


def upgrade() -> None:
    # pricing_products é tabela quente: se o worker estiver no meio de uma
    # volta, desiste em 5 s em vez de enfileirar a API atrás da migration.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint("ck_pricing_products_garantia_meses", "pricing_products", schema=SCHEMA)
    op.create_check_constraint(
        "garantia_meses",
        "pricing_products",
        "garantia_meses IS NULL OR garantia_meses BETWEEN 0 AND 120",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        f"UPDATE {SCHEMA}.pricing_products SET garantia_meses = NULL"  # noqa: S608
        " WHERE garantia_meses = 0"
    )
    op.drop_constraint("ck_pricing_products_garantia_meses", "pricing_products", schema=SCHEMA)
    op.create_check_constraint(
        "garantia_meses",
        "pricing_products",
        "garantia_meses IS NULL OR garantia_meses BETWEEN 1 AND 120",
        schema=SCHEMA,
    )
