"""Rastreio AUTOMÁTICO do pacote que VOLTA (aba Acompanhamento de Devoluções).

Eduardo (03/09): "o TikTok não está pegando o número de rastreio correto" /
"esse rastreio está incorreto, precisa sempre estar atualizadinho" / "em
devolução desde todas as datas estão iguais". A aba mostrava o rastreio da
ENTREGA original (painel Logística); o que interessa ali é a DEVOLUÇÃO — que
tem código e transportadora próprios na returns API de cada marketplace.

Fluxo (cron a cada 30 min + botão de recarregar quando houver):
  1. pedidos hoje em "Aguardando Devolução" (83957) + a linha da Logística de
     cada um (pedido_marketplace/conta/plataforma);
  2. por marketplace, `returns_por_pedido(session, linhas)` (contrato em
     services/devolucao_returns.ReturnInfo — implementado em logistica_tiktok /
     logistica_shopee / logistica_meli);
  3. grava em `devolucao_rastreio.*_auto` (grão pedido; o MANUAL continua
     mandando na aba) — inclusive `devolucao_criada_em`, que vira o "Em
     devolução desde" real;
  4. registra no 17track os códigos Correios (`...BR`) novos — a localização
     do pacote de volta chega pelo webhook (routers/logistica_track).

Best-effort em todas as camadas: um marketplace fora do ar não derruba os
outros; um pedido sem devolução conhecida fica como está.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import DevolucaoRastreio, Logistica
from app.services import logistica_rules, logistica_track
from app.services.devolucao_returns import ReturnInfo

logger = structlog.get_logger()

_SITUACAO_AGUARDANDO_DEVOLUCAO = "83957"


def _qualified(table: str) -> str:
    return f'"{get_settings().database_schema}".{table}'


def _plataforma_key(plataforma: str | None) -> str | None:
    p = (plataforma or "").strip().lower()
    if p in logistica_rules._ML_PLATAFORMAS:
        return "ml"
    if p in logistica_rules._SHOPEE_PLATAFORMAS:
        return "shopee"
    if p in logistica_rules._TIKTOK_PLATAFORMAS:
        return "tiktok"
    return None


async def _fetch_por_marketplace(
    session: AsyncSession, key: str, linhas: list[Logistica]
) -> dict[str, ReturnInfo]:
    """Chama o `returns_por_pedido` do módulo do marketplace. Import tardio e
    tolerante: módulo sem a função (ainda) ou erro → dict vazio + log."""
    try:
        if key == "tiktok":
            from app.services import logistica_tiktok as mod
        elif key == "shopee":
            from app.services import logistica_shopee as mod
        else:
            from app.services import logistica_meli as mod
        fn = getattr(mod, "returns_por_pedido", None)
        if fn is None:
            logger.warning("devolucao_rastreio_sync_sem_fetcher", marketplace=key)
            return {}
        out = await fn(session, linhas)
        return dict(out or {})
    except Exception as e:  # noqa: BLE001 — um marketplace não derruba os outros
        logger.warning("devolucao_rastreio_sync_fetch_falhou", marketplace=key, err=str(e)[:300])
        return {}


async def pedidos_em_devolucao(session: AsyncSession) -> list[str]:
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT v.pedido_bling::text AS pedido "  # noqa: S608
                f"FROM {_qualified('vw_devolucoes')} v "
                "WHERE v.situacao = :s"
            ),
            {"s": _SITUACAO_AGUARDANDO_DEVOLUCAO},
        )
    ).all()
    return [r[0] for r in rows if r[0]]


async def _linhas_logistica(
    session: AsyncSession, pedidos: Collection[str]
) -> dict[str, Logistica]:
    """Linha da Logística por pedido (a mais recente quando há mais de uma)."""
    if not pedidos:
        return {}
    rows = (
        await session.execute(select(Logistica).where(Logistica.pedido_bling.in_(list(pedidos))))
    ).scalars().all()
    por_pedido: dict[str, Logistica] = {}
    for r in rows:
        cur = por_pedido.get(r.pedido_bling or "")
        if cur is None or (r.updated_at and cur.updated_at and r.updated_at > cur.updated_at):
            por_pedido[r.pedido_bling or ""] = r
    return por_pedido


async def run(session: AsyncSession, *, pedidos: Collection[str] | None = None) -> dict[str, int]:
    """Sincroniza o rastreio automático das devoluções. `pedidos` restringe
    (o recarregar de um pedido); sem ele, todos os 83957."""
    alvo = list(pedidos) if pedidos is not None else await pedidos_em_devolucao(session)
    linhas = await _linhas_logistica(session, alvo)
    por_mk: dict[str, list[Logistica]] = {}
    for linha in linhas.values():
        k = _plataforma_key(linha.plataforma)
        if k:
            por_mk.setdefault(k, []).append(linha)

    infos: dict[str, ReturnInfo] = {}
    for key, ls in por_mk.items():
        got = await _fetch_por_marketplace(session, key, ls)
        for pedido, info in got.items():
            if isinstance(info, ReturnInfo) and pedido:
                infos[str(pedido)] = info

    agora = datetime.now(UTC)
    gravados = 0
    novos_codigos: list[str] = []
    if infos:
        existentes = {
            r.pedido_bling: r
            for r in (
                await session.execute(
                    select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling.in_(list(infos)))
                )
            ).scalars().all()
        }
        for pedido, info in infos.items():
            row = existentes.get(pedido)
            if row is None:
                row = DevolucaoRastreio(pedido_bling=pedido)
                session.add(row)
            tracking = (info.tracking or "").strip() or None
            if tracking and tracking != row.rastreio_auto:
                # Código novo → localização anterior (de outro código) não vale mais.
                row.localizacao_auto = None
                row.localizacao_auto_data = None
                if logistica_track.is_correios(tracking):
                    novos_codigos.append(tracking)
            row.rastreio_auto = tracking
            row.transportadora_auto = (info.carrier or "").strip() or None
            row.devolucao_status_auto = (info.status or "").strip() or None
            row.devolucao_id_auto = (info.return_id or "").strip() or None
            row.fonte_auto = info.fonte
            if info.created_at:
                row.devolucao_criada_em = info.created_at
            if info.updated_at:
                row.devolucao_atualizada_em = info.updated_at
            row.auto_sync_at = agora
            gravados += 1
    # Commit SEMPRE (mesmo sem devolução nova): o refresh de token dos clients
    # (TikTok/Shopee/ML) só dá flush — sem commit o token rotacionado se perde.
    await session.commit()

    registrados = 0
    if novos_codigos:
        try:
            await logistica_track.register(sorted(set(novos_codigos)))
            registrados = len(set(novos_codigos))
        except Exception as e:  # noqa: BLE001 — 17track fora do ar não derruba o sync
            logger.warning("devolucao_rastreio_sync_17track_falhou", err=str(e)[:200])

    summary = {
        "pedidos": len(alvo),
        "com_logistica": len(linhas),
        "devolucoes": len(infos),
        "gravados": gravados,
        "codigos_17track": registrados,
    }
    logger.info("devolucao_rastreio_sync_done", **summary)
    return summary
