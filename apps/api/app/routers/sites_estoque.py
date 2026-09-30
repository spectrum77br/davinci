"""Estoque para os sites Charlots e Uranyx — só leitura (Marco, 30/09/2026).

"pegar o estoque correspondente do item para pessoas logadas" — nos dois
sites. Quem autentica PESSOA é o site (PHP, na Hostinger): ele guarda uma
cópia local e só mostra o número para cliente logado. O que atravessa a
internet é uma chamada servidor-a-servidor, com o token no header
`Authorization: Bearer` — o Caddy redige esse header no log de acesso; um
header próprio (como o `X-Portal-Token`) iria pro log em claro. O navegador
do cliente nunca vê o token.

## O que sai daqui

Por SKU ativo do recorte do site: `sku`, `nome`, `estoque` e `kit`. Nada de
id interno, preço, custo, conta de marketplace ou link.

- `estoque` é `products.stock` — a coluna "Bling" da tela Produtos, que o
  webhook e o `_refresh_bling` mantêm quase em tempo real. Cortado em 0: o
  webhook grava o valor cru e o Bling às vezes manda -1. NÃO é
  `saldo_fisico`/`saldo_virtual_total` (foto velha do botão manual), nem
  `full_stock`, nem `product_links.stock` (cópias por anúncio, atrasadas).
- `kit` = formato E no Bling, ou SKU com `+` (mala com acessório). O saldo
  do kit já vem calculado pelo Bling; o site não soma componente.
- `ultima_releitura_bling_em` diz ao site se o DaVinci ainda está conversando
  com o Bling: é o `refresh_bling` ok mais recente das últimas 48 h, ou null.

## As travas

1. **Fecha por padrão.** `SITES_ESTOQUE_TOKENS` vazio, header ausente,
   esquema que não é Bearer ou token diferente → 401, antes de qualquer
   trabalho. Parser e comparação são os do `portal_criativos` (compare_digest
   em bytes contra CADA token).
2. **O recorte vem do token, nunca do request.** O token diz o site; o site
   diz a regex em `ESCOPOS`, fixa no código. Site sem recorte → 401. O `sku`
   do request só estreita DENTRO do recorte: a Charlots não lê celular, a
   Uranyx não lê mala, e ninguém lê `z*` (salvado de devolução).
3. **Sem filtro de `user_id`.** Os produtos têm três donos: ~290 dos SKUs
   ativos da Uranyx (quase todos kits `dg0xx.pi+…`) são de um admin, e
   filtrar pelo dono operacional sumiria com eles. Dedupe por `lower(sku)`
   entre os ativos.
4. **Lista branca na saída.** Campo novo no modelo não vaza sozinho.
5. **Fora do openapi** (ele é público) e com limite de 30 chamadas/min por
   site. Redis fora do ar não vira 500: o limite falha aberto, com log.
"""

from __future__ import annotations

import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.enums import IntegrationPlatform, LinkSyncStatus, SyncLogAction
from app.models.product import Product
from app.models.sync_log import SyncLog
from app.routers.auth import _client_ip
from app.services.rate_limit import RateLimitError, sliding_window_check

logger = structlog.get_logger()

# `include_in_schema=False` no router inteiro: o /api/openapi.json é público
# e não precisa anunciar que esta porta existe.
router = APIRouter(prefix="/api/sites", tags=["sites"], include_in_schema=False)

# O recorte de cada site, sobre `lower(sku)`. Fixo no código de propósito:
# nada do request entra aqui.
#   charlots: malas `b<dígito>`, mochilas `bp<dígito>` e acessórios `a0NN`.
#   uranyx:   eletro `u*`, celulares `dg<dígito>` e acessórios `a0NN`.
# `z*` (peça salvada de devolução, ~1 unidade com o nome da mala original)
# não casa com nenhum dos dois.
ESCOPOS: dict[str, str] = {
    "charlots": r"^(b[0-9]|bp[0-9]|a0[0-9]{2})",
    "uranyx": r"^(u|dg[0-9]|a0[0-9]{2})",
}

# O que o site pode pedir em `sku`. Um SKU do Bling cabe folgado (o maior
# ativo tem 33 caracteres, kits com `+` incluídos). `fullmatch`, e não `$`:
# em Python o `$` aceita um "\n" no fim.
_RX_SKU = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,99}")
MAX_SKUS_POR_CONSULTA = 200

LIMITE_CHAMADAS_MIN = 30
# Janela do "o DaVinci ainda relê o Bling?". O sync_all diário garante pelo
# menos uma leva por dia; 48 h dá folga para um dia que falhou.
JANELA_RELEITURA = timedelta(hours=48)


def _nao_autorizado() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "sites_nao_autorizado"})


def _mapa_tokens() -> dict[str, str]:
    """`"tok:site,tok:site"` → {token: site}. Linha torta é ignorada."""
    bruto = get_settings().sites_estoque_tokens or ""
    mapa: dict[str, str] = {}
    for parte in bruto.split(","):
        token, _, site = parte.partition(":")
        token, site = token.strip(), site.strip().lower()
        if token and site:
            mapa[token] = site
    return mapa


async def site_do_token(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Devolve o NOME do site dono do token. Nunca devolve usuário, nunca abre sem token."""
    mapa = _mapa_tokens()
    esquema, _, enviado = (authorization or "").strip().partition(" ")
    enviado = enviado.strip()
    if not mapa or esquema.lower() != "bearer" or not enviado:
        raise _nao_autorizado()
    # compare_digest em bytes contra CADA token cadastrado: `in` responderia
    # em tempo variável, e str com acento levantaria TypeError (500 em vez
    # de 401) — os mesmos dois motivos do `equipe_do_token` do portal.
    bruto = enviado.encode("utf-8", "surrogatepass")
    for token, site in mapa.items():
        if secrets.compare_digest(bruto, token.encode("utf-8", "surrogatepass")):
            if site not in ESCOPOS:
                # Token válido para um site sem recorte = porta sem parede.
                raise _nao_autorizado()
            return site
    raise _nao_autorizado()


def _skus_pedidos(sku: list[str] | None) -> set[str]:
    """Os SKUs do filtro opcional, já em minúsculas. Inválido/excesso → 422."""
    if not sku:
        return set()
    if len(sku) > MAX_SKUS_POR_CONSULTA:
        raise HTTPException(422, detail={"code": "sites_skus_demais", "max": MAX_SKUS_POR_CONSULTA})
    pedidos: set[str] = set()
    for s in sku:
        if not _RX_SKU.fullmatch(s):
            raise HTTPException(422, detail={"code": "sites_sku_invalido"})
        pedidos.add(s.lower())
    return pedidos


async def _limite(site: str) -> None:
    try:
        await sliding_window_check(
            key=f"sites_estoque:rl:{site}",
            limit=LIMITE_CHAMADAS_MIN,
            window_seconds=60,
        )
    except RateLimitError as e:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "sites_limite"},
            headers={"Retry-After": str(e.retry_after)},
        ) from None
    except Exception as e:  # noqa: BLE001 — Redis fora do ar não derruba o estoque do site
        logger.warning("sites_estoque_rate_limit_indisponivel", site=site, err=str(e)[:120])


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec="seconds")


@router.get("/estoque")
async def estoque(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    site: Annotated[str, Depends(site_do_token)],
    sku: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    """Estoque dos SKUs ativos do recorte do site, ordenado por `lower(sku)`."""
    inicio = time.perf_counter()
    await _limite(site)
    pedidos = _skus_pedidos(sku)

    chave = func.lower(Product.sku)
    consulta = (
        select(
            Product.sku,
            Product.name,
            func.greatest(Product.stock, 0).label("estoque"),
            Product.formato,
        )
        .where(Product.situacao == "A", chave.op("~")(ESCOPOS[site]))
        # Mesmo SKU com dono diferente: fica uma linha só, sempre a mesma.
        .distinct(chave)
        .order_by(chave, Product.updated_at.desc(), Product.id)
    )
    if pedidos:
        # Busca exata, sem diferenciar maiúsculas — e AINDA dentro do recorte.
        consulta = consulta.where(chave.in_(pedidos))
    linhas = (await session.execute(consulta)).all()

    ultima_releitura = await session.scalar(
        select(func.max(SyncLog.created_at)).where(
            SyncLog.platform == IntegrationPlatform.BLING,
            SyncLog.action == SyncLogAction.REFRESH_BLING,
            SyncLog.status == LinkSyncStatus.OK,
            SyncLog.created_at > datetime.now(UTC) - JANELA_RELEITURA,
        )
    )

    # LISTA BRANCA. Coluna nova em `products` não sai por aqui sozinha.
    itens = [
        {
            "sku": r.sku,
            "nome": r.name,
            "estoque": int(r.estoque or 0),
            "kit": r.formato == "E" or "+" in r.sku,
        }
        for r in linhas
    ]

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["Vary"] = "Authorization"
    # Nunca o token: só quem chamou, de onde, quanto levou.
    logger.info(
        "sites_estoque",
        site=site,
        ip=_client_ip(request),
        total=len(itens),
        filtro=len(pedidos),
        ms=round((time.perf_counter() - inicio) * 1000),
    )
    return {
        "site": site,
        "gerado_em": _iso(datetime.now(UTC)),
        "ultima_releitura_bling_em": _iso(ultima_releitura),
        "total": len(itens),
        "itens": itens,
    }
