"""Auto-popula refunds tipo='Logistica' a partir da view de margens marketplace.

Espelha o filtro "frete attention" da página margens (`_ATTENTION_FRETE_SQL`
em app/routers/margens.py): linhas onde o frete real cobrado pelo marketplace
é maior que o frete anúncio do item. Uma linha de refund por
(pedido_bling, conta), tipo='Logistica'.

Política: nunca atualiza, nunca sobrescreve. Se já existe um refund
Logistica para o (pedido_bling, conta) — manual ou auto-gerado — o
INSERT é skipado via WHERE NOT EXISTS. O usuário sempre tem o controle
final do refund.

Duas entradas:
  * upsert_freight_refund_for_bling_order — escopado a um pedido,
    chamado pelo hook após o sync financeiro (baixa latência).
  * backfill_freight_refunds — varre a view inteira, chamado pelo cron
    diário pra pegar reconciliations de ML que fecham dias depois.

O fragmento SQL de "frete plataforma" é duplicado de
app/routers/margens.py em vez de importado, pra evitar dependência
service→router. Atualizar aqui se o filtro de margens mudar.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

logger = structlog.get_logger()

_settings = get_settings()
SCHEMA = _settings.database_schema


# Espelha _FRETE_PLATAFORMA_SQL em app/routers/margens.py.
_FRETE_PLATAFORMA_SQL = (
    "CASE "
    "WHEN COALESCE(v.plataforma_bling, v.plataforma_financeiro) = 'shopee' "
    "THEN CASE WHEN v.evento_freight IS NULL THEN NULL "
    "          ELSE GREATEST(v.evento_freight * v.item_proportion, 0::numeric) END "
    "ELSE v.marketplace_frete_real_cobrado_item "
    "END"
)

# Espelha _FRETE_ANUNCIO_SQL em app/routers/margens.py.
# Intencionalmente sem item_proportion: o frete anúncio cheio fica em cada
# produto do pedido.
_FRETE_ANUNCIO_SQL = "v.evento_frete_anuncio"

# Espelha _FRETE_RESULTADO_SQL em app/routers/margens.py.
_FRETE_RESULTADO_SQL = f"(({_FRETE_PLATAFORMA_SQL}) - ({_FRETE_ANUNCIO_SQL}))"

# Filtro espelha _ATTENTION_FRETE_SQL: vendedor pagou mais frete que o
# frete anúncio. Ambas as expressões precisam ser não-nulas (sem dados
# financeiros sincronizados ainda → skipa).
_FRETE_ATTENTION_FILTER = (
    f"({_FRETE_ANUNCIO_SQL}) IS NOT NULL "
    f"AND ({_FRETE_PLATAFORMA_SQL}) IS NOT NULL "
    f"AND {_FRETE_RESULTADO_SQL} > 0"
)

# Agrega por (pedido_bling, conta). Prejuizo = SUM(frete_plataforma -
# frete_anuncio) sobre os itens onde o gatilho disparou — número positivo
# representando a perda.
_AGG_SELECT_SQL = f"""
SELECT
    MAX(v.data) AS data,
    v.pedido_bling::text AS pedido_bling,
    MAX(v.pedido_marketplace)::text AS pedido_marketplace,
    COALESCE(v.plataforma_bling, v.plataforma_financeiro)::text AS plataforma,
    btrim(v.loja_nome) AS conta,
    SUM({_FRETE_RESULTADO_SQL}) AS prejuizo
FROM {{schema}}.vw_conciliacao_margens_marketplace v
WHERE {_FRETE_ATTENTION_FILTER}
  AND v.pedido_bling IS NOT NULL
  AND v.loja_nome IS NOT NULL
  AND btrim(v.loja_nome) <> ''
  {{extra_where}}
GROUP BY
    v.pedido_bling,
    COALESCE(v.plataforma_bling, v.plataforma_financeiro),
    btrim(v.loja_nome)
"""


# INSERT WHERE NOT EXISTS — só insere se ainda não houver refund
# Logistica para o (pedido_bling, conta). Manual e auto coexistem pela
# ausência de overwrite.
_INSERT_TMPL = f"""
INSERT INTO {{schema}}.refunds (
    data, pedido_bling, pedido_marketplace, plataforma, conta,
    tipo, prejuizo, reembolso, conferido
)
SELECT
    s.data, s.pedido_bling, s.pedido_marketplace, s.plataforma,
    s.conta, 'Logistica', s.prejuizo, NULL::double precision, false
FROM ({_AGG_SELECT_SQL}) s
WHERE NOT EXISTS (
    SELECT 1 FROM {{schema}}.refunds r
    WHERE r.pedido_bling = s.pedido_bling
      AND r.conta = s.conta
      AND r.tipo = 'Logistica'
)
"""


async def upsert_freight_refund_for_bling_order(
    session: AsyncSession,
    *,
    pedido_bling: str,
) -> dict[str, Any]:
    """Insere o refund Logistica de um pedido se ainda não existir.

    Chamado pelo hook em marketplace_financials. Escopa a varredura da
    view a um pedido_bling pra que o custo seja constante independente
    do tamanho da janela de 20 dias.
    """
    if not pedido_bling:
        return {"ok": False, "skipped": "missing_pedido_bling"}

    sql = _INSERT_TMPL.format(
        schema=SCHEMA,
        extra_where="AND v.pedido_bling::text = :pedido_bling",
    )
    result = await session.execute(text(sql), {"pedido_bling": str(pedido_bling)})
    rowcount = result.rowcount or 0
    if rowcount:
        logger.info(
            "refunds_freight_inserted",
            pedido_bling=pedido_bling,
            rows=rowcount,
        )
    return {"ok": True, "rows": rowcount, "pedido_bling": pedido_bling}


async def backfill_freight_refunds(session: AsyncSession) -> dict[str, Any]:
    """Varre a view inteira e insere refunds Logistica faltantes.

    Chamado pelo cron diário. A janela de 20d da view limita o escopo,
    então é O(pedidos_nos_ultimos_20d).
    """
    sql = _INSERT_TMPL.format(schema=SCHEMA, extra_where="")
    result = await session.execute(text(sql))
    rowcount = result.rowcount or 0
    logger.info("refunds_freight_backfill_done", rows=rowcount)
    return {"ok": True, "rows": rowcount}
