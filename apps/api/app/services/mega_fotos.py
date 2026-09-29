"""Fotos de produtos via MEGA (sidecar MEGAcmd).

Cliente HTTP do sidecar (serviço `megacmd` no compose) + matching por nome
entre as pastas de fotos da conta MEGA e os produtos da Tabela de Preços.

Matching em dois passes sobre nomes normalizados (minúsculas, sem acento,
pontuação → espaço):
  1. igualdade exata;
  2. continência com fronteira de palavra ("redmi 13" NÃO casa "redmi 13c").
     Só aplica quando sobra exatamente UMA pasta candidata — ambíguos vão
     pro relatório em vez de chutar. Uma mesma pasta PODE atender vários
     produtos (variações 4/128 vs 8/256 dividem as mesmas fotos).
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import get_settings


class MegaError(Exception):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _headers() -> dict[str, str]:
    token = get_settings().mega_sidecar_token
    return {"X-Sidecar-Token": token} if token else {}


async def sidecar_request(
    method: str,
    path: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    files: list[tuple[str, tuple[str, Any, str]]] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(
            base_url=settings.mega_sidecar_url,
            headers=_headers(),
            timeout=httpx.Timeout(timeout, connect=10.0),
        ) as client:
            resp = await client.request(
                method, path, json=json, params=params, data=data, files=files
            )
    except httpx.HTTPError as exc:
        raise MegaError(f"sidecar MEGA inacessível: {exc}", 503) from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail")
        except Exception:
            detail = resp.text[:400]
        raise MegaError(str(detail or f"sidecar HTTP {resp.status_code}"))
    return resp.json()


async def sidecar_bytes(path: str, *, params: dict[str, Any], timeout: float = 600.0) -> bytes:
    """Como `sidecar_request`, mas devolve os BYTES.

    Existe porque a foto do produto não é JSON: ela desce do MEGA pelo sidecar
    e atravessa o DaVinci até o portal. Link público do MEGA resolveria sem
    código nenhum — e publicaria a pasta inteira da linha, sem revogação e com
    o material do fornecedor junto.
    """
    settings = get_settings()
    try:
        async with httpx.AsyncClient(
            base_url=settings.mega_sidecar_url,
            headers=_headers(),
            timeout=httpx.Timeout(timeout, connect=10.0),
        ) as client:
            resp = await client.get(path, params=params)
    except httpx.HTTPError as exc:
        raise MegaError(f"sidecar MEGA inacessível: {exc}", 503) from exc
    if resp.status_code >= 400:
        raise MegaError(f"sidecar HTTP {resp.status_code}", resp.status_code)
    return resp.content


async def sidecar_stream(
    path: str, *, params: dict[str, Any], timeout: float = 600.0
) -> tuple[AsyncIterator[bytes], Any]:
    """Como `sidecar_bytes`, mas SEM carregar o arquivo na memória da API.

    Existe por causa das embalagens: arte de impressão (.ai, .psd, .zip) passa
    fácil de 100 MB, e `resp.content` seguraria o arquivo inteiro na API a
    cada download. Devolve (pedaços, fechar): quem chama entrega os pedaços
    num StreamingResponse e roda `fechar()` no fim (BackgroundTask). O status
    é conferido ANTES do primeiro byte — erro do sidecar vira MegaError com o
    status dele (404 = arquivo não achado), não uma resposta cortada.
    """
    settings = get_settings()
    client = httpx.AsyncClient(
        base_url=settings.mega_sidecar_url,
        headers=_headers(),
        timeout=httpx.Timeout(timeout, connect=10.0),
    )
    try:
        resp = await client.send(client.build_request("GET", path, params=params), stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise MegaError(f"sidecar MEGA inacessível: {exc}", 503) from exc
    if resp.status_code >= 400:
        await resp.aclose()
        await client.aclose()
        raise MegaError(f"sidecar HTTP {resp.status_code}", resp.status_code)

    async def fechar() -> None:
        await resp.aclose()
        await client.aclose()

    return resp.aiter_bytes(), fechar


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return _NON_ALNUM_RE.sub(" ", s).strip()


# A subpasta de embalagens dentro da pasta de fotos do produto (Eduardo,
# 29/09/2026). O sidecar (infra/megacmd/app.py, `_classificar`) usa a MESMA
# regra para contar e listar; os dois lados precisam concordar, senão o portal
# recusaria um arquivo que a listagem mostrou (ou o contrário).
NOMES_PASTA_EMBALAGEM = frozenset({"embalagens", "embalagem"})
PASTA_EMBALAGEM_PADRAO = "Embalagens"


def eh_de_embalagens(nome_relativo: str) -> bool:
    """O 1º segmento do caminho RELATIVO à pasta do produto é a subpasta de
    embalagens? ("Embalagens/caixa.pdf", "embalagem/x.jpg", "EMBALAGÉNS/y").

    Trecho vazio e "." são pulados: o MEGAcmd resolve "./Embalagens/x.jpg"
    e "/Embalagens//x.jpg" para a mesma subpasta, e olhar só o 1º trecho
    literal ("." vira "" no norm_name) deixava a caixa passar. As rotas já
    recusam esses nomes antes; isto é a segunda trava."""
    segs = [s for s in (nome_relativo or "").split("/") if s.strip() not in ("", ".")]
    return bool(segs) and norm_name(segs[0]) in NOMES_PASTA_EMBALAGEM


def match_products_to_folders(
    products: list[Any], folders: list[dict[str, Any]]
) -> dict[str, Any]:
    """products: linhas PricingProduct; folders: itens do sidecar /folders.

    Retorna dict com matches [(product, folder)], ambiguous, unmatched.
    """
    folder_items = [f for f in folders if f.get("is_folder", True)]
    by_norm: dict[str, list[dict[str, Any]]] = {}
    for f in folder_items:
        by_norm.setdefault(norm_name(f["name"]), []).append(f)

    matches: list[tuple[Any, dict[str, Any]]] = []
    ambiguous: list[dict[str, Any]] = []
    unmatched_products: list[Any] = []
    matched_folder_paths: set[str] = set()

    for p in products:
        p_norm = norm_name(p.name or "")
        if not p_norm:
            unmatched_products.append(p)
            continue
        exact = by_norm.get(p_norm) or []
        if len(exact) == 1:
            matches.append((p, exact[0]))
            matched_folder_paths.add(exact[0]["path"])
            continue
        if len(exact) > 1:
            ambiguous.append(
                {"product": p, "candidates": [f["name"] for f in exact]}
            )
            continue
        # Passe 2: continência com fronteira de palavra, nas duas direções.
        p_pad = f" {p_norm} "
        candidates = [
            f
            for f_norm, fs in by_norm.items()
            if f_norm and (f" {f_norm} " in p_pad or p_pad in f" {f_norm} ")
            for f in fs
        ]
        if len(candidates) == 1:
            matches.append((p, candidates[0]))
            matched_folder_paths.add(candidates[0]["path"])
        elif len(candidates) > 1:
            ambiguous.append(
                {"product": p, "candidates": [f["name"] for f in candidates]}
            )
        else:
            unmatched_products.append(p)

    unmatched_folders = [
        f for f in folder_items if f["path"] not in matched_folder_paths
    ]
    return {
        "matches": matches,
        "ambiguous": ambiguous,
        "unmatched_products": unmatched_products,
        "unmatched_folders": unmatched_folders,
        "folders_total": len(folder_items),
    }
