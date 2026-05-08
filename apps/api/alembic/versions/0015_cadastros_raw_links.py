"""cadastros.raw_links jsonb

Revision ID: 0015_cadastros_raw_links
Revises: 0014_cadastro_tipo_servidor
Create Date: 2026-05-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_cadastros_raw_links"
down_revision: str | None = "0014_cadastro_tipo_servidor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "cadastros",
        sa.Column(
            "raw_links",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=SCHEMA,
    )

    # Backfill: parse "raw_links: { ... }" segment from obs (populate_xlsx
    # wrote it there before this column existed), then strip it from obs.
    op.execute(
        f"""
        UPDATE "{SCHEMA}".cadastros
           SET raw_links = (
                 substring(obs from 'raw_links: (\\{{.*\\}})')
               )::jsonb
         WHERE obs ~ 'raw_links: \\{{'
        """
    )
    op.execute(
        f"""
        UPDATE "{SCHEMA}".cadastros
           SET obs = NULLIF(
                 trim(both ' |' from
                   regexp_replace(obs, '\\s*\\|?\\s*raw_links: \\{{[^}}]*\\}}', '', 'g')
                 ),
                 ''
               )
         WHERE obs ~ 'raw_links: \\{{'
        """
    )


def downgrade() -> None:
    op.drop_column("cadastros", "raw_links", schema=SCHEMA)
