"""Tag de envio do SKU pela LISTA de Cadastros › Produtos (M-29, migration 0508).

A tag é o fim do código: `dg019.ra` tem a tag `.ra` e a numeração `dg019`; `z2819` não tem tag
(numeração `z2819`). Mesma regra do filtro Tags do Produtos 2 (product-list.js,
`productFilterKeys`): a tag da lista em que o SKU (minúsculo) termina, ou nenhuma.

Usado pelo "Transferir" entre lotes do mesmo produto (M-44: mesma numeração, tag diferente) e pelo
"Trocar a tag" em massa (M-46: numeração + tag nova). Diferente de `services/sku_tags.py`, que é a
classificação do Controle de Estoque (com exceções por SKU, mala/eletro por prefixo etc.).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.produto_cadastro import CHAVE_TAG_ENVIO, ProdutoCadastro, ProdutoCadastroTipo


async def tags_de_envio(session: AsyncSession) -> list[str]:
    """As tags da lista "Tag de envio" (minúsculas, sem espaços), a mais longa primeiro."""
    nomes = (
        (
            await session.execute(
                select(ProdutoCadastro.nome)
                .join(ProdutoCadastroTipo, ProdutoCadastroTipo.id == ProdutoCadastro.tipo_id)
                .where(ProdutoCadastroTipo.chave == CHAVE_TAG_ENVIO)
            )
        )
        .scalars()
        .all()
    )
    tags = {(n or "").strip().lower() for n in nomes}
    return sorted((t for t in tags if t), key=lambda t: (-len(t), t))


def tag_do_sku(sku: str | None, tags: list[str]) -> str | None:
    """A tag da lista em que o SKU termina (a mais longa, se mais de uma casar), ou None. Sobra
    sempre alguma numeração: um SKU igual à tag não "tem" a tag."""
    s = (sku or "").strip().lower()
    for t in sorted(tags, key=len, reverse=True):
        if t and len(s) > len(t) and s.endswith(t):
            return t
    return None


def numeracao_do_sku(sku: str | None, tags: list[str]) -> str:
    """O SKU (minúsculo) sem a tag do fim: `dg019.ra` → `dg019`; `z2819` → `z2819`."""
    s = (sku or "").strip().lower()
    t = tag_do_sku(s, tags)
    return s[: -len(t)] if t else s


def codigo_com_tag(sku: str, tag: str, tags: list[str]) -> str:
    """O código com a `tag` no lugar da atual, mantendo a grafia da numeração:
    `z2819` + `.sa` → `z2819.sa`; `dg050.ra` + `.sp` → `dg050.sp`."""
    original = (sku or "").strip()
    atual = tag_do_sku(original, tags)
    numeracao = original[: -len(atual)] if atual else original
    return numeracao + tag.strip().lower()
