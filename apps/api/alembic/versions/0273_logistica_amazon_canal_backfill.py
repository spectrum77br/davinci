"""logistica.amazon_canal — preenche pelo que a SP-API já gravou

Vinicius, 15/09/2026: a aba Amazon mostrava pedidos antigos em "Sem
classificação" porque o canal só era persistido quando o enrich passava de
novo pela linha (e o robô do Bling lê 40 por rodada, mais recentes primeiro).
A regra é a mesma de services/logistica_amazon_canal.classificar: EasyShip
presente = DBA; AFN = FBA; MFN sem EasyShip = Envio próprio. Idempotente.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0273_logistica_amazon_canal_backfill"
down_revision: str | None = "0272_logistica_amazon_prazos_mensagens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE "{SCHEMA}".logistica
               SET amazon_canal = CASE
                     WHEN coalesce(meli_status->>'easyship_status', '') <> '' THEN 'dba'
                     WHEN upper(coalesce(meli_status->>'fulfillment_channel', '')) = 'AFN'
                          THEN 'fba'
                     WHEN upper(coalesce(meli_status->>'fulfillment_channel', '')) = 'MFN'
                          THEN 'proprio'
                   END
             WHERE lower(trim(coalesce(plataforma, ''))) = 'amazon'
               AND amazon_canal IS NULL
               AND (
                     coalesce(meli_status->>'easyship_status', '') <> ''
                  OR upper(coalesce(meli_status->>'fulfillment_channel', '')) IN ('AFN', 'MFN')
               )
            """  # noqa: S608
        )
    )


def downgrade() -> None:
    # Dado derivado: o enrich recompõe; nada a desfazer.
    pass
