"""A coluna `obs` do produto de importação vira `duimp`.

O operador digita a DUIMP (ex. "26BR0001329175-1") produto a produto, igual ao
cadastro do Bling, que guarda o despacho no produto e não no lote. A `obs`
nunca foi usada (vazia nos 487 produtos de prod), então dá pra renomear em vez
de inchar a tabela com mais uma coluna de texto.
"""

from alembic import op

revision: str = "0224_import_products_duimp"
down_revision: str | None = "0223_store_info_etiqueta_sabado"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.alter_column(
        "import_products",
        "obs",
        new_column_name="duimp",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.alter_column(
        "import_products",
        "duimp",
        new_column_name="obs",
        schema=SCHEMA,
    )
