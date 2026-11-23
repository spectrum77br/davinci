"""A ordem das categorias em tudo o que o servidor escreve para alguém ler.

M-23 (23/11/2026, prévia aprovada pelo dono): em toda tela e relatório as
categorias aparecem em ordem alfabética — Celular, Eletro, Mala. O que não é
categoria (o "Geral" da Conferência, Catálogo, Shein, "Sem categoria"…) vem
depois, na ordem em que já estava.

É só ordem de EXIBIÇÃO: não muda dado, cálculo, permissão nem o que fica
guardado. O relatório congelado da Conferência, por exemplo, continua com os
grupos na ordem em que foi calculado (o JSON guardado não muda); quem escreve
o Excel, o HTML, o CSV, o Markdown e o aviso do Threema é que ordena. O
`segments.sort_order` também fica como está (é a ordem dos tipos de cada
categoria, as colunas/slots da Tabela de Preços).

A tela tem a mesma lista: apps/web/lib/categorias.ts. Mudou aqui, muda lá
(apps/web/tests/categorias-ordem.cjs confere que as duas batem).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

ORDEM_CATEGORIAS: tuple[str, ...] = ("celular", "eletro", "mala")


def posicao_categoria(chave: object) -> int:
    """0 = Celular, 1 = Eletro, 2 = Mala; o que não é categoria vem depois."""
    texto = str(chave if chave is not None else "").strip().lower()
    return ORDEM_CATEGORIAS.index(texto) if texto in ORDEM_CATEGORIAS else len(ORDEM_CATEGORIAS)


def ordenar_categorias[T](
    itens: Iterable[T], chave: Callable[[T], object] = lambda item: item
) -> list[T]:
    """Uma lista nova com as categorias em Celular, Eletro, Mala e o resto
    depois, na ordem em que veio (`sorted` é estável). `chave` diz onde está a
    categoria de cada item (a chave do grupo, o slug…); sem ela, o próprio item."""
    return sorted(itens, key=lambda item: posicao_categoria(chave(item)))
