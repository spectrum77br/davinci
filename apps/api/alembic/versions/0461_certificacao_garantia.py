"""certificacao_garantia — o prazo da garantia passa a ser do CERTIFICADO

Decisão do Eduardo (06/11/2026): "em certificações adicione o campo garantia,
e aí ele passa a mudar de acordo com o certificado". A Certificações
(Importação › Certificações) ganha a coluna Garantia (meses); mudar ali muda a
coluna Garantia (meses) de todos os produtos da Tabela de Preços daquele
certificado (o gravado na coluna Modelo ou, sem ele, o achado pelo código no
nome) — pelo mesmo caminho do PATCH do produto: vigência + recálculo das
garantias (routers/financeiro.py, services/certificacao_produto.py).

* `financeiro_suprimentos.garantia_meses` SMALLINT NULL, CHECK 0..120
  (0 = sem garantia, como na Tabela). NULL = o certificado não define o prazo
  e os produtos dele continuam com o deles.

Não preenche nada: começa vazio em todos os certificados, e nenhum produto
muda até alguém digitar o prazo no certificado.

DOWNGRADE: tira a coluna (o CHECK vai junto). Os produtos ficam com o prazo
que já tinham recebido.

Revision ID: 0461_certificacao_garantia
Revises: 0460_garantia_sem_garantia
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0461_certificacao_garantia"
down_revision: str | None = "0460_garantia_sem_garantia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"


def upgrade() -> None:
    # A sincronização da Anatel/Inmetro pode estar no meio de uma volta nessa
    # tabela: desiste em 5 s em vez de enfileirar a API atrás da migration.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "financeiro_suprimentos",
        sa.Column("garantia_meses", sa.SmallInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "garantia_meses",
        "financeiro_suprimentos",
        "garantia_meses IS NULL OR garantia_meses BETWEEN 0 AND 120",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("financeiro_suprimentos", "garantia_meses", schema=SCHEMA)
