"""Catálogo ML na Tabela de Preços: preço de catálogo, conta "filha" e a marca do anúncio

Pedido do Eduardo (05/10/2026, ~/Downloads/catalogo-mercado-livre.md) e
análise de 06/10: o catálogo deixa de ser uma categoria separada e vira mais
um preço do produto, com colunas próprias por conta ML (clássico/premium).

- pricing_products.preco_catalogo: custo base do anúncio de catálogo (como
  Kit 1..8). NULL = sem preço de catálogo (a célula mostra "—" e não envia).
- pricing_accounts.canal / conta_base_id: a coluna de catálogo é uma conta
  "filha" (canal='catalogo') de uma conta ML de kit. Ela NÃO tem integração
  de propósito: as views/funções que escolhem conta pela integração (Margem,
  frete projetado, Lojas) continuam enxergando só a conta de kit. Os números
  (comissão, margens, fretes) vêm da base na hora do cálculo. Uma filha por
  base; apagar a base apaga a filha.
- product_links: a marca de catálogo gravada pela varredura diária de
  vínculos, que já lê o item inteiro do ML (sem chamada nova):
  catalog_listing (NULL = ainda não lido), catalog_product_id,
  catalogo_relacionado (anúncios de item_relations, separados por vírgula —
  catálogo preso ao anúncio comum) e catalogo_lido_em. anuncio_status guarda
  o status lido (active/paused/under_review/closed/inactive) para a tela
  saber, sem perguntar ao ML, que o anúncio de catálogo está pausado.

Só ADD COLUMN (o default constante de `canal` não reescreve a tabela) e
constraints que as linhas de hoje já cumprem (todas viram canal='kit').
`in_catalog` e o valor 'catalogo' do enum ficam: saem na 2ª entrega.

Revision ID: 0377_catalogo_ml
Revises: 0376_imobilizado
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0377_catalogo_ml"
down_revision: str | None = "0376_imobilizado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

_CK_CANAL = "ck_pricing_accounts_canal_valido"
_CK_BASE = "ck_pricing_accounts_canal_conta_base"
_FK_BASE = "fk_pricing_accounts_conta_base_id_pricing_accounts"
_UQ_BASE = "uq_pricing_accounts_catalogo_por_base"
_IX_CATALOGO = "ix_product_links_catalogo"

_LINK_COLUNAS = (
    "anuncio_status",
    "catalogo_lido_em",
    "catalogo_relacionado",
    "catalog_product_id",
    "catalog_listing",
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")

    op.add_column(
        "pricing_products",
        sa.Column("preco_catalogo", sa.Numeric(10, 2), nullable=True),
        schema=SCHEMA,
    )

    op.add_column(
        "pricing_accounts",
        sa.Column("canal", sa.Text(), nullable=False, server_default=sa.text("'kit'")),
        schema=SCHEMA,
    )
    op.add_column(
        "pricing_accounts",
        sa.Column("conta_base_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f(_FK_BASE),
        "pricing_accounts",
        "pricing_accounts",
        ["conta_base_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f(_CK_CANAL), "pricing_accounts", "canal IN ('kit', 'catalogo')", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f(_CK_BASE),
        "pricing_accounts",
        "(canal = 'catalogo' AND conta_base_id IS NOT NULL AND integration_id IS NULL)"
        " OR (canal = 'kit' AND conta_base_id IS NULL)",
        schema=SCHEMA,
    )
    op.create_index(
        _UQ_BASE,
        "pricing_accounts",
        ["conta_base_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("canal = 'catalogo'"),
    )

    op.add_column(
        "product_links", sa.Column("catalog_listing", sa.Boolean(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "product_links", sa.Column("catalog_product_id", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "product_links",
        sa.Column("catalogo_relacionado", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "product_links",
        sa.Column("catalogo_lido_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "product_links", sa.Column("anuncio_status", sa.Text(), nullable=True), schema=SCHEMA
    )
    # A Tabela de Preços carrega de uma vez os anúncios de catálogo de cada
    # integração (poucos: ~440 em maio) — índice parcial, nasce vazio.
    op.create_index(
        _IX_CATALOGO,
        "product_links",
        ["integration_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("catalog_listing IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index(_IX_CATALOGO, table_name="product_links", schema=SCHEMA)
    for coluna in _LINK_COLUNAS:
        op.drop_column("product_links", coluna, schema=SCHEMA)
    op.drop_index(_UQ_BASE, table_name="pricing_accounts", schema=SCHEMA)
    op.drop_constraint(op.f(_CK_BASE), "pricing_accounts", schema=SCHEMA, type_="check")
    op.drop_constraint(op.f(_CK_CANAL), "pricing_accounts", schema=SCHEMA, type_="check")
    op.drop_constraint(op.f(_FK_BASE), "pricing_accounts", schema=SCHEMA, type_="foreignkey")
    # Antes de tirar as colunas: as filhas (canal='catalogo') somem — sem a
    # coluna `canal` elas virariam contas de kit sem integração.
    op.execute(f"DELETE FROM {SCHEMA}.pricing_accounts WHERE canal = 'catalogo'")  # noqa: S608
    op.drop_column("pricing_accounts", "conta_base_id", schema=SCHEMA)
    op.drop_column("pricing_accounts", "canal", schema=SCHEMA)
    op.drop_column("pricing_products", "preco_catalogo", schema=SCHEMA)
