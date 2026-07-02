# ruff: noqa: E501, S608
"""bling_orders.data: normaliza datas gravadas em meia-noite UTC p/ meia-noite SP

O parser `_to_dt` (services/bling_orders.py) carimbava a data date-only do
Bling com tzinfo=UTC: '2026-07-01' virava 2026-07-01 00:00+00, que convertido
p/ São Paulo é 30/06 21h — o pedido caía UM DIA ANTES em todo relatório que
agrupa por dia/mês SP (Valuation, margem, envios). Na virada de mês, mudava o
mês: junho/2026 estava inflado em ~R$ 161k de pedidos de 01/07 (achado da
conciliação com o export de vendas do Bling, 2026-07-02).

Outro caminho de ingest (rotina diária das 5h) grava correto (meia-noite SP =
03:00 UTC), por isso o banco tinha os dois formatos misturados: ~63k pedidos
em 00:00 UTC (carga histórica + webhook ingest) vs ~17k em 03:00 UTC.

Correção: +3h em toda linha cujo horário UTC é exatamente 00:00:00 —
equivale a reinterpretar a mesma DATA de calendário como meia-noite SP.
`data` do Bling é date-only nesses caminhos (só existem 00:00 e 03:00 no
banco), então não há falso positivo de pedido real às 21h SP.

Triggers de bling_orders: os de captura/ship-date disparam só em UPDATE OF
situacao/em_andamento_data (não tocados); protect_em_andamento_data é no-op
quando em_andamento_data não muda. Só updated_at é re-carimbado (aceitável —
é o rastro do backfill). O código novo (`_to_dt` com _BRT) já grava certo
daqui pra frente.

Revision ID: 0166_bling_orders_data_tz_fix
Revises: 0165_background_job_details
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0166_bling_orders_data_tz_fix"
down_revision: str | None = "0165_background_job_details"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

# (data AT TIME ZONE 'UTC')::time é independente do TimeZone da sessão.
_BACKFILL = f"""
    UPDATE "{SCHEMA}"."bling_orders"
    SET data = data + INTERVAL '3 hours'
    WHERE data IS NOT NULL
      AND (data AT TIME ZONE 'UTC')::time = '00:00:00'
"""

_REVERT = f"""
    UPDATE "{SCHEMA}"."bling_orders"
    SET data = data - INTERVAL '3 hours'
    WHERE data IS NOT NULL
      AND (data AT TIME ZONE 'UTC')::time = '03:00:00'
"""


def upgrade() -> None:
    bind = op.get_bind()
    result = bind.execute(sa.text(_BACKFILL))
    print(f"0166: {result.rowcount} linhas normalizadas p/ meia-noite SP")


def downgrade() -> None:
    # Imperfeito por natureza: devolve TODAS as 03:00 p/ 00:00, inclusive as
    # que já nasceram certas. Existe só como escotilha formal.
    op.get_bind().execute(sa.text(_REVERT))
