# ruff: noqa: E501
"""marketing_creative_files: vários arquivos por criativo.

A aba Criativos passa a aceitar mais de um arquivo por linha (e apagar
arquivo individual). Os campos file_* de marketing_creatives migram pra
tabela filha marketing_creative_files (1:N, cascade no delete da linha).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0209_marketing_creative_files"
down_revision: str | None = "0208_marketing_creatives"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "marketing_creative_files",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "creative_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.marketing_creatives.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("file_name", sa.String(256), nullable=False),
        sa.Column("file_mime", sa.String(128), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_rel", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.marketing_creative_files
            (id, creative_id, file_name, file_mime, file_size, file_rel, created_at, updated_at)
        SELECT gen_random_uuid(), id, coalesce(file_name, 'arquivo'), file_mime,
               file_size, file_rel, now(), now()
        FROM {SCHEMA}.marketing_creatives
        WHERE file_rel IS NOT NULL
        """
    )
    op.drop_column("marketing_creatives", "file_name", schema=SCHEMA)
    op.drop_column("marketing_creatives", "file_mime", schema=SCHEMA)
    op.drop_column("marketing_creatives", "file_size", schema=SCHEMA)
    op.drop_column("marketing_creatives", "file_rel", schema=SCHEMA)


def downgrade() -> None:
    op.add_column("marketing_creatives", sa.Column("file_name", sa.String(256), nullable=True), schema=SCHEMA)
    op.add_column("marketing_creatives", sa.Column("file_mime", sa.String(128), nullable=True), schema=SCHEMA)
    op.add_column("marketing_creatives", sa.Column("file_size", sa.BigInteger(), nullable=True), schema=SCHEMA)
    op.add_column("marketing_creatives", sa.Column("file_rel", sa.String(512), nullable=True), schema=SCHEMA)
    op.drop_table("marketing_creative_files", schema=SCHEMA)
