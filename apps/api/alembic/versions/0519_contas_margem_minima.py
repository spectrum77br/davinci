"""Aba Contas: Margem mínima (%) da conta (M-85) — o Custo total sai dela (M-86)

Pedidos M-85 e M-86 do Harry (26/11/2026, aprovados):

- `pricing_accounts.margem_minima_pct` NUMERIC(5,2), de 0 a 100, NULL. Em porcentagem, como o
  `custo_total_pct` (25 = 25%). Um valor por conta: no par do ML (Clássico + Premium) o mesmo
  nas duas linhas; a coluna de catálogo mostra a da conta base. Só o administrador vê e grava.
- M-86: com a Margem mínima preenchida, o Custo total da conta deixa de ser digitado e passa a
  ser calculado pelo servidor (`services/pricing/custo_total.custo_total_pela_margem_minima`) e
  gravado no `custo_total_pct` de sempre. Vazia = tudo como antes.

ESTRUTURA (só aditiva): a coluna e o CHECK. Toda conta nasce com a Margem mínima vazia, então
nada é recalculado aqui: no dia em que for ao ar nenhum Custo total, preço, cartão vermelho ou
Margem muda. O Histórico já cobre a tabela (o gatilho grava a linha inteira): a coluna nova
entra nele sozinha, com o nome "Margem mínima (%)" (historico/nomes.py).

`SET LOCAL lock_timeout` como na 0511: ALTER em `pricing_accounts`, lida a toda hora pela
grade; se algo segurar a tabela, desiste em 5 s e é só rodar de novo.

DOWNGRADE: apaga a coluna (com o CHECK). O Custo total calculado fica gravado como está e volta
a ser digitado.

Revision ID: 0519_contas_margem_minima
Revises: 0518_produto_regras_centro_custo
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0519_contas_margem_minima"
down_revision: str | None = "0518_produto_regras_centro_custo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

TABELA = "pricing_accounts"
COLUNA = "margem_minima_pct"
# Nome final (`op.f`), o mesmo que a convenção do modelo dá (models/base.NAMING_CONVENTION).
CHECK = f"ck_{TABELA}_{COLUNA}"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(TABELA, sa.Column(COLUNA, sa.Numeric(5, 2), nullable=True), schema=SCHEMA)
    op.create_check_constraint(
        op.f(CHECK), TABELA, f"{COLUNA} BETWEEN 0 AND 100", schema=SCHEMA
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")  # ver o upgrade
    # Apagar a coluna apaga o CHECK dela.
    op.drop_column(TABELA, COLUNA, schema=SCHEMA)
