"""Peças compartilhadas pelos enriquecedores de logística (ML/Shopee/TikTok/Amazon).

O botão "recarregar" varre TODAS as linhas das quatro plataformas. Sequencial
isso levava horas (a Shopee sozinha gasta ~2,9s por linha: três chamadas em
sequência). Só que o custo é de REDE, não de banco — dá pra sobrepor várias
linhas ao mesmo tempo, desde que NADA toque a `AsyncSession` durante a rajada
(uma sessão async não aceita operações concorrentes). Daí as três peças aqui:

- `prewarm_clients`: resolve a integração de cada conta ANTES da rajada, então
  `enrich_row` só lê o cache e nunca consulta o banco no meio dela.
- `chunked` + `CONCURRENCY`: o tamanho da rajada.
- `NOLOCK`: o único ponto que ainda escreve no banco durante a rajada é o
  `on_token_refresh` (flush das credenciais novas — o refresh token do ML gira,
  perder o novo quebra a integração). Os builders de client aceitam um
  `asyncio.Lock` pra serializar esse flush; fora da rajada não há lock.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable, Iterable, Iterator, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, Logistica

# Linhas enriquecidas em paralelo. 6 corta a varredura completa de horas pra
# dezenas de minutos sem chegar perto do rate limit dos marketplaces (a Amazon,
# que é a mais restrita, usa um valor menor).
CONCURRENCY = 6

NOLOCK = contextlib.nullcontext()


def chunked[T](seq: Sequence[T], size: int = CONCURRENCY) -> Iterator[Sequence[T]]:
    """Fatia `seq` em pedaços de até `size` itens, preservando a ordem."""
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


async def prewarm_clients(
    session: AsyncSession,
    rows: Iterable[Logistica],
    cache: dict[Any, Any],
    *,
    resolve: Callable[[AsyncSession, str | None], Awaitable[Integration | None]],
    build: Callable[[AsyncSession, Integration], Any],
) -> None:
    """Preenche `cache` com um client por conta distinta das `rows`.

    Roda ANTES da rajada concorrente justamente porque `resolve` consulta o
    banco. Conta sem integração fica FORA do cache de propósito: quem chama
    descarta essas linhas (contam como `skipped`) em vez de deixar o
    `enrich_row` ir ao banco no meio da rajada.
    """
    for conta in dict.fromkeys(r.conta for r in rows):
        if conta in cache:
            continue
        integ = await resolve(session, conta)
        if integ is not None:
            cache[conta] = build(session, integ)
