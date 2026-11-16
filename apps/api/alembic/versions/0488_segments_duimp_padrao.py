"""Segmentos: DUIMP padrão (mensagem da nota quando o produto não tem DUIMP)

Eduardo, 16/11/2026: "quero criar uma coluna, para quando um produto não tiver
duimp, ir a mensagem ou duimp que colocarmos, deixar por segmento porque é mais
fácil — no cadastro de importação tem todos os produtos novos, mas os produtos
que voltam usados, avulsos etc. não têm".

`segments.duimp_padrao` (texto livre, até 200 caracteres, sem "|"). A nota e a
etiqueta usam a DUIMP da Importação primeiro (SKU exato, depois o código-base);
só o SKU que continuar sem DUIMP pega o texto do segmento dele (o do subsegmento;
vazio → o da raiz). Mesmo portão de hoje: só faturador com `observacao_duimp`
(nota cheia) — decisão do dono no mesmo dia.

Revision ID: 0488_segments_duimp_padrao
Revises: 0487_cairo_devolucoes_resolver
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0488_segments_duimp_padrao"
down_revision: str | None = "0487_cairo_devolucoes_resolver"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "segments",
        sa.Column("duimp_padrao", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("segments", "duimp_padrao", schema=SCHEMA)
