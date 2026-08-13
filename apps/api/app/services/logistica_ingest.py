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

from uuid import UUID

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica, LogisticaStatus
from app.services import (
    logistica_amazon,
    logistica_bling,
    logistica_match,
    logistica_meli,
    logistica_rules,
    logistica_shopee,
    logistica_tiktok,
)

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
    session: AsyncSession, *, dias: int = 3, enrich_limit: int = 400
) -> dict[str, int]:
    """Insere os pedidos novos de Shopee/TikTok/Amazon pra aba Logística e
    enriquece o Status Plataforma das três (Shopee via order_status; TikTok via
    order status + rastreio; Amazon via OrderStatus + EasyShip) nas linhas ainda
    vazias. Retorna o total inserido por plataforma + o resumo de cada enrich."""
    out: dict[str, int] = {}
    for platform in ("shopee", "tiktok", "amazon"):
        out[platform] = await _ingest_platform(session, platform, dias)
    enr_shopee = await logistica_shopee.enrich_recent(
        session, limit=enrich_limit, only_empty=True
    )
    enr_tiktok = await logistica_tiktok.enrich_recent(
        session, limit=enrich_limit, only_empty=True
    )
    enr_amazon = await logistica_amazon.enrich_recent(
        session, limit=enrich_limit, only_empty=True
    )
    return {
        **out,
        **{f"shopee_enrich_{k}": v for k, v in enr_shopee.items()},
        **{f"tiktok_enrich_{k}": v for k, v in enr_tiktok.items()},
        **{f"amazon_enrich_{k}": v for k, v in enr_amazon.items()},
    }


async def _ids_pendentes(session: AsyncSession) -> dict[str, list[UUID]]:
    """Ids das linhas PENDENTES por plataforma — a mesma regra que o painel usa
    pra decidir o que MOSTRAR (esconde `resolvido and not monitorar`). É o
    conjunto que o operador está vendo e quer ver atualizado."""
    regras = list((await session.execute(select(LogisticaStatus))).scalars().all())
    linhas = list((await session.execute(select(Logistica))).scalars().all())
    por_chave: dict[str, set[str]] = {
        "ml": logistica_rules._ML_PLATAFORMAS,
        "shopee": logistica_rules._SHOPEE_PLATAFORMAS,
        "tiktok": logistica_rules._TIKTOK_PLATAFORMAS,
        "amazon": logistica_rules._AMAZON_PLATAFORMAS,
    }
    pend: dict[str, list[UUID]] = {k: [] for k in por_chave}
    for r in linhas:
        plat = (r.plataforma or "").strip().lower()
        chave = next((k for k, nomes in por_chave.items() if plat in nomes), None)
        if chave is None:
            continue
        assinatura = logistica_rules.assinatura_para(r.plataforma, r.meli_status or {})
        cands = logistica_match.find_matching_rules(
            regras, assinatura=assinatura, plataforma=r.plataforma
        )
        resolvido = logistica_match.estado_resolvido(
            cands, r.status_bling, threema_enviado=r.threema_enviado_at is not None
        )
        if resolvido and not logistica_match.deve_monitorar(cands, r.status_bling):
            continue
        pend[chave].append(r.id)
    return pend


async def recarregar_ml(
    session: AsyncSession, *, enrich_limit: int = 300
) -> dict[str, int]:
    """Recarga sob demanda do botão "recarregar" das abas de marketplace.

    Diferente do cron diário, RE-enriquece linhas que já têm assinatura — mas SÓ
    as PENDENTES do painel, e então aplica no Bling a mudança de situação das
    que casam uma regra da aba Status. Já foi "300 mais recentes de cada
    marketplace + Bling em TODAS as linhas": ~25 min só de enrich e o passo do
    Bling morrendo no job_timeout (1800s) ANTES do commit — o botão nunca
    terminava (12-13/ago, TimeoutError em aplicar_status_em_lote). Com ~2.7k
    linhas e só ~50 pendentes, mirar o painel termina em poucos minutos e cobre
    exatamente o que o operador está vendo. Linha resolvida que mudar depois no
    marketplace não volta sozinha — o ⟳ por linha continua sendo o caminho pra
    re-checar uma antiga. Roda em background (arq) porque mesmo assim pode
    passar dos 100s do Cloudflare.
    """
    pend = await _ids_pendentes(session)
    logger.info(
        "logistica_recarregar_pendentes",
        **{k: len(v) for k, v in pend.items()},
    )
    enr = await logistica_meli.enrich_recent(
        session, limit=enrich_limit, only_empty=False, ids=pend["ml"]
    )
    enr_shopee = await logistica_shopee.enrich_recent(
        session, limit=enrich_limit, only_empty=False, ids=pend["shopee"]
    )
    enr_tiktok = await logistica_tiktok.enrich_recent(
        session, limit=enrich_limit, only_empty=False, ids=pend["tiktok"]
    )
    enr_amazon = await logistica_amazon.enrich_recent(
        session, limit=enrich_limit, only_empty=False, ids=pend["amazon"]
    )
    lote = await logistica_bling.aplicar_status_em_lote(
        session, ids=[i for ids in pend.values() for i in ids]
    )
    logger.info(
        "logistica_recarregar_ml",
        **{f"enrich_{k}": v for k, v in enr.items()},
        **{f"shopee_enrich_{k}": v for k, v in enr_shopee.items()},
        **{f"tiktok_enrich_{k}": v for k, v in enr_tiktok.items()},
        **{f"amazon_enrich_{k}": v for k, v in enr_amazon.items()},
        **lote,
    )
    return {
        **{f"enrich_{k}": v for k, v in enr.items()},
        **{f"shopee_enrich_{k}": v for k, v in enr_shopee.items()},
        **{f"tiktok_enrich_{k}": v for k, v in enr_tiktok.items()},
        **{f"amazon_enrich_{k}": v for k, v in enr_amazon.items()},
        **{f"status_{k}": v for k, v in lote.items()},
    }
