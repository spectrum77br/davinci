# ruff: noqa: E501
"""bind shipping/margin slots to segments via slot{1..5}_segment_id

Production state (2026-05-16): `pricing_accounts` has flat
shipping{N}/margin{N} columns (N=1..5) with no semantic link to the
segments table. Each account already carries the department via
`segment_id` (root segment), but consumers had to assume slot order via
sort_order of children — a convention enforced only by the UI.

This migration makes the binding explicit by adding 5 nullable UUID FKs
(`slot{N}_segment_id`) and backfilling them from the ordered children of
each account's department, using the exact ordering the UI's TYPE_HEADERS
uses (sort_order ASC, then name).

Calc code is intentionally NOT changed: pricing/calc.py keeps reading
shipping1..5/margin1..5 by slot index. The new columns are pure metadata
so analysts can JOIN them to segments and see "shipping1 is Acessórios"
directly in the row.

Revision ID: 0048_pricing_accounts_slot_segments
Revises: 0047_pricing_accounts_store_info_fk
Create Date: 2026-05-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0048_pricing_accounts_slot_segments"
down_revision: str | None = "0047_pricing_accounts_store_info_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


_BACKFILL_SQL = f"""
WITH ranked AS (
    SELECT
        child.id AS segment_id,
        child.parent_id AS department_id,
        ROW_NUMBER() OVER (
            PARTITION BY child.parent_id
            ORDER BY child.sort_order, child.name
        ) AS slot_num
    FROM "{SCHEMA}".segments child
    JOIN "{SCHEMA}".segments parent ON parent.id = child.parent_id
    WHERE parent.parent_id IS NULL AND child.active
),
slots AS (
    SELECT
        r1.department_id,
        r1.segment_id AS s1,
        r2.segment_id AS s2,
        r3.segment_id AS s3,
        r4.segment_id AS s4,
        r5.segment_id AS s5
    FROM (SELECT * FROM ranked WHERE slot_num = 1) r1
    LEFT JOIN (SELECT * FROM ranked WHERE slot_num = 2) r2 USING (department_id)
    LEFT JOIN (SELECT * FROM ranked WHERE slot_num = 3) r3 USING (department_id)
    LEFT JOIN (SELECT * FROM ranked WHERE slot_num = 4) r4 USING (department_id)
    LEFT JOIN (SELECT * FROM ranked WHERE slot_num = 5) r5 USING (department_id)
)
UPDATE "{SCHEMA}".pricing_accounts pa
SET slot1_segment_id = slots.s1,
    slot2_segment_id = slots.s2,
    slot3_segment_id = slots.s3,
    slot4_segment_id = slots.s4,
    slot5_segment_id = slots.s5
FROM slots
WHERE slots.department_id = pa.segment_id
  AND (
      pa.slot1_segment_id IS NULL OR pa.slot2_segment_id IS NULL OR
      pa.slot3_segment_id IS NULL OR pa.slot4_segment_id IS NULL OR
      pa.slot5_segment_id IS NULL
  );
"""


def upgrade() -> None:
    for n in (1, 2, 3, 4, 5):
        op.add_column(
            "pricing_accounts",
            sa.Column(f"slot{n}_segment_id", PG_UUID(as_uuid=True), nullable=True),
            schema=SCHEMA,
        )
    op.execute(_BACKFILL_SQL)
    for n in (1, 2, 3, 4, 5):
        op.create_foreign_key(
            f"fk_pricing_accounts_slot{n}_segment",
            "pricing_accounts",
            "segments",
            [f"slot{n}_segment_id"],
            ["id"],
            source_schema=SCHEMA,
            referent_schema=SCHEMA,
            ondelete="SET NULL",
        )
        op.create_index(
            f"ix_pricing_accounts_slot{n}_segment",
            "pricing_accounts",
            [f"slot{n}_segment_id"],
            schema=SCHEMA,
        )


def downgrade() -> None:
    for n in (1, 2, 3, 4, 5):
        op.drop_index(
            f"ix_pricing_accounts_slot{n}_segment",
            table_name="pricing_accounts",
            schema=SCHEMA,
        )
        op.drop_constraint(
            f"fk_pricing_accounts_slot{n}_segment",
            "pricing_accounts",
            schema=SCHEMA,
            type_="foreignkey",
        )
        op.drop_column(
            "pricing_accounts",
            f"slot{n}_segment_id",
            schema=SCHEMA,
        )
