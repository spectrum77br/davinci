"""Cadastros › Produtos (M-29): nomes, ordem alfabética e o uso na base do ERP.

Quantos produtos usam uma categoria ou uma tag de envio vem da base do ERP (o SQLite do serviço
`erp`), que o servidor principal não abre. Ele pergunta ao próprio serviço, pela rede interna
(davinci_net, `settings.erp_interno_url`), na rota só de leitura GET /api/erp-local/catalog/usage.
O ERP confia nos cabeçalhos X-ERP-Actor/X-ERP-Role só porque o Caddy os apaga do navegador e só a
API os escreve; aqui a API se apresenta como uma pessoa comum (papel "user"), que só lê.

Sem resposta do ERP o uso é `None`: quem chama decide (categoria: recusa; tag: pede confirmação).
"""

from __future__ import annotations

import unicodedata

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()

# Cabeçalho HTTP: só ASCII.
ATOR_ERP = "davinci-api/cadastro-produtos"
TIMEOUT_ERP = 8.0


def normalizar(nome: str) -> str:
    """Sem espaços nas pontas e um espaço só entre as palavras."""
    return " ".join((nome or "").split())


def igual(a: str, b: str) -> bool:
    """Mesmo nome sem olhar maiúsculas — a mesma regra do índice único (lower(nome))."""
    return normalizar(a).casefold() == normalizar(b).casefold()


def ordem(nome: str) -> tuple[str, str]:
    """Ordem alfabética de gente: sem acento e sem maiúsculas ("Acessórios" antes de "Celular")."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", nome) if not unicodedata.combining(c)
    )
    return (sem_acento.casefold(), nome)


async def uso_no_erp(tags: list[str] | None = None) -> dict | None:
    """{"products": N, "categories": {nome: n}, "without_category": n, "tags": {tag: n}} ou None.

    Conta os produtos não excluídos da base do ERP, como a lista do Produtos 2 mostra.
    """
    url = get_settings().erp_interno_url.rstrip("/") + "/api/erp-local/catalog/usage"
    params = [("tag", t) for t in tags or []]
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_ERP) as client:
            r = await client.get(
                url,
                params=params,
                headers={"X-ERP-Actor": ATOR_ERP, "X-ERP-Role": "user"},
            )
        if r.status_code != 200:
            logger.warning("produto_cadastros.erp_resposta", status=r.status_code)
            return None
        dados = r.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("produto_cadastros.erp_fora", erro=type(e).__name__)
        return None
    if not isinstance(dados, dict) or not isinstance(dados.get("categories"), dict):
        return None
    return dados


def produtos_na_categoria(uso: dict, nome: str) -> int:
    """Produtos cuja categoria no ERP tem este nome (sem olhar maiúsculas)."""
    return sum(int(n) for c, n in (uso.get("categories") or {}).items() if igual(c, nome))


def produtos_com_tag(uso: dict, tag: str) -> int:
    for t, n in (uso.get("tags") or {}).items():
        if igual(t, tag):
            return int(n)
    return 0
