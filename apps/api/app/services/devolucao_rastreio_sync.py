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

import unicodedata
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
    session: AsyncSession,
    key: str,
    linhas: list[Logistica],
    ja_entregues: set[str] | None = None,
) -> dict[str, ReturnInfo]:
    """Chama o `returns_por_pedido` do módulo do marketplace. Import tardio e
    tolerante: módulo sem a função (ainda) ou erro → dict vazio + log.

    `ja_entregues` poupa a consulta extra do detalhe nas devoluções que já
    constam entregues (só a Shopee aceita o parâmetro hoje)."""
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
        try:
            out = await fn(session, linhas, ja_entregues=ja_entregues or set())
        except TypeError:
            out = await fn(session, linhas)  # marketplace que ainda não aceita
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


# "Objeto entregue ao destinatário" no pacote de VOLTA = chegou em NÓS (numa
# devolução, o destinatário é o vendedor). "Entregue ao remetente" fica de
# fora de propósito: aí o pacote voltou pro comprador, não pra gente.
_TEXTO_ENTREGUE = "entregue ao destinat"


def _texto_diz_entregue(localizacao: str | None) -> bool:
    txt = unicodedata.normalize("NFKD", (localizacao or "").casefold())
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    alvo = unicodedata.normalize("NFKD", _TEXTO_ENTREGUE)
    alvo = "".join(c for c in alvo if not unicodedata.combining(c))
    return alvo in txt


async def _puxar_correios(session: AsyncSession) -> dict[str, int]:
    """Rede de segurança do rastreio do pacote de VOLTA.

    Até aqui a localização física só entrava pelo PUSH do 17track. Push é
    evento: se ele acontece antes de a gente passar a escutar (ou se perde),
    a linha fica parada pra sempre — foi o caso do 293437, entregue pelos
    Correios em 09/09 e ainda sem "Chegou em" (Eduardo, 10/09). Agora o sync
    também PERGUNTA o estado dos códigos já registrados. Consulta é grátis no
    17track (crédito só se gasta ao registrar), então roda a cada rodada.
    """
    linhas = list(
        (
            await session.execute(
                select(DevolucaoRastreio).where(
                    DevolucaoRastreio.rastreio_auto.is_not(None),
                    DevolucaoRastreio.pacote_entregue_em.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    por_codigo = {
        (r.rastreio_auto or "").strip(): r
        for r in linhas
        if logistica_track.is_correios(r.rastreio_auto or "")
    }
    resumo = {"consultados": len(por_codigo), "entregues": 0, "localizacoes": 0}

    # Antes de perguntar: a prova pode já estar guardada aqui. O 17track APAGA
    # o número depois da entrega (consultar AP444879986BR hoje devolve "não
    # conhece"), então o evento que ele empurrou na época é a única cópia que
    # sobrou — e ela está em `localizacao_auto`. Foi o caso do 293437, que o
    # Eduardo apontou: entregue em 09/09, texto salvo, campo vazio.
    for row in linhas:
        if _texto_diz_entregue(row.localizacao_auto) and row.pacote_entregue_em is None:
            row.pacote_entregue_em = row.localizacao_auto_data or datetime.now(UTC)
            resumo["entregues"] += 1

    if not por_codigo:
        if resumo["entregues"]:
            await session.commit()
        return resumo
    try:
        got = await logistica_track.fetch_detalhado(sorted(por_codigo))
    except Exception as e:  # noqa: BLE001 — 17track fora do ar não derruba o sync
        logger.warning("devolucao_rastreio_pull_falhou", err=str(e)[:200])
        return resumo
    agora = datetime.now(UTC)
    for numero, dados in (got.get("info") or {}).items():
        row = por_codigo.get(numero)
        if row is None:
            continue
        loc = (dados.get("localizacao") or "").strip()
        if loc and loc != (row.localizacao_auto or ""):
            row.localizacao_auto = loc
            row.localizacao_auto_data = dados.get("sync_at") or agora
            resumo["localizacoes"] += 1
        if str(dados.get("status") or "").strip() == "Delivered":
            row.pacote_entregue_em = dados.get("sync_at") or agora
            resumo["entregues"] += 1
    if resumo["entregues"] or resumo["localizacoes"]:
        await session.commit()
    return resumo


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

    # Devolução já marcada como entregue não precisa da consulta extra de
    # detalhe a cada rodada — o dado não volta atrás.
    ja_entregues = set(
        (
            await session.execute(
                select(DevolucaoRastreio.pedido_bling).where(
                    DevolucaoRastreio.pacote_entregue_em.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )

    infos: dict[str, ReturnInfo] = {}
    for key, ls in por_mk.items():
        got = await _fetch_por_marketplace(session, key, ls, ja_entregues)
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
            # Só carimba a chegada; nunca apaga (a plataforma pode parar de
            # informar numa rodada e a data não pode sumir da tela).
            if info.entregue_em and row.pacote_entregue_em is None:
                row.pacote_entregue_em = info.entregue_em
            row.auto_sync_at = agora
            gravados += 1
    # Commit SEMPRE (mesmo sem devolução nova): o refresh de token dos clients
    # (TikTok/Shopee/ML) só dá flush — sem commit o token rotacionado se perde.
    await session.commit()

    pull = await _puxar_correios(session)

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
        "correios_consultados": pull["consultados"],
        "correios_entregues": pull["entregues"],
        "correios_localizacoes": pull["localizacoes"],
    }
    logger.info("devolucao_rastreio_sync_done", **summary)
    return summary
