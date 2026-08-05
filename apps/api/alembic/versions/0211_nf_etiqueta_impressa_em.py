# ruff: noqa: E501
"""nf_etiqueta_arquivo.impressa_em: carimbo de quando a etiqueta foi impressa.

O Controle de Estoque passa a imprimir etiquetas EM LOTE. Sem um carimbo de
impressão o operador não tem como saber o que já saiu da impressora e acaba
imprimindo o mesmo pedido duas vezes. A coluna guarda o instante da PRIMEIRA
impressão (não é sobrescrita nas reimpressões) — NULL = nunca impressa.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0211_nf_etiqueta_impressa_em"
down_revision: str | None = "0210_equipe_marketing"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "nf_etiqueta_arquivo",
        sa.Column("impressa_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("nf_etiqueta_arquivo", "impressa_em", schema=SCHEMA)
