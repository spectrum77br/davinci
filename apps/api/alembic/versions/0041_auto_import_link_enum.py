"""add 'auto_import_link' to background_job_type enum

Revision ID: 0041_auto_import_link_enum
Revises: 0040_cell_status_error_no_link
Create Date: 2026-05-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0041_auto_import_link_enum"
down_revision: Union[str, None] = "0040_cell_status_error_no_link"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"ALTER TYPE \"{SCHEMA}\".background_job_type "
        f"ADD VALUE IF NOT EXISTS 'auto_import_link'"
    )


def downgrade() -> None:
    pass
