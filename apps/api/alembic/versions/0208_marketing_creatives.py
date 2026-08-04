# ruff: noqa: E501
"""marketing_creatives: aba Criativos do Marketing.

Planilha de briefing de imagens/vídeos (modelo/marca/sku/roteiro), com
arquivo anexado pelo criador de conteúdo e aprovação (V/X) pelo admin.
Ao aprovar, o arquivo sobe pra pasta do produto no MEGA (match por SKU
na tabela de preços) — pushed_at/pushed_dest registram esse envio.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0208_marketing_creatives"
down_revision: str | None = "0207_pricing_product_kits5_8"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "marketing_creatives",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("modelo", sa.String(160), nullable=False),
        sa.Column("marca", sa.String(64), nullable=True),
        sa.Column("sku", sa.String(512), nullable=True),
        sa.Column("roteiro", sa.Text(), nullable=True),
        sa.Column("file_name", sa.String(256), nullable=True),
        sa.Column("file_mime", sa.String(128), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_rel", sa.String(512), nullable=True),
        sa.Column("aprovado", sa.Boolean(), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pushed_dest", sa.String(512), nullable=True),
        sa.Column(
            "created_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("marketing_creatives", schema=SCHEMA)
