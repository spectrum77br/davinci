"""Centro de custo POR PRODUTO no cadastro do Produtos 2 (M-73)

Pedido M-73 do Harry (26/11/2026): na linha 2 do cadastro do produto, o campo "Centro de custo"
(menu com as opções de Cadastros › Centro de custo), guardado por produto.

ESTRUTURA (só aditiva): a coluna `produto_regras.centro_custo_id` (uuid, vazia), com FK para
`centros_custo(id)` ON DELETE RESTRICT (um centro escolhido por algum produto não sai; a aba
Centro de custo já recusa antes, dizendo quantos produtos usam) e índice (a contagem de uso).

VAZIO = o departamento do produto, CALCULADO e NÃO copiado: o da linha da Tabela de Preços; sem
linha, o da categoria (services/produto_regras.centro_herdado). Por isso a coluna nasce vazia em
todos os produtos e nada é preenchido aqui: no dia em que for ao ar, todo produto mostra o
departamento que já tem hoje, e nenhuma tela, margem, preço ou nota muda.

A chave é a mesma da M-74 (`sku_base`: o código sem a tag de envio). O Histórico já cobre a
tabela (gatilho da 0516, que grava a linha inteira): a coluna nova entra nele sozinha, com o nome
"Centro de custo" (historico/nomes.py).

DOWNGRADE: tira o índice, a FK e a coluna (o centro escolhido em cada produto se perde; o produto
volta a mostrar o departamento).

`SET LOCAL lock_timeout` como na 0508/0517: `produto_regras` é pequena, mas a regra da casa vale
para toda migration.

Revision ID: 0518_produto_regras_centro_custo
Revises: 0517_centros_custo
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0518_produto_regras_centro_custo"
down_revision: str | None = "0517_centros_custo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

TABELA = "produto_regras"
COLUNA = "centro_custo_id"
# Nomes finais (`op.f`), os mesmos que a convenção do modelo dá (models/base.NAMING_CONVENTION).
FK = "fk_produto_regras_centro_custo_id_centros_custo"
INDICE = "ix_produto_regras_centro_custo_id"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        TABELA,
        sa.Column(COLUNA, postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f(FK),
        TABELA,
        "centros_custo",
        [COLUNA],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_index(op.f(INDICE), TABELA, [COLUNA], schema=SCHEMA)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")  # ver o upgrade
    op.drop_index(op.f(INDICE), table_name=TABELA, schema=SCHEMA)
    op.drop_constraint(op.f(FK), TABELA, type_="foreignkey", schema=SCHEMA)
    op.drop_column(TABELA, COLUNA, schema=SCHEMA)
