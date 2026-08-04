# ruff: noqa: E501
"""equipe de marketing: users.marketing_teams + marketing_creatives.equipe.

Espelha as Equipes de Vendas: o usuário ganha uma lista de equipes de
marketing (nomes livres) e cada linha da aba Criativos ganha um campo
"equipe". Usuário não-admin com ≥1 equipe só enxerga as linhas da(s)
equipe(s) dele.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0210_equipe_marketing"
down_revision: str | None = "0209_marketing_creative_files"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column("users", sa.Column("marketing_teams", JSONB(), nullable=True), schema=SCHEMA)
    op.add_column("marketing_creatives", sa.Column("equipe", sa.String(64), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("marketing_creatives", "equipe", schema=SCHEMA)
    op.drop_column("users", "marketing_teams", schema=SCHEMA)
