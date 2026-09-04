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

from collections.abc import Collection
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
      -- Finalizados nunca entram (o cleanup_finalizados removeria em seguida;
      -- sem este filtro a linha ia e voltava a cada rodada). Entregue ENTRA —
      -- fica no painel os 90 dias da janela de reclamação e aí o cleanup tira.
      AND (sb.nome IS NULL OR sb.nome NOT IN ('Cancelado', 'Resolvido', 'Perdimento'))
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
    RETURNING l.id
    """
)


async def refresh_status_bling(session: AsyncSession) -> list[UUID]:
    """Realinha `logistica.status_bling` com a situação atual do espelho
    `bling_orders`. Retorna os ids das linhas que mudaram. É o que faz um pedido
    que virou "Problemas" DEPOIS de entrar na tabela aparecer no painel (o
    passe-livre de 360d olha esse campo).

    A lista de ids é o sinal BARATO de "esse pedido mexeu": o recarregar usa ela
    pra não varrer as milhares de linhas que continuam iguais."""
    res = await session.execute(_REFRESH_STATUS_SQL)
    mudaram = list(res.scalars().all())
    await session.commit()
    logger.info("logistica_refresh_status_bling", changed=len(mudaram))
    return mudaram


# Pedido do usuário (25/08): situações que encerram o acompanhamento somem da
# aba sozinhas. Cancelado/Resolvido/Perdimento saem na hora; Entregue segura 90
# dias (janela de reclamação do comprador no marketplace) e depois sai. Sem
# data não apaga — melhor sobrar uma linha que sumir cedo demais.
_CLEANUP_SQL = text(
    """
    DELETE FROM davinci.logistica
     WHERE lower(coalesce(status_bling, '')) IN ('cancelado', 'resolvido', 'perdimento')
        OR (
            lower(coalesce(status_bling, '')) = 'entregue'
            AND data IS NOT NULL
            AND data < CURRENT_DATE - 90
        )
    """
)


async def cleanup_finalizados(session: AsyncSession) -> int:
    """Apaga da aba Logística os pedidos que não precisam mais de acompanhamento:
    situação Bling Cancelado, Resolvido ou Perdimento — e Entregue com mais de
    90 dias. Some SÓ da Logística; o pedido segue intacto em `bling_orders` e
    nas outras telas. Roda logo depois do `refresh_status_bling` (é o
    realinhamento que traz a situação fresca) em toda ingestão e no recarregar
    do painel, então a tabela se mantém enxuta sem faxina manual."""
    res = await session.execute(_CLEANUP_SQL)
    removed = res.rowcount or 0
    await session.commit()
    logger.info("logistica_cleanup_finalizados", removed=removed)
    return removed


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
    está na tabela (todas as plataformas — o UPDATE é global e barato) e limpa
    os finalizados (Cancelado/Resolvido/Perdimento e Entregue +90d)."""
    refreshed = len(await refresh_status_bling(session))
    removed = await cleanup_finalizados(session)
    inserted = await _ingest_platform(session, "ml", dias)
    problemas = await _ingest_platform(
        session, "ml", problemas_dias, so_problemas=True
    )
    enr = await logistica_meli.enrich_recent(
        session, limit=enrich_limit, only_empty=True
    )
    return {
        "status_refresh": refreshed,
        "cleanup": removed,
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
    out: dict[str, int] = {"status_refresh": len(await refresh_status_bling(session))}
    out["cleanup"] = await cleanup_finalizados(session)
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


async def _ids_pendentes(
    session: AsyncSession, *, extras: Collection[UUID] = ()
) -> dict[str, list[UUID]]:
    """Ids das linhas PENDENTES por plataforma — a mesma regra que o painel usa
    pra decidir o que MOSTRAR (esconde `resolvido and not monitorar`). É o
    conjunto que o operador está vendo e quer ver atualizado.

    `extras` entra mesmo estando escondido: o recarregar passa aí as linhas cuja
    situação no Bling acabou de mudar — mudou, então precisa ser re-avaliada,
    ainda que a foto anterior a desse como resolvida."""
    forcadas = set(extras)
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
        if r.id in forcadas:
            pend[chave].append(r.id)
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
            and not logistica_match.devolucao_travada(
                cands,
                plataforma=r.plataforma,
                meli_status=r.meli_status or {},
                status_bling=r.status_bling,
            )
        ):
            continue
        pend[chave].append(r.id)
    return pend


async def recarregar_ml(session: AsyncSession) -> dict[str, int]:
    """Recarga sob demanda do botão "recarregar" das abas de marketplace.

    Num clique cobre os três lados — situação no Bling, Status Bling e Status
    Plataforma — mas SÓ das linhas que realmente mexeram, não das ~9 mil. O alvo
    é a união de:

    - as linhas cuja situação no Bling mudou agora (o `refresh_status_bling`
      devolve exatamente essas — é um UPDATE barato, sem chamada de API);
    - as pendentes do painel (o que o operador está olhando).

    Varrer tudo custava ~55min mesmo com a rajada concorrente do
    `logistica_enrich`; o delta resolve em segundos. O preço: uma linha JÁ
    resolvida (escondida) cujo status mudou só do lado do MARKETPLACE, sem mexer
    na situação do Bling, não entra sozinha — pra essas continua valendo o ⟳ da
    linha.

    Roda em background (arq) porque ainda pode passar dos 100s do Cloudflare.
    """
    mudaram = await refresh_status_bling(session)
    # Quem acabou de virar Cancelado/Resolvido/Perdimento (ou Entregue velho)
    # sai daqui mesmo — o _ids_pendentes só considera linhas existentes, então
    # os ids apagados em `mudaram` não voltam.
    removed = await cleanup_finalizados(session)
    # Pós-venda que muda DEPOIS da entrega (devolução, entrega tardia) não
    # aparece nas pendentes do painel (a linha "Concluído"/"Em trânsito" fica
    # escondida como resolvida): os sweeps Shopee/TikTok/ML/Amazon re-olham as
    # escondidas em lote e devolvem quem mudou de vida — esses ids entram como
    # extras e furam o escondimento. (Na Amazon o cego era a própria ENTREGA:
    # "Enviado | Coletado" resolvido como Em andamento nunca mais era
    # consultado e o pedido não virava Entregue no Bling.)
    sweep = await logistica_shopee.sweep_pos_venda(session)
    sweep_tk = await logistica_tiktok.sweep_pos_venda(session)
    sweep_ml = await logistica_meli.sweep_pos_venda(session)
    sweep_amz = await logistica_amazon.sweep_pos_venda(session)
    alvo = await _ids_pendentes(
        session,
        extras=[
            *mudaram,
            *sweep["ids"],
            *sweep_tk["ids"],
            *sweep_ml["ids"],
            *sweep_amz["ids"],
        ],
    )
    logger.info(
        "logistica_recarregar_inicio",
        status_refresh=len(mudaram),
        cleanup=removed,
        sweep_shopee=len(sweep["ids"]),
        sweep_tiktok=len(sweep_tk["ids"]),
        sweep_ml=len(sweep_ml["ids"]),
        sweep_amazon=len(sweep_amz["ids"]),
        **{f"alvo_{k}": len(v) for k, v in alvo.items()},
    )
    enr = await logistica_meli.enrich_recent(session, ids=alvo["ml"], only_empty=False)
    enr_shopee = await logistica_shopee.enrich_recent(
        session, ids=alvo["shopee"], only_empty=False
    )
    enr_tiktok = await logistica_tiktok.enrich_recent(
        session, ids=alvo["tiktok"], only_empty=False
    )
    enr_amazon = await logistica_amazon.enrich_recent(
        session, ids=alvo["amazon"], only_empty=False
    )
    lote = await logistica_bling.aplicar_status_em_lote(
        session, [i for ids in alvo.values() for i in ids]
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
        "status_refresh": len(mudaram),
        "cleanup": removed,
        **{f"enrich_{k}": v for k, v in enr.items()},
        **{f"shopee_enrich_{k}": v for k, v in enr_shopee.items()},
        **{f"tiktok_enrich_{k}": v for k, v in enr_tiktok.items()},
        **{f"amazon_enrich_{k}": v for k, v in enr_amazon.items()},
        **{f"status_{k}": v for k, v in lote.items()},
    }
