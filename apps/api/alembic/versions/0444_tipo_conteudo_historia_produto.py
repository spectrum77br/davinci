"""ideia e vídeo ganham `tipo_conteudo`: "historia" ou "produto"

Eduardo, 03/11/2026: a aba Ideias vira "Ideias — Histórias" e "Ideias —
Produtos"; "essas de produto vão ali automaticamente para o feed também, o das
histórias só shorts".

* `marketing_roteiros.tipo_conteudo` e `marketing_creatives.tipo_conteudo`
  (varchar(16), NOT NULL, default 'historia', CHECK nos dois valores).
* Tudo o que já existe vira "historia" — é o comportamento de hoje (desde o
  3ecfae86 o Reel do robô no Instagram vai só para a aba Reels). NÃO se
  classifica pelo SKU: quase toda ideia tem SKU, inclusive as histórias
  (microdramas da Charlots), e tudo iria para o feed. Lote a reclassificar
  vem como lista explícita.

DOWNGRADE: tira as duas colunas. O robô volta a não saber o tipo, e o código
de antes manda tudo só para a aba Reels.

Revision ID: 0444_tipo_conteudo_historia_produto
Revises: 0443_atendimento_modelo_equipe
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0444_tipo_conteudo_historia_produto"
down_revision: str | None = "0443_atendimento_modelo_equipe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABELAS = ("marketing_roteiros", "marketing_creatives")


def upgrade() -> None:
    for tabela in TABELAS:
        op.add_column(
            tabela,
            sa.Column(
                "tipo_conteudo",
                sa.String(16),
                nullable=False,
                server_default="historia",
            ),
            schema=SCHEMA,
        )
        op.create_check_constraint(
            f"ck_{tabela}_tipo_conteudo",
            tabela,
            "tipo_conteudo IN ('historia', 'produto')",
            schema=SCHEMA,
        )


def downgrade() -> None:
    for tabela in TABELAS:
        op.drop_constraint(f"ck_{tabela}_tipo_conteudo", tabela, schema=SCHEMA)
        op.drop_column(tabela, "tipo_conteudo", schema=SCHEMA)
