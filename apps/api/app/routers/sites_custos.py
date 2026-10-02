"""Custo "Kit 1" da Tabela de Preços para os sites Charlots e Uranyx — só leitura (02/10/2026).

Marco: o preço de atacado do site da Uranyx é "o preço de custo × 1,3 para
cada produto [...] pegar de lá do DaVinci Kit 1 preço de custo". Quem faz a
conta é o site (PHP, na Hostinger), no mesmo cron que já lê o estoque: o
multiplicador, a regra das "várias versões" (maior ou menor custo) e o
interruptor do preço automático são do q-admin dele, não daqui. Daqui sai só
o custo, servidor a servidor, com o MESMO Bearer do estoque.

## O que sai daqui

Por linha ATIVA da Tabela de Preços (`pricing_products`) do recorte do site:
`skus` (os códigos da coluna SKU, sem espaço, em minúsculas e sem repetir),
`nome`, `custo_kit1` e `atualizado_em`. Nada de id, dono, `bling_cost_price`,
Kit 2 a 8, pasta do MEGA, segmento ou EAN.

- `custo_kit1` é o "Kit 1" da tela: no celular, aparelho + fone com fio; no
  eletro e no acessório, o próprio produto. A coluna é NOT NULL com padrão 0,
  então 0 (ou negativo) quer dizer "não preenchido" e sai `null` — o site
  trata como sem custo e não mexe no preço.
- Uma linha agrupa as cores de uma versão (`dg052,dg053,dg054`). O mesmo
  aparelho em duas versões (A17 12/128 a 560 e 12/64 a 495) são duas linhas:
  quem escolhe a maior ou a menor é o site.

## O recorte (mais estreito que o do estoque)

O custo é sensível: um site não vê o custo da outra marca. A pasta de fotos
diz de quem é a linha, como na mídia (`RAIZES` de `services/sites_midia`):

1. Linha com pasta dentro da raiz de OUTRO site fica de fora, mesmo que um
   código case a regex — os acessórios `a0NN` existem nas duas marcas (o
   chaveiro `a075` mora em /Malas; o fone `a003` em /Celular).
2. Linha com pasta do site, ou sem pasta (os `dg1xxx` "Diversos", o T8
   `dg101`, os cestos `uaf001m1.2l`…), entra se algum código casar a regex
   do site em `ESCOPOS`; e só os códigos que casam saem.
3. Segmento Apple fica de fora (revenda, não é marca da casa).

As travas da porta são as do estoque: `site_do_token` (fecha sem
configuração, o token diz o site), fora do openapi, 30 chamadas/min por site
com o Redis falhando aberto, lista branca na saída e nada de cache
(`no-store`). O log diz quem chamou e quantas linhas saíram — nunca o token,
nunca um custo.
"""

from __future__ import annotations

import posixpath
import re
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.pricing import PricingProduct
from app.models.segment import Segment
from app.routers.auth import _client_ip
from app.routers.sites_estoque import ESCOPOS, _iso, site_do_token
from app.services.rate_limit import RateLimitError, sliding_window_check
from app.services.sites_midia import RAIZES

logger = structlog.get_logger()

# `include_in_schema=False` no router inteiro: o /api/openapi.json é público.
router = APIRouter(prefix="/api/sites", tags=["sites"], include_in_schema=False)

LIMITE_CHAMADAS_MIN = 30


async def _limite(site: str) -> None:
    try:
        await sliding_window_check(
            key=f"sites_custos:rl:{site}",
            limit=LIMITE_CHAMADAS_MIN,
            window_seconds=60,
        )
    except RateLimitError as e:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "sites_limite"},
            headers={"Retry-After": str(e.retry_after)},
        ) from None
    except Exception as e:  # noqa: BLE001 — Redis fora do ar não derruba o preço do site
        logger.warning("sites_custos_rate_limit_indisponivel", site=site, err=str(e)[:120])


def tokens(sku: str | None) -> list[str]:
    """`"uaf001m1.110, UAF001M1.220"` → `["uaf001m1.110", "uaf001m1.220"]`, sem repetir."""
    saida: list[str] = []
    for parte in (sku or "").split(","):
        t = parte.strip().lower()
        if t and t not in saida:
            saida.append(t)
    return saida


def dono_da_pasta(pasta: str | None) -> str | None:
    """O site em cuja raiz a pasta mora (sem diferenciar maiúsculas), ou None.

    Diferente de `pasta_do_site` da mídia, aqui não importa se a pasta é
    "boa para cliente" (`_algo`, "referencia"): a pergunta é só de quem é.
    """
    bruto = (pasta or "").strip()
    if not bruto.strip("/"):
        return None
    p = posixpath.normpath("/" + bruto.lstrip("/")).lower()
    for site, raizes in RAIZES.items():
        for raiz in raizes:
            r = raiz.lower()
            if p == r or p.startswith(r + "/"):
                return site
    return None


def _custo(valor: Decimal | None) -> float | None:
    if valor is None or valor <= 0:
        return None
    return float(valor)


def recortar(site: str, linhas: Iterable[Any]) -> list[dict[str, Any]]:
    """As linhas (sku, name, cost_kit1, updated_at, fotos_path) que o site pode ver.

    LISTA BRANCA: coluna nova em `pricing_products` não sai por aqui sozinha.
    """
    escopo = re.compile(ESCOPOS[site])
    itens: list[dict[str, Any]] = []
    for r in linhas:
        dono = dono_da_pasta(r.fotos_path)
        if dono is not None and dono != site:
            continue
        skus = [t for t in tokens(r.sku) if escopo.match(t)]
        if not skus:
            continue
        itens.append(
            {
                "skus": skus,
                "nome": r.name,
                "custo_kit1": _custo(r.cost_kit1),
                "atualizado_em": _iso(r.updated_at),
            }
        )
    itens.sort(key=lambda i: (i["skus"], i["nome"]))
    return itens


@router.get("/custos")
async def custos(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    site: Annotated[str, Depends(site_do_token)],
) -> dict[str, Any]:
    """Custo Kit 1 das linhas ativas da Tabela de Preços do recorte do site."""
    inicio = time.perf_counter()
    await _limite(site)

    apple = select(Segment.id).where(func.lower(Segment.name) == "apple").scalar_subquery()
    # Sem filtro de `user_id`: as linhas da Tabela têm donos diferentes.
    linhas = (
        await session.execute(
            select(
                PricingProduct.sku,
                PricingProduct.name,
                PricingProduct.cost_kit1,
                PricingProduct.updated_at,
                PricingProduct.fotos_path,
            )
            .where(
                PricingProduct.is_active.is_(True),
                PricingProduct.segment_id.not_in(apple),
            )
            .order_by(PricingProduct.sku, PricingProduct.id)
        )
    ).all()
    itens = recortar(site, linhas)

    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization"
    # Nunca o token, nunca um custo: só quem chamou, de onde, quanto levou.
    logger.info(
        "sites_custos",
        site=site,
        ip=_client_ip(request),
        total=len(itens),
        sem_custo=sum(1 for i in itens if i["custo_kit1"] is None),
        ms=round((time.perf_counter() - inicio) * 1000),
    )
    return {
        "site": site,
        "gerado_em": _iso(datetime.now(UTC)),
        "total": len(itens),
        "itens": itens,
    }
