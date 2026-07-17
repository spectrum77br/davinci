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

# Chave (store_info.platform) -> rótulo gravado em `logistica.plataforma`
# (o filtro da aba por marketplace usa esse rótulo; ver routers/logistica).
_PLATAFORMA_LABELS = {
    "ml": "Mercado Livre",
    "shopee": "Shopee",
    "amazon": "Amazon",
    "tiktok": "TikTok",
}

# Mesmo mapeamento do backfill: plataforma/conta via store_info (o `loja` do
# pedido é o bling_store_id), status_bling via situacao_bling. meli_status fica
# vazio (só o ML enriquece a assinatura; os outros marketplaces ficam sem
# Status Plataforma por enquanto — a aba mostra a linha com o status do Bling).
_INSERT_SQL = text(
    """
    INSERT INTO davinci.logistica
        (id, data, pedido_bling, pedido_marketplace, plataforma, conta,
         meli_status, status_bling, created_at, updated_at)
    SELECT DISTINCT ON (bo.numero)
        gen_random_uuid(),
        bo.data::date,
        bo.numero,
        bo.numeroloja,
        :label,
        si.account_name,
        '{}'::jsonb,
        sb.nome,
        now(),
        now()
    FROM davinci.bling_orders bo
    JOIN davinci.store_info si
        ON si.bling_store_id::text = bo.loja AND si.platform = :platform
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


async def _ingest_platform(session: AsyncSession, platform: str, dias: int) -> int:
    """Insere os pedidos novos (janela de `dias`) de UMA plataforma. Idempotente
    (NOT EXISTS por pedido_bling). Retorna quantas linhas entraram."""
    label = _PLATAFORMA_LABELS[platform]
    res = await session.execute(
        _INSERT_SQL, {"platform": platform, "label": label, "dias": dias}
    )
    inserted = res.rowcount or 0
    await session.commit()
    logger.info(
        "logistica_ingest_inserted", platform=platform, inserted=inserted, dias=dias
    )
    return inserted


async def run_ingest_ml_daily(
    session: AsyncSession, *, dias: int = 3, enrich_limit: int = 400
) -> dict[str, int]:
    """Insere os pedidos ML novos (janela de `dias`) e enriquece o status do
    Meli das linhas ML ainda vazias (mais recentes primeiro)."""
    inserted = await _ingest_platform(session, "ml", dias)
    enr = await logistica_meli.enrich_recent(
        session, limit=enrich_limit, only_empty=True
    )
    return {"inserted": inserted, **{f"enrich_{k}": v for k, v in enr.items()}}


async def run_ingest_marketplaces_daily(
    session: AsyncSession, *, dias: int = 3
) -> dict[str, int]:
    """Insere os pedidos novos de Shopee/TikTok/Amazon pra aba Logística. Só
    ingestão — esses marketplaces ainda não têm enriquecimento de Status
    Plataforma (as linhas aparecem com o status do Bling e casam a aba Status
    quando a regra tiver a chave). Retorna o total inserido por plataforma."""
    out: dict[str, int] = {}
    for platform in ("shopee", "tiktok", "amazon"):
        out[platform] = await _ingest_platform(session, platform, dias)
    return out


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
