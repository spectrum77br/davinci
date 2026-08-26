"""Faturador POR TIPO de produto na tela Lojas.

Eduardo (2026-08-26): "tem contas que tem dois tipos de produtos vendendo
nela, celular e eletro, celular e um faturador, eletro que vende na mesma
conta e outra" — a célula Faturador dessas contas abre um popover com um
select por tipo (os tipos da coluna Tipo/departments).

JSONB {slug do tipo: uuid do nf_faturador}, ex. {"celular": "…", "eletro":
"…"}. NULL/{} = regra única (nf_faturador_id segue valendo). Sem FK (JSONB);
faturador apagado vira uuid órfão inofensivo — a tela mostra "—".

Só cadastro — a regra de emissão é programada depois (mesmo estado do
Faturador produto/0226). A coluna "Faturador produto" NÃO muda.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0228_store_faturador_por_tipo"
down_revision: str | None = "0227_previsao_impressa"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "store_info",
        sa.Column("nf_faturador_por_tipo", postgresql.JSONB(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("store_info", "nf_faturador_por_tipo", schema=SCHEMA)
