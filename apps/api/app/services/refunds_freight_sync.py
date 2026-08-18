"""Auto-popula refunds tipo='Logistica' a partir da view de margens marketplace.

Espelha o filtro "frete attention" da página margens (`_ATTENTION_FRETE_SQL`
em app/routers/margens.py): linhas onde o frete real cobrado pelo marketplace
é maior que o frete anúncio do item. Uma linha de refund por
(pedido_bling, conta), tipo='Logistica'.

Só cria quando o prejuizo agregado do pedido é de pelo menos `_MIN_PREJUIZO`
(R$5): diferenças menores não compensam o esforço de cobrar o reembolso.

Política: nunca atualiza, nunca sobrescreve. Se já existe um refund
Logistica para o (pedido_bling, conta) — manual ou auto-gerado — o
INSERT é skipado via WHERE NOT EXISTS. O usuário sempre tem o controle
final do refund.

Duas entradas:
  * upsert_freight_refund_for_bling_order — escopado a um pedido,
    chamado pelo hook após o sync financeiro (baixa latência). Lê a view
    de 20 dias (barata).
  * backfill_freight_refunds — chamado pelo cron diário pra pegar
    reconciliations de ML que fecham semanas depois. Lê a view `_all`
    (sem janela interna) com janela própria de 90 dias: com a view de
    20d, pedidos cuja cobrança de frete chegava atrasada já tinham saído
    do campo de visão e o chamado nunca nascia (ago/2026: 23 pedidos,
    R$528 sem chamado).

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

# View com janela de 20d embutida (a mesma da página de margens) — barata,
# usada pelo hook por pedido (chamado a cada sync financeiro).
_VIEW_20D = "vw_conciliacao_margens_marketplace"

# View idêntica SEM janela interna (diferem só nos dois filtros de data;
# ver migration 0080). Cara (~3 min: pricing de todo o histórico), então
# usada só pelo backfill diário — nunca no hook.
_VIEW_ALL = "vw_conciliacao_margens_marketplace_all"

# Janela própria do backfill sobre a _all. O ML fecha a conciliação de
# frete de alguns pedidos SEMANAS depois da venda (observado: ~35 dias);
# com a view de 20d o pedido saía do campo de visão antes da cobrança
# chegar e o chamado nunca nascia. 90 dias cobre o atraso com folga; o
# WHERE NOT EXISTS mantém a re-varredura segura (nunca duplica).
_BACKFILL_WINDOW_SQL = "AND v.data >= now() - interval '90 days'"


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

# Piso de prejuizo (R$) por pedido pra criar o refund. Abaixo disso a
# diferença de frete não compensa o esforço de cobrar reembolso, então
# nem é lançada. R$5 exato cria (>=). Aplicado via HAVING sobre o SUM
# agregado do pedido.
_MIN_PREJUIZO = 5.0

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
FROM {{schema}}.{{view}} v
WHERE {_FRETE_ATTENTION_FILTER}
  AND v.pedido_bling IS NOT NULL
  AND v.loja_nome IS NOT NULL
  AND btrim(v.loja_nome) <> ''
  {{extra_where}}
GROUP BY
    v.pedido_bling,
    COALESCE(v.plataforma_bling, v.plataforma_financeiro),
    btrim(v.loja_nome)
HAVING SUM({_FRETE_RESULTADO_SQL}) >= {_MIN_PREJUIZO}
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
    view a um pedido_bling pra que o custo seja constante. Usa a view de
    20 dias (barata); se a cobrança de frete chegou depois do pedido sair
    dessa janela, quem cria o chamado é o backfill diário (90d).
    """
    if not pedido_bling:
        return {"ok": False, "skipped": "missing_pedido_bling"}

    sql = _INSERT_TMPL.format(
        schema=SCHEMA,
        view=_VIEW_20D,
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
    """Varre os últimos 90 dias e insere refunds Logistica faltantes.

    Chamado pelo cron diário (03:20 BRT). Lê a view `_all` com janela
    própria de 90d pra enxergar cobranças de frete que o ML fecha semanas
    depois da venda — a view de 20d escondia esses pedidos. Custa ~3 min
    (a `_all` resolve pricing de todo o histórico), ok pra madrugada e
    dentro do job_timeout global de 1800s do worker.
    """
    sql = _INSERT_TMPL.format(
        schema=SCHEMA,
        view=_VIEW_ALL,
        extra_where=_BACKFILL_WINDOW_SQL,
    )
    result = await session.execute(text(sql))
    rowcount = result.rowcount or 0
    logger.info("refunds_freight_backfill_done", rows=rowcount)
    return {"ok": True, "rows": rowcount}
