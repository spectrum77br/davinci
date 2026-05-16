# ruff: noqa: E501
"""add FK + index pricing_accounts.store_info_id -> store_info.id

Production state (2026-05-16): the `store_info_id` column already exists on
`pricing_accounts` but was unconstrained — 214 rows, 0 populated. We
backfilled it via name+platform matching (mercadolivre→ml; classico/premium
suffix strip; whitespace-collapse) and then need the FK + index in code so
fresh environments reproduce the same shape.

The backfill itself is data-side and runs idempotently here (NULL-only
rows, normalized matcher). 8 rows are expected to stay NULL for accounts
that have no `store_info` counterpart yet (e.g. "victor mei" variants).

Revision ID: 0047_pricing_accounts_store_info_fk
Revises: 0046_marketplace_freight_reconciliation
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0047_pricing_accounts_store_info_fk"
down_revision: str | None = "0046_marketplace_freight_reconciliation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

# Both passes use REGEXP_REPLACE on pa.name to strip the classico/premium
# suffix; pass 2 also collapses internal whitespace so "jlas 2" matches
# "jlas2" in store_info.account_name.
_BACKFILL_PASS_1 = f"""
UPDATE "{SCHEMA}".pricing_accounts pa
SET store_info_id = si.id
FROM "{SCHEMA}".store_info si
WHERE pa.store_info_id IS NULL
  AND LOWER(BTRIM(si.platform)) = CASE LOWER(pa.platform::text)
        WHEN 'mercadolivre' THEN 'ml'
        ELSE LOWER(pa.platform::text)
      END
  AND LOWER(BTRIM(si.account_name)) = LOWER(BTRIM(
        REGEXP_REPLACE(pa.name, '\\s+(classico|cl[áa]ssico|premium|prem)\\s*$', '', 'i')
      ));
"""

_BACKFILL_PASS_2 = f"""
UPDATE "{SCHEMA}".pricing_accounts pa
SET store_info_id = si.id
FROM "{SCHEMA}".store_info si
WHERE pa.store_info_id IS NULL
  AND LOWER(BTRIM(si.platform)) = CASE LOWER(pa.platform::text)
        WHEN 'mercadolivre' THEN 'ml'
        ELSE LOWER(pa.platform::text)
      END
  AND REGEXP_REPLACE(LOWER(BTRIM(si.account_name)), '\\s+', '', 'g')
    = REGEXP_REPLACE(LOWER(BTRIM(
        REGEXP_REPLACE(pa.name, '\\s+(classico|cl[áa]ssico|premium|prem)\\s*$', '', 'i')
      )), '\\s+', '', 'g');
"""


def upgrade() -> None:
    op.execute(_BACKFILL_PASS_1)
    op.execute(_BACKFILL_PASS_2)
    # FK + index. The column itself already existed before this migration.
    op.create_foreign_key(
        "fk_pricing_accounts_store_info_id_store_info",
        "pricing_accounts",
        "store_info",
        ["store_info_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_pricing_accounts_store_info_id",
        "pricing_accounts",
        ["store_info_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pricing_accounts_store_info_id",
        table_name="pricing_accounts",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "fk_pricing_accounts_store_info_id_store_info",
        "pricing_accounts",
        schema=SCHEMA,
        type_="foreignkey",
    )
    # Intentionally do NOT clear the backfilled FKs — preserving the data on
    # rollback is safer than orphaning operator-confirmed links.
