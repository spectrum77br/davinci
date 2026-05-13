"""seed subtype children under each root segment (Phase 1.5)

Replaces hardcoded TYPE_HEADERS in apps/web/pages/pricing/[tab].vue. Sort_order
mirrors the column index (0..4) so existing `pricing_products.product_type`
(1..5) maps to a child via `sort_order = product_type - 1` under the matching
root.

Revision ID: 0024_segments_subtypes_seed
Revises: 0023_segments
Create Date: 2026-05-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_segments_subtypes_seed"
down_revision: str | None = "0023_segments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

CHILDREN: dict[str, list[tuple[str, str]]] = {
    # root_slug: [(child_name, child_slug), ...] in sort_order 0..4
    "celular": [
        ("Acessórios", "acessorios"),
        ("Diversos", "diversos"),
        ("Regular", "regular"),
        ("Robusto", "robusto"),
        ("Apple", "apple"),
    ],
    "mala": [
        ("Acessórios", "acessorios"),
        ('12"', "12"),
        ('18" e 20"', "18-20"),
        ('24" acima', "24-acima"),
        ("Queima de estoque", "queima-estoque"),
    ],
    "eletro": [
        ("1", "1"),
        ("2", "2"),
        ("3", "3"),
        ("4", "4"),
        ("5", "5"),
    ],
    "catalogo": [
        ("Acessórios", "acessorios"),
        ("Diversos", "diversos"),
        ("Regular", "regular"),
        ("Robusto", "robusto"),
        ("Apple", "apple"),
    ],
}


def upgrade() -> None:
    bind = op.get_bind()
    # Resolve root ids by slug (rows seeded in 0023_segments).
    for root_slug, children in CHILDREN.items():
        root_id = bind.execute(
            sa.text(
                f"SELECT id FROM {SCHEMA}.segments "
                "WHERE parent_id IS NULL AND slug = :slug"
            ),
            {"slug": root_slug},
        ).scalar_one_or_none()
        if root_id is None:
            continue
        for idx, (name, slug) in enumerate(children):
            # Idempotent: skip if (parent_id, slug) already exists.
            exists = bind.execute(
                sa.text(
                    f"SELECT 1 FROM {SCHEMA}.segments "
                    "WHERE parent_id = :pid AND slug = :slug"
                ),
                {"pid": root_id, "slug": slug},
            ).scalar_one_or_none()
            if exists:
                continue
            bind.execute(
                sa.text(
                    f"INSERT INTO {SCHEMA}.segments "
                    "(user_id, parent_id, name, slug, sort_order) "
                    "VALUES (NULL, :pid, :name, :slug, :ord)"
                ),
                {"pid": root_id, "name": name, "slug": slug, "ord": idx},
            )


def downgrade() -> None:
    bind = op.get_bind()
    for root_slug, children in CHILDREN.items():
        root_id = bind.execute(
            sa.text(
                f"SELECT id FROM {SCHEMA}.segments "
                "WHERE parent_id IS NULL AND slug = :slug"
            ),
            {"slug": root_slug},
        ).scalar_one_or_none()
        if root_id is None:
            continue
        slugs = [c[1] for c in children]
        bind.execute(
            sa.text(
                f"DELETE FROM {SCHEMA}.segments "
                "WHERE parent_id = :pid AND slug = ANY(:slugs)"
            ),
            {"pid": root_id, "slugs": slugs},
        )
