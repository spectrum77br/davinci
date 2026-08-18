"""Importação diária dos novos pedidos Mercado Livre pra aba Logística.

A tabela `logistica` foi populada por backfill manual; depois o usuário optou
por manter só "de hoje pra frente". Esta rotina roda todo dia, insere os pedidos
ML novos vindos do Bling (`bling_orders`) e enriquece a assinatura de status do
Meli — assim a lista cresce sozinha sem backfill manual.

O INSERT é idempotente (`NOT EXISTS` por `pedido_bling`), então re-rodar não
duplica; a janela de dias dá folga pra pegar pedidos que o sync do Bling só
trouxe com atraso.

Além de inserir, cada rodada REALINHA o `status_bling` de quem já está na
tabela com o espelho `bling_orders` (que o sync do Bling mantém fresco) — sem
isso o painel ficava com a foto do momento da ingestão e um pedido que virava
"Problemas" depois nunca ganhava o passe-livre de 360 dias (caso real: 289863,
18/08).
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
      AND (:so_problemas = false OR sb.nome = 'Problemas')
      AND bo.numero IS NOT NULL
      AND bo.situacao IS DISTINCT FROM 'excluido'
      AND NOT EXISTS (
          SELECT 1 FROM davinci.logistica l WHERE l.pedido_bling = bo.numero
      )
    ORDER BY bo.numero, bo.data DESC
    """
)


# O status_bling de quem JÁ está na tabela ficava congelado na foto da
# ingestão (só o ⟳ por linha atualizava, via API). Como o espelho
# `bling_orders` é mantido fresco pelo sync do Bling, um UPDATE barato realinha
# todo mundo sem nenhuma chamada de API. item_index=0 é a linha canônica do
# pedido (a tabela tem uma linha por item).
_REFRESH_STATUS_SQL = text(
    """
    UPDATE davinci.logistica l
       SET status_bling = sb.nome,
           updated_at = now()
      FROM davinci.bling_orders bo
      JOIN davinci.situacao_bling sb ON sb.id::text = bo.situacao
     WHERE bo.numero = l.pedido_bling
       AND bo.item_index = 0
       AND sb.nome IS DISTINCT FROM l.status_bling
    """
)


async def refresh_status_bling(session: AsyncSession) -> int:
    """Realinha `logistica.status_bling` com a situação atual do espelho
    `bling_orders`. Retorna quantas linhas mudaram. É o que faz um pedido que
    virou "Problemas" DEPOIS de entrar na tabela aparecer no painel (o
    passe-livre de 360d olha esse campo)."""
    res = await session.execute(_REFRESH_STATUS_SQL)
    changed = res.rowcount or 0
    await session.commit()
    logger.info("logistica_refresh_status_bling", changed=changed)
    return changed


async def _ingest_platform(
    session: AsyncSession, platform: str, dias: int, *, so_problemas: bool = False
) -> int:
    """Insere os pedidos novos (janela de `dias`) de UMA plataforma. Idempotente
    (NOT EXISTS por pedido_bling). `so_problemas=True` restringe aos pedidos com
    situação Bling "Problemas" (a passada extra de 360 dias). Retorna quantas
    linhas entraram."""
    label = _PLATAFORMA_LABELS[platform]
    res = await session.execute(
        _INSERT_SQL,
        {
            "platform": platform,
            "label": label,
            "dias": dias,
            "so_problemas": so_problemas,
        },
    )
    inserted = res.rowcount or 0
    await session.commit()
    logger.info(
        "logistica_ingest_inserted",
        platform=platform,
        inserted=inserted,
        dias=dias,
        so_problemas=so_problemas,
    )
    return inserted


async def run_ingest_ml_daily(
    session: AsyncSession,
    *,
    dias: int = 60,
    problemas_dias: int = 360,
    enrich_limit: int = 400,
) -> dict[str, int]:
    """Insere os pedidos ML novos (janela de `dias`, 60 por padrão — pedido do
    usuário 18/08) + os com situação Bling "Problemas" dos últimos
    `problemas_dias`, e enriquece o status do Meli das linhas ML ainda vazias
    (mais recentes primeiro). Antes de tudo realinha o status_bling de quem já
    está na tabela (todas as plataformas — o UPDATE é global e barato)."""
    refreshed = await refresh_status_bling(session)
    inserted = await _ingest_platform(session, "ml", dias)
    problemas = await _ingest_platform(
        session, "ml", problemas_dias, so_problemas=True
    )
    enr = await logistica_meli.enrich_recent(
        session, limit=enrich_limit, only_empty=True
    )
    return {
        "status_refresh": refreshed,
        "inserted": inserted,
        "problemas": problemas,
        **{f"enrich_{k}": v for k, v in enr.items()},
    }


async def run_ingest_marketplaces_daily(
    session: AsyncSession,
    *,
    dias: int = 60,
    problemas_dias: int = 360,
    enrich_limit: int = 400,
) -> dict[str, int]:
    """Insere os pedidos novos de Shopee/TikTok/Amazon pra aba Logística
    (janela de `dias` + "Problemas" dos últimos `problemas_dias`) e enriquece o
    Status Plataforma das três (Shopee via order_status; TikTok via order
    status + rastreio; Amazon via OrderStatus + EasyShip) nas linhas ainda
    vazias. Retorna o total inserido por plataforma + o resumo de cada enrich.
    Também realinha o status_bling primeiro (idempotente; o cron do ML faz o
    mesmo — tanto faz qual roda antes)."""
    out: dict[str, int] = {"status_refresh": await refresh_status_bling(session)}
    for platform in ("shopee", "tiktok", "amazon"):
        out[platform] = await _ingest_platform(session, platform, dias)
        out[f"{platform}_problemas"] = await _ingest_platform(
            session, platform, problemas_dias, so_problemas=True
        )
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
        if (
            resolvido
            and not logistica_match.deve_monitorar(cands, r.status_bling)
            and not logistica_match.problema_bling_visivel(r.status_bling, r.data)
        ):
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
    exatamente o que o operador está vendo. Antes de escolher as pendentes,
    realinha o status_bling com o espelho do Bling — assim linha resolvida cujo
    BLING mudou (ex.: virou "Problemas") volta pro conjunto sozinha. Mudança só
    no lado do MARKETPLACE de linha resolvida continua exigindo o ⟳ por linha.
    Roda em background (arq) porque mesmo assim pode passar dos 100s do
    Cloudflare.
    """
    refreshed = await refresh_status_bling(session)
    pend = await _ids_pendentes(session)
    logger.info(
        "logistica_recarregar_pendentes",
        status_refresh=refreshed,
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
        "status_refresh": refreshed,
        **{f"enrich_{k}": v for k, v in enr.items()},
        **{f"shopee_enrich_{k}": v for k, v in enr_shopee.items()},
        **{f"tiktok_enrich_{k}": v for k, v in enr_tiktok.items()},
        **{f"amazon_enrich_{k}": v for k, v in enr_amazon.items()},
        **{f"status_{k}": v for k, v in lote.items()},
    }
