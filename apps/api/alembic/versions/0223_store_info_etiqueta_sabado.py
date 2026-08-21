"""Regra de sábado da etiqueta (só Mercado Livre) na tela Lojas.

No sábado o ML de **correios** segue contínuo (sem regra de estoque); o ML de
**agência** emite uma única vez, num horário fixo, e só para os estoques/tags
escolhidos. Os dois campos ficam ao lado de `etiqueta_horarios` (0222):

- `etiqueta_sabado_horario`: "HH:MM" em BRT (ex. "08:00"). NULL = não emite.
- `etiqueta_sabado_tags`: slugs de estoque separados por vírgula
  (ex. "pi, ra, sp"). NULL/vazio = nenhum estoque marcado.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0223_store_info_etiqueta_sabado"
down_revision: str | None = "0222_store_info_etiqueta_horarios"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "store_info",
        sa.Column("etiqueta_sabado_horario", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "store_info",
        sa.Column("etiqueta_sabado_tags", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("store_info", "etiqueta_sabado_tags", schema=SCHEMA)
    op.drop_column("store_info", "etiqueta_sabado_horario", schema=SCHEMA)
