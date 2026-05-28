"""normalize devolution tags

Revision ID: 0107_normalize_devolution_tags
Revises: 0106_import_categoria
Create Date: 2026-05-28

Normaliza `devolutions.tag` para uma tag unica. Kits como
`x.sp+y.sp` devem ficar apenas `.sp`, nao `.sp.sp`.
"""

from alembic import op

revision = "0107_normalize_devolution_tags"
down_revision = "0106_import_categoria"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        UPDATE devolutions d
        SET tag = sub.tag
        FROM (
            SELECT dd.id,
                   (
                       SELECT '.' || p.suffix
                       FROM (
                           SELECT
                               lower((regexp_match(btrim(u.part), '\.([A-Za-z]+)$'))[1]) AS suffix,
                               u.ord
                           FROM unnest(string_to_array(dd.sku, '+'))
                               WITH ORDINALITY AS u(part, ord)
                       ) p
                       WHERE p.suffix IN ('ci', 'pi', 'ra', 'sa', 'sp', 'us', 'cd')
                       ORDER BY p.ord
                       LIMIT 1
                   ) AS tag
            FROM devolutions dd
        ) sub
        WHERE d.id = sub.id
        """
    )


def downgrade() -> None:
    pass
