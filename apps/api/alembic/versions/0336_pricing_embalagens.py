"""pricing_products: embalagens (subpasta da caixa no MEGA) + quando contou

Eduardo, 29/09/2026: "em tabela de preços, produtos, um campo do lado de
fotos, chamado EMBALAGENS, onde vamos concentrar todas as nossas fotos de
embalagens, caixa etc, vai ir para o mega normal, mesmo processo de fotos, só
que um campo separado".

As embalagens moram na subpasta "Embalagens" DENTRO da pasta de fotos do
produto — a linha continua tendo UMA pasta no MEGA, e a caixa vai junto
quando a pasta muda de lugar. Por isso só colunas novas, sem tabela:

- embalagens_url / embalagens_path: link público e caminho da subpasta
  (gravados no envio, ou pela contagem quando a pasta foi criada à mão).
- embalagens_count: arquivos lá dentro (NULL = nunca contado).
- midias_contadas_em: quando fotos/vídeos/embalagens foram contados pela
  última vez — a tela mostra para o operador saber se o número está velho.

Só ADD COLUMN nulável, sem default: não reescreve a tabela (116 linhas).

Também recria as funções do Histórico (app/historico/sql.py): a
`midias_contadas_em` entrou na lista de ruído do gatilho — senão cada
recontagem feita por uma pessoa gravava uma "alteração" por produto com
pasta, mesmo sem nada mudar. As funções só são instaladas pela 0331; aqui é
o mesmo laço dela. Só CREATE OR REPLACE FUNCTION: não trava tabela.

Revision ID: 0336_pricing_embalagens
Revises: 0335_vw_perfis_grupo_lojas
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.historico import sql as hsql

revision: str = "0336_pricing_embalagens"
down_revision: str | None = "0335_vw_perfis_grupo_lojas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "pricing_products", sa.Column("embalagens_url", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "pricing_products", sa.Column("embalagens_path", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "pricing_products",
        sa.Column("embalagens_count", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "pricing_products",
        sa.Column("midias_contadas_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    # Gatilho do Histórico com `midias_contadas_em` no ruído (ver docstring).
    # Depois do deploy: o prosrc de davinci.historico_captura tem de conter
    # "midias_contadas_em".
    for comando in hsql.funcoes(SCHEMA):
        op.execute(comando)


def downgrade() -> None:
    for coluna in ("midias_contadas_em", "embalagens_count", "embalagens_path", "embalagens_url"):
        op.drop_column("pricing_products", coluna, schema=SCHEMA)
