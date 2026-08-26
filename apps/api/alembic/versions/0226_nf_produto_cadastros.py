"""Cadastros da NF PRODUTO (spec Eduardo 26/08) — 4 colunas novas.

1. `store_info.nf_faturador_produto_id` — faturador que emite a NF PRODUTO da
   loja (coluna "Faturador produto" na tela Lojas, ao lado do Faturador atual).
2. `nf_faturador.observacao_duimp` — sim/não (igual `nf_cheia`): quando sim, a
   nota leva o número da DUIMP cadastrado na Importação do produto.
3. `nf_faturador.ncm_fonte` — de onde vem o NCM da nota: NULL = NCM padrão
   (fixo, campo `ncm`); 'importacao' = NCM do produto de importação, com o
   padrão como reserva se o produto não tiver NCM.
4. `import_products.ncm` — NCM por produto (coluna depois da DUIMP na tela
   Importação).

Tudo CADASTRO: a regra de emissão que usa esses campos é programada depois —
a emissão atual não muda.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0226_nf_produto_cadastros"
down_revision: str | None = "0225_import_products_duimp"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "store_info",
        sa.Column("nf_faturador_produto_id", PG_UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_store_info_nf_faturador_produto_id",
        "store_info",
        "nf_faturador",
        ["nf_faturador_produto_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.add_column(
        "nf_faturador",
        sa.Column(
            "observacao_duimp",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "nf_faturador",
        sa.Column("ncm_fonte", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_products",
        sa.Column("ncm", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("import_products", "ncm", schema=SCHEMA)
    op.drop_column("nf_faturador", "ncm_fonte", schema=SCHEMA)
    op.drop_column("nf_faturador", "observacao_duimp", schema=SCHEMA)
    op.drop_constraint(
        "fk_store_info_nf_faturador_produto_id",
        "store_info",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("store_info", "nf_faturador_produto_id", schema=SCHEMA)
