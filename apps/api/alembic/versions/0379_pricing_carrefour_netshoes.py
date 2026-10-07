"""Carrefour/Netshoes pricing accounts for the types already selected in Lojas.

Adds only blank pricing accounts: commission, margins, shipping, integrations
and overrides are never copied or guessed. Store links keep archived stores
hidden and preserve owner/team scoping. Registration without a selected type
still creates no account. Converted manual tags are cleared so later account
deletions cannot resurrect them; unknown/manual-only tags remain unchanged.

The async Alembic env holds an external transaction, so autocommit_block is
not usable. PostgreSQL requires enum additions to be committed before their
values can be inserted. Commit that additive step explicitly and begin a new
transaction for the backfill and revision marker. If backfill fails, rerunning
is safe (IF NOT EXISTS enum values, plus account existence checks).

Revision ID: 0379_pricing_carrefour_netshoes
Revises: 0378_catalogo_ml
"""

# SQL identifiers below are the fixed application schema, never user input.
# ruff: noqa: S608

from collections.abc import Sequence

from alembic import op

revision: str = "0379_pricing_carrefour_netshoes"
down_revision: str | None = "0378_catalogo_ml"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    for platform in ("carrefour", "netshoes"):
        op.execute(
            f'ALTER TYPE "{SCHEMA}".pricing_platform ADD VALUE IF NOT EXISTS \'{platform}\''
        )
    op.execute("COMMIT")
    op.execute("BEGIN")
    op.execute("SET LOCAL lock_timeout = '10s'")
    # Match the API's store-then-account lock order. Only this short backfill
    # holds the lock; SELECTs continue working while writers wait briefly.
    op.execute(
        f'LOCK TABLE "{SCHEMA}".store_info, "{SCHEMA}".pricing_accounts '
        'IN SHARE ROW EXCLUSIVE MODE'
    )
    op.execute(f"""
        INSERT INTO "{SCHEMA}".pricing_accounts (
            id, user_id, name, platform, segment_id, store_info_id, canal,
            kit_number, sort_order
        )
        SELECT gen_random_uuid(), s.user_id,
               COALESCE(NULLIF(btrim(s.account_name), ''),
                        lower(btrim(s.platform)) || ' — ' || segment.slug),
               lower(btrim(s.platform))::"{SCHEMA}".pricing_platform,
               segment.id, s.id, 'kit', 1,
               COALESCE((
                   SELECT max(existing.sort_order)
                   FROM "{SCHEMA}".pricing_accounts existing
                   WHERE existing.user_id = s.user_id
                     AND existing.platform::text = lower(btrim(s.platform))
                     AND existing.segment_id = segment.id
               ), 0) + row_number() OVER (
                   PARTITION BY s.user_id, lower(btrim(s.platform)), segment.id
                   ORDER BY s.id
               )
        FROM "{SCHEMA}".store_info s
        JOIN "{SCHEMA}".segments segment
          ON segment.parent_id IS NULL
         AND segment.slug = ANY(s.manual_departments)
         AND segment.slug <> 'catalogo'
        WHERE lower(btrim(s.platform)) IN ('carrefour', 'netshoes')
          AND NOT EXISTS (
              SELECT 1 FROM "{SCHEMA}".pricing_accounts linked
              WHERE linked.store_info_id = s.id
                AND linked.segment_id = segment.id
                AND linked.canal = 'kit'
          )
    """)
    op.execute(f"""
        UPDATE "{SCHEMA}".store_info s
        SET manual_departments = NULLIF(ARRAY(
            SELECT tag.slug
            FROM unnest(s.manual_departments) AS tag(slug)
            WHERE NOT EXISTS (
                SELECT 1
                FROM "{SCHEMA}".pricing_accounts linked
                JOIN "{SCHEMA}".segments segment ON segment.id = linked.segment_id
                WHERE linked.store_info_id = s.id
                  AND linked.canal = 'kit'
                  AND segment.parent_id IS NULL
                  AND segment.slug = tag.slug
                  AND segment.slug <> 'catalogo'
            )
        ), ARRAY[]::text[])
        WHERE lower(btrim(s.platform)) IN ('carrefour', 'netshoes')
          AND s.manual_departments IS NOT NULL
    """)


def downgrade() -> None:
    # Neither delete accounts that users may have configured nor drop enum
    # labels that those accounts now depend on. This additive data change is
    # deliberately non-destructive on rollback.
    pass
