"""refunds: coluna created_by (rastreio de quem cadastrou o reembolso)

Revision ID: 0132_refund_created_by
Revises: 0131_user_password

Adiciona rastreabilidade de autoria nas linhas de reembolso. O handler
POST /api/refunds passa a gravar o usuário autenticado em created_by. A
coluna é nullable (linhas antigas ficam sem autor) e FK pra users.id com
ondelete SET NULL (apagar um usuário não apaga os reembolsos dele).

Uso interno/DB apenas — NÃO é exposto no schema RefundOut nem na UI.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0132_refund_created_by"
down_revision = "0131_user_password"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "refunds",
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("refunds", "created_by", schema=SCHEMA)
