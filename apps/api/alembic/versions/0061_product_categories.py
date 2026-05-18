"""product categories catalog

Revision ID: 0061_product_categories
Revises: 0060_vw_conciliacao_margens_freight_coalesce
Create Date: 2026-05-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0061_product_categories"
down_revision: str | None = "0060_vw_conciliacao_margens_freight_coalesce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

CATEGORIES: tuple[tuple[int, str, int | None], ...] = (
    (8680318, "Celular", None),
    (9164372, "Mala", None),
    (9860107, "Embalagem", None),
    (10800334, "Celular Kit", None),
    (10804361, "Mala Kit", None),
    (10926683, "Celular Usado", None),
    (11444263, "Acessórios Celular", None),
    (12247095, "Insumos", None),
    (12947511, "Mala Usada", None),
    (12947515, "Eletro", None),
    (12947521, "Eletro Kit", None),
    (12947523, "Eletro Usado", None),
)


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    bind = op.get_bind()
    table_exists = bind.execute(
        sa.text("SELECT to_regclass(:table_name)"),
        {"table_name": "davinci.product_categories"},
    ).scalar_one()
    if table_exists is None:
        op.create_table(
            "product_categories",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("bling_category_id", sa.BigInteger(), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("parent_bling_category_id", sa.BigInteger(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["parent_bling_category_id"],
                [f"{SCHEMA}.product_categories.bling_category_id"],
                ondelete="SET NULL",
                name="fk_product_categories_parent_bling_cat_id",
            ),
            sa.UniqueConstraint(
                "bling_category_id",
                name="uq_product_categories_bling_category_id",
            ),
            sa.CheckConstraint(
                "length(trim(name)) > 0",
                name="ck_product_categories_name_not_blank",
            ),
            schema=SCHEMA,
        )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_product_categories_parent_bling_category_id
        ON davinci.product_categories (parent_bling_category_id)
        """
    )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_product_categories_updated_at "
        "ON davinci.product_categories"
    )
    op.execute(
        f'CREATE TRIGGER trg_product_categories_updated_at '
        f'BEFORE UPDATE ON "{SCHEMA}".product_categories '
        f'FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at()'
    )

    for bling_category_id, name, parent_bling_category_id in CATEGORIES:
        bind.execute(
            sa.text(
                "INSERT INTO davinci.product_categories "
                "(bling_category_id, name, parent_bling_category_id) "
                "VALUES (:bling_category_id, :name, :parent_bling_category_id) "
                "ON CONFLICT (bling_category_id) DO UPDATE SET "
                "name = EXCLUDED.name, "
                "parent_bling_category_id = EXCLUDED.parent_bling_category_id, "
                "updated_at = now()"
            ),
            {
                "bling_category_id": bling_category_id,
                "name": name,
                "parent_bling_category_id": parent_bling_category_id,
            },
        )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(
        f'DROP TRIGGER IF EXISTS trg_product_categories_updated_at '
        f'ON "{SCHEMA}".product_categories'
    )
    op.drop_table("product_categories", schema=SCHEMA)
