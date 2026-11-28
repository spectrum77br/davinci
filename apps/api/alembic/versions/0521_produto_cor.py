"""Cor do produto (M-107): tipo "Cor" em Cadastros › Produtos e a cor de cada produto

Pedido M-107 do Harry (27/11/2026, aprovado): na Tabela "Produtos 2" de Cadastros › Produtos, o
tipo novo "Cor" (os nomes entram pelo "Novo cadastro", como os da Categoria); no cadastro do
produto (Produtos 2), o campo "Cor", que só aceita as cores cadastradas, uma por produto, gravada
pelo código sem a tag de envio (`sku_base`, a mesma chave da Margem mín).

ESTRUTURA E DADOS (só aditivos, nada existente é reescrito):
- o TIPO "Cor" em `produto_cadastro_tipos`, com a chave fixa 'cor' (é pela chave, não pelo nome,
  que o servidor reconhece os tipos com regra — Categoria e Tag de envio, da 0508 —; renomear o
  tipo não perde a regra). Item 1 do pedido, "se já existir, usar o existente": se já houver um
  tipo chamado "Cor" (sem olhar maiúsculas), criado pela tela e sem chave, ele GANHA a chave, com
  os nomes que já tiver; senão o tipo nasce. A lista de cores NÃO ganha nome nenhum (item 3: sem
  cores de exemplo);
- a coluna `produto_regras.cor_id` (uuid, vazia), com FK para `produto_cadastros(id)` ON DELETE
  SET NULL (a cor excluída deixa os produtos sem cor; a tela de Cadastros já recusa excluir cor
  em uso, e o SET NULL só cobre a corrida) e índice (a contagem de uso da lixeira).

NO DIA EM QUE FOR AO AR nenhum produto tem cor (item 14: nada é preenchido a partir do nome), a
lista de cores está vazia e nada do que a Margem, a nota ou o robô fazem muda: ninguém lê a cor
além do cadastro e da lista do Produtos 2. O Histórico já cobre `produto_regras` (gatilho da
0516, que grava a linha inteira): a coluna nova entra nele sozinha, com o nome "Cor" e o nome da
cor pela FK (historico/nomes.py).

DOWNGRADE: tira o índice, a FK e a coluna (a cor escolhida em cada produto se perde) e tira a
chave 'cor' do tipo, que fica como um tipo comum da lista, com os nomes que tiver — nada é
apagado de Cadastros › Produtos; um upgrade de novo reencontra o tipo pelo nome e devolve a chave.

`SET LOCAL lock_timeout` como na 0508/0518: `produto_regras` e os cadastros são pequenos, mas a
regra da casa vale para toda migration.

Revision ID: 0521_produto_cor
Revises: 0520_contas_dez_categorias
Create Date: 2026-11-27
"""

# ruff: noqa: S608 — SQL montado só com nomes fixos desta migration (schema), sem entrada de fora
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0521_produto_cor"
down_revision: str | None = "0520_contas_dez_categorias"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

TIPOS = "produto_cadastro_tipos"
CADASTROS = "produto_cadastros"
TABELA = "produto_regras"
COLUNA = "cor_id"
# FIXOS (não importam o modelo vivo): a chave e o nome do tipo; os nomes finais (`op.f`) da FK e
# do índice, os mesmos que a convenção do modelo dá (models/base.NAMING_CONVENTION).
CHAVE = "cor"
NOME = "Cor"
FK = "fk_produto_regras_cor_id_produto_cadastros"
INDICE = "ix_produto_regras_cor_id"


def tipo_cor(bind) -> str:
    """O tipo "Cor" com a chave 'cor': o que já existe (pela chave ou pelo nome) ou um novo.
    Devolve o que foi feito, para o log."""
    s = SCHEMA
    ja = bind.execute(
        sa.text(f"SELECT id FROM {s}.{TIPOS} WHERE chave = :chave"), {"chave": CHAVE}
    ).first()
    if ja is not None:
        return "já existia com a regra"
    pelo_nome = bind.execute(
        sa.text(
            f"UPDATE {s}.{TIPOS} SET chave = :chave, updated_at = now() "
            "WHERE lower(btrim(nome)) = lower(:nome) AND chave IS NULL RETURNING id"
        ),
        {"chave": CHAVE, "nome": NOME},
    ).first()
    if pelo_nome is not None:
        n = bind.execute(
            sa.text(f"SELECT count(*) FROM {s}.{CADASTROS} WHERE tipo_id = :t"),
            {"t": pelo_nome.id},
        ).scalar_one()
        return f"já existia sem a regra ({n} nome(s)) e ganhou a regra"
    bind.execute(
        sa.text(f"INSERT INTO {s}.{TIPOS} (nome, chave) VALUES (:nome, :chave)"),
        {"nome": NOME, "chave": CHAVE},
    )
    return "criado, sem nenhuma cor"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    feito = tipo_cor(op.get_bind())
    print(f"[0521] tipo Cor (Cadastros › Produtos): {feito}")  # noqa: T201
    op.add_column(
        TABELA,
        sa.Column(COLUNA, postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f(FK),
        TABELA,
        CADASTROS,
        [COLUNA],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_index(op.f(INDICE), TABELA, [COLUNA], schema=SCHEMA)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")  # ver o upgrade
    op.drop_index(op.f(INDICE), table_name=TABELA, schema=SCHEMA)
    op.drop_constraint(op.f(FK), TABELA, type_="foreignkey", schema=SCHEMA)
    op.drop_column(TABELA, COLUNA, schema=SCHEMA)
    # O tipo fica (com os nomes que tiver), só sem a regra: nada sai de Cadastros › Produtos.
    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.{TIPOS} SET chave = NULL, updated_at = now() WHERE chave = :chave"
        ).bindparams(chave=CHAVE)
    )
