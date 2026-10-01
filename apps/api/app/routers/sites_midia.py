"""Fotos e vídeos com marca d'água para os sites Charlots e Uranyx (01/10/2026).

Duas portas:

- `GET /api/sites/midia?sku=…&sku=…` — servidor a servidor, com o MESMO
  Bearer do estoque (`SITES_ESTOQUE_TOKENS`; o token diz o site). Devolve a
  lista de fotos e vídeos do produto com links prontos. Lista branca: nenhum
  nome de arquivo, de pasta ou id interno sai daqui. 60 chamadas/min por site.
- `GET /api/sites/midia/a/{token}` — sem Bearer: é o navegador do lojista que
  busca, direto do DaVinci (um salto, Range nativo). O token é o link cifrado
  da listagem (services/sites_midia.py); vale de 3 a 6 h, e trocar o token do
  site derruba todos. Qualquer problema com o link é o mesmo 404.

Os bytes saem SEMPRE com a marca d'água do site (services/sites_midia_derivados.py).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.routers.auth import _client_ip
from app.routers.sites_estoque import _RX_SKU, site_do_token
from app.services import sites_midia as midia
from app.services import sites_midia_derivados as derivados
from app.services.marketing.anexos import disposicao_segura
from app.services.rate_limit import RateLimitError, sliding_window_check

logger = structlog.get_logger()

router = APIRouter(prefix="/api/sites/midia", tags=["sites"], include_in_schema=False)

LIMITE_CHAMADAS_MIN = 60
MAX_SKUS = 200
# Quanto um pedido de foto ainda não gerada espera a geração (que continua
# se ele desistir).
ESPERA_FOTO_S = 45
_RX_NOME_BAIXAR = re.compile(r"[a-z0-9][a-z0-9-]{0,80}")
_MIME_EXT = {"image/jpeg": "jpg", "image/webp": "webp", "video/mp4": "mp4"}


def _erro(status_code: int, codigo: str, **headers: str) -> HTTPException:
    return HTTPException(
        status_code,
        detail={"code": codigo},
        headers={"Cache-Control": "no-store", **headers},
    )


async def _limite(site: str) -> None:
    try:
        await sliding_window_check(
            key=f"sites_midia:rl:{site}", limit=LIMITE_CHAMADAS_MIN, window_seconds=60
        )
    except RateLimitError as e:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "sites_limite"},
            headers={"Retry-After": str(e.retry_after)},
        ) from None
    except Exception as e:  # noqa: BLE001 — Redis fora do ar não derruba a listagem
        logger.warning("sites_midia_rate_limit_indisponivel", site=site, err=str(e)[:120])


def _skus(sku: list[str] | None) -> list[str]:
    if not sku:
        raise HTTPException(422, detail={"code": "sites_sku_invalido"})
    if len(sku) > MAX_SKUS:
        raise HTTPException(422, detail={"code": "sites_skus_demais", "max": MAX_SKUS})
    for s in sku:
        if not _RX_SKU.fullmatch(s):
            raise HTTPException(422, detail={"code": "sites_sku_invalido"})
    return sku


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


def _estado_dos_itens(
    itens: list[midia.Item],
) -> list[tuple[midia.Item, dict[str, Any] | None, bool]]:
    """(item, meta, pronto) lendo o disco — roda numa thread."""
    saida = []
    for item in itens:
        g = derivados.grupo(item.site, item.caminho)
        meta = derivados.ler_meta(g)
        pronto = derivados.video_pronto(g) if item.tipo == "video" else True
        saida.append((item, meta, pronto))
    return saida


@router.get("")
async def listar(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    site: Annotated[str, Depends(site_do_token)],
    sku: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    """Fotos e vídeos do produto (pelos SKUs dele), com links de 3 a 6 h."""
    inicio = time.perf_counter()
    await _limite(site)
    pedidos = _skus(sku)
    derivados.iniciar()

    pastas = await midia.pastas_do_site(session, site, pedidos)
    # Devolve a conexão ANTES do sidecar: a listagem de 12 pastas não pode
    # segurar uma conexão do pool parada.
    await session.close()
    try:
        itens = await midia.itens_das_pastas(site, pastas)
    except midia.ListagemIndisponivel:
        raise _erro(503, "midia_indisponivel", **{"Retry-After": "30"}) from None

    agora = time.time()
    exp = midia.expiracao(agora)
    saida: list[dict[str, Any]] = []
    fotos = videos = preparando = 0
    for item, meta, pronto in await asyncio.to_thread(_estado_dos_itens, itens):
        if derivados.falhou_recente(meta, agora):
            continue
        variantes = ("mini", "grande", "video") if item.tipo == "video" else ("mini", "grande")
        urls: dict[str, str] = {}
        if pronto:
            for v in variantes:
                url = midia.link(item, v, exp)
                if url:
                    urls[v] = url
        else:
            preparando += 1
            derivados.enfileirar_video(site, item.caminho)
        derivados.agendar_conferencia(site, item.caminho, meta)
        if item.tipo == "video":
            videos += 1
        else:
            fotos += 1
        meta = meta or {}
        saida.append(
            {
                "id": item.id,
                "tipo": item.tipo,
                "estado": "pronto" if pronto else "preparando",
                "codigos": midia.codigos(site, item.nome),
                "cores": midia.cores(item.nome),
                "largura": meta.get("largura"),
                "altura": meta.get("altura"),
                "duracao": meta.get("duracao"),
                "urls": urls,
            }
        )

    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization"
    # Nunca o token, nem caminho do MEGA.
    logger.info(
        "sites_midia_lista",
        site=site,
        ip=_client_ip(request),
        skus=len(pedidos),
        pastas=len(pastas),
        total=len(saida),
        preparando=preparando,
        ms=round((time.perf_counter() - inicio) * 1000),
    )
    return {
        "site": site,
        "gerado_em": _iso(agora),
        "links_validos_ate": _iso(exp),
        "links_validos_s": exp - int(agora),
        "total": len(saida),
        "fotos": fotos,
        "videos": videos,
        "preparando": preparando,
        "itens": saida,
    }


@router.get("/a/{token}")
async def arquivo(
    token: str,
    baixar: Annotated[str | None, Query()] = None,
    nome: Annotated[str | None, Query()] = None,
) -> FileResponse:
    """Os bytes, com a marca d'água. Range/206, ETag e If-Range pelo Starlette."""
    link = midia.ler_link(token)
    if link is None:
        raise _erro(404, "midia_link_invalido")
    derivados.iniciar()
    para_baixar = (baixar or "").lower() in ("1", "true", "sim")
    variante = "grande" if para_baixar and link.variante == "mini" else link.variante
    g = derivados.grupo(link.site, link.caminho)

    achado = derivados.pronto(g, variante)
    if achado is None:
        if derivados.falhou_recente(derivados.ler_meta(g)):
            raise _erro(404, "midia_indisponivel")
        if midia.eh_video(link.nome):
            derivados.enfileirar_video(link.site, link.caminho)
            raise _erro(404, "midia_preparando")
        try:
            await asyncio.wait_for(
                asyncio.shield(derivados.tarefa_foto(link.site, link.caminho)), ESPERA_FOTO_S
            )
        except TimeoutError:
            raise _erro(503, "midia_ocupada", **{"Retry-After": "5"}) from None
        except derivados.ErroDeMidia as exc:
            extra = {"Retry-After": "5"} if exc.status == 503 else {}
            raise _erro(exc.status, exc.codigo, **extra) from None
        achado = derivados.pronto(g, variante)
        if achado is None:
            raise _erro(503, "midia_ocupada", **{"Retry-After": "5"})

    caminho, mime, st = achado
    derivados.marcar_uso(caminho, st)
    headers = {
        "Cache-Control": "private, max-age=21600",
        "X-Content-Type-Options": "nosniff",
    }
    if para_baixar:
        base = nome if nome and _RX_NOME_BAIXAR.fullmatch(nome) else f"{link.site}-midia"
        headers["Content-Disposition"] = disposicao_segura(
            "attachment", f"{base}.{_MIME_EXT.get(mime, 'bin')}"
        )
    else:
        headers["Content-Disposition"] = "inline"
    return FileResponse(caminho, media_type=mime, headers=headers, stat_result=st)


_RX_LINK_NO_PATH = re.compile(r"(/api/sites/midia/a/)[^/\s?]+")


def mascarar_link_no_access_log() -> None:
    """Tira o token do link do access log do uvicorn (o token vai no PATH).
    Mesmo molde de `marketing_creatives.mascarar_link_no_access_log`."""

    class _Mascara(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            args = record.args
            if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
                record.args = (*args[:2], _RX_LINK_NO_PATH.sub(r"\1***", args[2]), *args[3:])
            return True

    logging.getLogger("uvicorn.access").addFilter(_Mascara())
