"""Importação diária dos novos pedidos Mercado Livre pra aba Logística.

A tabela `logistica` foi populada por backfill manual; depois o usuário optou
por manter só "de hoje pra frente". Esta rotina roda todo dia, insere os pedidos
ML novos vindos do Bling (`bling_orders`) e enriquece a assinatura de status do
Meli — assim a lista cresce sozinha sem backfill manual.

O INSERT é idempotente (`NOT EXISTS` por `pedido_bling`), então re-rodar não
duplica; a janela de dias dá folga pra pegar pedidos que o sync do Bling só
trouxe com atraso.
"""
from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import logistica_bling, logistica_meli

logger = structlog.get_logger()

# Mesmo mapeamento do backfill: plataforma/conta via store_info (o `loja` do
# pedido é o bling_store_id), status_bling via situacao_bling. meli_status fica
# vazio até o enriquecimento puxar a assinatura do ML.
_INSERT_ML_SQL = text(
    """
    INSERT INTO davinci.logistica
        (id, data, pedido_bling, pedido_marketplace, plataforma, conta,
         meli_status, status_bling, created_at, updated_at)
    SELECT DISTINCT ON (bo.numero)
        gen_random_uuid(),
        bo.data::date,
        bo.numero,
        bo.numeroloja,
        'Mercado Livre',
        si.account_name,
        '{}'::jsonb,
        sb.nome,
        now(),
        now()
    FROM davinci.bling_orders bo
    JOIN davinci.store_info si
        ON si.bling_store_id::text = bo.loja AND si.platform = 'ml'
    LEFT JOIN davinci.situacao_bling sb ON sb.id::text = bo.situacao
    WHERE bo.data >= now() - make_interval(days => :dias)
      AND bo.numero IS NOT NULL
      AND bo.situacao IS DISTINCT FROM 'excluido'
      AND NOT EXISTS (
          SELECT 1 FROM davinci.logistica l WHERE l.pedido_bling = bo.numero
      )
    ORDER BY bo.numero, bo.data DESC
    """
)


async def run_ingest_ml_daily(
    session: AsyncSession, *, dias: int = 3, enrich_limit: int = 400
) -> dict[str, int]:
    """Insere os pedidos ML novos (janela de `dias`) e enriquece o status do
    Meli das linhas ML ainda vazias (mais recentes primeiro)."""
    res = await session.execute(_INSERT_ML_SQL, {"dias": dias})
    inserted = res.rowcount or 0
    await session.commit()
    logger.info("logistica_ingest_ml_inserted", inserted=inserted, dias=dias)

    enr = await logistica_meli.enrich_recent(
        session, limit=enrich_limit, only_empty=True
    )
    return {"inserted": inserted, **{f"enrich_{k}": v for k, v in enr.items()}}


async def recarregar_ml(
    session: AsyncSession, *, enrich_limit: int = 300
) -> dict[str, int]:
    """Recarga sob demanda do botão "recarregar" da aba Mercado Livre.

    Diferente do cron diário, RE-enriquece TODAS as linhas ML (não só as vazias)
    pra atualizar a assinatura de status do Meli, e então aplica em lote a
    mudança de situação no Bling das linhas que casam uma regra da aba Status.
    Roda em background (arq) porque o passo do ML+Bling pode passar dos 100s do
    Cloudflare.
    """
    enr = await logistica_meli.enrich_recent(
        session, limit=enrich_limit, only_empty=False
    )
    lote = await logistica_bling.aplicar_status_em_lote(session)
    logger.info("logistica_recarregar_ml", **{f"enrich_{k}": v for k, v in enr.items()}, **lote)
    return {**{f"enrich_{k}": v for k, v in enr.items()}, **{f"status_{k}": v for k, v in lote.items()}}
