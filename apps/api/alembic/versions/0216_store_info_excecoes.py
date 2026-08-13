# ruff: noqa: E501
"""store_info.excecoes: regras de exceção de envio por loja.

Campo "Exceções" da tela Lojas: regras configuráveis POR LOJA que bloqueiam
o envio no sweep automático de NF (mesmo tratamento da restrição hardcoded
Shopee Apple→RJ, que continua existindo — isto é ADICIONAL, só pras lojas
que o usuário escolher). Lista JSONB de regras:

    [{"uf": "RJ", "tipo": "valor",   "valor": 700},
     {"uf": "RJ", "tipo": "sku",     "termos": ["a001"]},
     {"uf": "RJ", "tipo": "palavra", "termos": ["apple"]}]

- tipo "valor":   bloqueia pedido pra UF com valor total >= `valor`
                  ("não envia nada pro RJ a menos que seja abaixo de 700");
- tipo "sku":     bloqueia se algum item do pedido tem um dos SKUs;
- tipo "palavra": bloqueia se o nome de algum item contém uma das palavras.

NULL/lista vazia = loja sem exceções (comportamento atual).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0216_store_info_excecoes"
down_revision: str | None = "0215_bling_orders_documento_destinatario"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "store_info",
        sa.Column("excecoes", JSONB(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("store_info", "excecoes", schema=SCHEMA)
