"""Fotos e vídeos do produto para os sites Charlots e Uranyx (Marco, 01/10/2026).

"para pessoas logadas já confirmadas o cadastro no admin também ver conteúdos
da mala [...] acessa conteúdos do davinci de imagens e vídeos do produto" — e
"em cada imagem e vídeo [...] bote a logo [...] como marca d'água". Quem decide
QUEM vê é o site (lojista aprovado no painel dele). Aqui moram as três coisas
que só o DaVinci sabe fazer:

1. **SKU → pasta.** O site manda os SKUs do produto (os mesmos do estoque) e
   nunca um caminho do MEGA. A pasta sai de `pricing_products.fotos_path` pela
   igualdade EXATA de um token da lista `sku` da Tabela de Preços, e só vale
   pasta dentro da raiz do site (`/Malas` na Charlots; `/Celular` e `/uranyx`
   na Uranyx), fora o segmento Apple (revenda, não é marca da casa).
2. **O que entra.** A listagem do sidecar (`/files`) passa por mais travas:
   embalagem, pasta que começa com `_` ("renomeie para `_algo` e some do
   site"), referência de concorrente, "não usar", interno, avariada, nome que
   o `mega-get` trataria como curinga, extensão que não é foto nem vídeo, e
   duplicata NFC/NFD do mesmo nome.
3. **O link.** Os bytes vão do DaVinci direto ao navegador por um link com
   validade (3 a 6 h, em janelas de 3 h para a URL ficar estável e o navegador
   reaproveitar o cache). O link é CIFRADO (AES-GCM), não só assinado: o nome
   do arquivo e da pasta ("画板 21.jpg", "hf_2026…", "referencia/") nunca chega
   à página, nem em base64. A chave é por site, derivada do `jwt_secret` (só o
   DaVinci emite) E do token atual do site — trocar o token em
   `SITES_ESTOQUE_TOKENS` derruba todos os links já emitidos.

A marca d'água e o cache das derivadas ficam em `sites_midia_derivados`.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

import structlog
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import PricingProduct, Segment
from app.routers.pricing_mega import _nome_invalido
from app.routers.sites_estoque import ESCOPOS, _mapa_tokens
from app.services.mega_fotos import MegaError, eh_de_embalagens, norm_name, sidecar_request
from app.services.mega_midias import EXT_IMAGEM, EXT_VIDEO, extensao

logger = structlog.get_logger()

# ─────────────── recorte por site ───────────────

# A pasta diz de quem é o conteúdo: o acessório `a003` mora em /Celular/fone e
# o `a006` em /Malas/…, e os dois sites podem pedir `a0NN` (ESCOPOS). Sem o
# filtro de raiz, a Charlots receberia o fone da Uranyx.
RAIZES: dict[str, tuple[str, ...]] = {
    "charlots": ("/Malas",),
    "uranyx": ("/Celular", "/uranyx"),
}

# Pasta (ou subpasta) com qualquer um destes trechos no nome normalizado fica
# de fora. "referencia" e "concorren" são as 12 fotos de concorrente da air
# fryer (Ninja, eBay); o resto é o que a operação usa para guardar material
# que não é para cliente.
_TRECHOS_PROIBIDOS = ("referencia", "concorren", "nao usar", "interno", "avariad")

# Lote do Bling no fim do SKU: sem pasta para `dg053.cd`, vale a de `dg053`.
_RX_LOTE = re.compile(r"\.(ci|pi|ra|sa|sp|cd|us)$")

MAX_PASTAS = 12
MAX_ITENS = 400

# Cache em memória. ~112 linhas da Tabela; a listagem do sidecar leva 5-23 ms
# por pasta, mas uma grade de produto pede de 1 a 12 pastas × 2 tipos.
_TTL_S = 600
_linhas_cache: tuple[float, list[tuple[str, tuple[str, ...]]]] | None = None
_arquivos_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}


def limpar_caches() -> None:
    """Esvazia os caches em memória (testes, e quem quiser forçar releitura)."""
    global _linhas_cache
    _linhas_cache = None
    _arquivos_cache.clear()


def _pasta_abs(pasta: str) -> str:
    """Caminho absoluto sem barra no fim. NÃO tira espaço: o MEGA tem pasta com
    espaço no fim do nome ("b006 M1 listrada verde claro  ")."""
    return "/" + (pasta or "").strip("/")


def _segmento_proibido(seg: str) -> bool:
    if seg.strip().startswith("_"):
        return True
    n = norm_name(seg)
    return any(t in n for t in _TRECHOS_PROIBIDOS)


def pasta_do_site(site: str, pasta: str) -> bool:
    """A pasta está dentro de uma raiz do site, e nenhum trecho dela é proibido?"""
    if not pasta or not pasta.strip("/").strip():
        return False
    p = _pasta_abs(pasta)
    # "/Malas/../Celular" passaria no prefixo: trecho "."/".." ou byte de
    # controle nunca vale (nenhuma das 82 pastas da Tabela tem, 01/10/2026).
    segs = p.split("/")[1:]
    if any(s.strip() in (".", "..") for s in segs) or any(ord(c) < 32 for c in p):
        return False
    if not any(p == r or p.startswith(r + "/") for r in RAIZES.get(site, ())):
        return False
    return not any(_segmento_proibido(s) for s in segs)


def excluido(nome: str) -> bool:
    """O arquivo (nome RELATIVO à pasta do produto) fica fora do site?"""
    if _nome_invalido(nome) or eh_de_embalagens(nome):
        return True
    if any(_segmento_proibido(s) for s in nome.split("/")[:-1]):
        return True
    ext = extensao(nome)
    return ext not in EXT_IMAGEM and ext not in EXT_VIDEO


def eh_video(nome: str) -> bool:
    return extensao(nome) in EXT_VIDEO


def caminho_completo(pasta: str, nome: str) -> str:
    return _pasta_abs(pasta) + "/" + nome


def item_id(site: str, caminho: str) -> str:
    # sha1 só como identificador opaco e estável (não é segurança).
    return hashlib.sha1(  # noqa: S324
        f"{site}\0{caminho}".encode("utf-8", "surrogatepass"), usedforsecurity=False
    ).hexdigest()[:16]


# ─────────────── códigos e cores (para o site filtrar por cor) ───────────────

# A base da cor da mala no caminho: "b005 M1 listrada preto/b005.20.jpg",
# "20B025.jpg", "14.20.b057.jpg", "B045.12.jpg" → b005/b025/b057/b045.
# Validada nas 629 fotos de mala da Tabela (01/10/2026).
_RX_CODIGO = re.compile(r"(?i)(?:^|[^a-z0-9])(?:\d{1,2})?(bp?)(\d{3})(?!\d)")
# Só onde o SKU do site tem base `bNNN`: na Uranyx os SKUs são `dg…`/`u…`/`a0…`
# e os nomes de arquivo são livres — um uuid de vídeo gerado
# ("hf_…-4520-b512-…") daria um "b512" fantasma, e o item sumiria ao filtrar
# por cor.
SITES_COM_CODIGO = frozenset({"charlots"})

# Dicionário compartilhado com os sites (SPEC-midia §2.4). Forma canônica no
# masculino, sem acento.
_CORES: dict[str, tuple[str, ...]] = {
    "preto": ("preto", "preta", "black"),
    "branco": ("branco", "branca", "white"),
    "azul": ("azul", "blue"),
    "verde": ("verde", "green"),
    "vermelho": ("vermelho", "vermelha", "red"),
    "laranja": ("laranja", "orange"),
    "cinza": ("cinza", "gray", "grey"),
    "prata": ("prata", "prateado", "prateada", "silver"),
    "dourado": ("dourado", "dourada", "gold", "golden"),
    "rosa": ("rosa", "pink"),
    "roxo": ("roxo", "roxa", "purple"),
    "amarelo": ("amarelo", "amarela", "yellow"),
    "bege": ("bege", "beige"),
    "marrom": ("marrom", "brown"),
}
_PALAVRA_PARA_COR = {p: canon for canon, palavras in _CORES.items() for p in palavras}
# "white background images/" é a pasta do fornecedor com fundo branco: não é cor.
_RX_SEM_COR = re.compile(r"\bwhite background\b")


def codigos(site: str, nome: str) -> list[str]:
    if site not in SITES_COM_CODIGO:
        return []
    vistos: list[str] = []
    for m in _RX_CODIGO.finditer(nome):
        c = (m.group(1) + m.group(2)).lower()
        if c not in vistos:
            vistos.append(c)
    return vistos


def cores(nome: str) -> list[str]:
    texto = _RX_SEM_COR.sub(" ", norm_name(nome))
    achadas: list[str] = []
    for palavra in texto.split():
        canon = _PALAVRA_PARA_COR.get(palavra)
        if canon and canon not in achadas:
            achadas.append(canon)
    return achadas


def _ordem_natural(nome: str) -> list[Any]:
    # re.split com grupo alterna texto/número sempre nas mesmas posições, então
    # a comparação nunca mistura str com int.
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", nome)]


# ─────────────── SKU → pastas ───────────────


async def _linhas(session: AsyncSession) -> list[tuple[str, tuple[str, ...]]]:
    """(fotos_path, tokens do sku) de TODA a Tabela com pasta, fora Apple.

    Sem filtro de `user_id` (as linhas têm donos diferentes). Ordem estável:
    quem tem mais fotos primeiro — num token repetido em duas pastas, vale a
    que tem conteúdo —, depois o caminho.
    """
    global _linhas_cache
    agora = time.monotonic()
    if _linhas_cache and agora - _linhas_cache[0] < _TTL_S:
        return _linhas_cache[1]
    apple = select(Segment.id).where(func.lower(Segment.name) == "apple").scalar_subquery()
    linhas = (
        await session.execute(
            select(PricingProduct.fotos_path, PricingProduct.sku)
            .where(
                PricingProduct.fotos_path.is_not(None),
                PricingProduct.fotos_path != "",
                PricingProduct.segment_id.not_in(apple),
            )
            .order_by(
                PricingProduct.fotos_count.desc().nulls_last(),
                PricingProduct.fotos_path,
                PricingProduct.id,
            )
        )
    ).all()
    saida = [
        (r.fotos_path, tuple(t.strip().lower() for t in (r.sku or "").split(",") if t.strip()))
        for r in linhas
    ]
    _linhas_cache = (agora, saida)
    return saida


async def pastas_do_site(session: AsyncSession, site: str, skus: list[str] | None) -> list[str]:
    """As pastas do MEGA para os SKUs pedidos, na ordem do pedido (máx. 12).

    `skus=None` devolve TODAS as pastas do site (o aquecimento). Quem chama
    fecha a sessão antes de falar com o sidecar.
    """
    linhas = [(p, toks) for p, toks in await _linhas(session) if pasta_do_site(site, p)]
    if skus is None:
        return sorted({p for p, _ in linhas})

    def _primeira(token: str) -> str | None:
        for pasta, toks in linhas:
            if token in toks:
                return pasta
        return None

    escopo = re.compile(ESCOPOS[site])
    pastas: list[str] = []
    for sku in skus:
        s = sku.lower()
        if not escopo.match(s):
            continue  # SKU de outro site não alarga nada
        pasta = _primeira(s)
        if pasta is None and _RX_LOTE.search(s):
            pasta = _primeira(_RX_LOTE.sub("", s))
        if pasta is not None and pasta not in pastas:
            pastas.append(pasta)
            if len(pastas) >= MAX_PASTAS:
                break
    return pastas


# ─────────────── arquivos de cada pasta ───────────────


@dataclass(frozen=True)
class Item:
    site: str
    pasta: str
    nome: str  # relativo à pasta, byte a byte como o sidecar mandou
    tipo: str  # "foto" | "video"

    @property
    def caminho(self) -> str:
        return caminho_completo(self.pasta, self.nome)

    @property
    def id(self) -> str:
        return item_id(self.site, self.caminho)


class ListagemIndisponivel(Exception):  # noqa: N818 — português, como `ErroCaixa`
    """Nenhuma pasta pedida pôde ser listada (sidecar fora, pastas renomeadas)."""


async def _arquivos_da_pasta(pasta: str, tipo: str) -> list[str] | None:
    """Nomes relativos da pasta num tipo (`imagens`/`videos`), ou None se falhou."""
    chave = (pasta, tipo)
    agora = time.monotonic()
    guardado = _arquivos_cache.get(chave)
    if guardado and agora - guardado[0] < _TTL_S:
        return guardado[1]
    try:
        resp = await sidecar_request(
            "GET", "/files", params={"path": pasta, "tipo": tipo}, timeout=60
        )
    except MegaError as exc:
        logger.warning("sites_midia_pasta_falhou", tipo=tipo, err=str(exc)[:200])
        return None
    if resp.get("rc", 0) != 0:
        # Quase sempre pasta renomeada no MEGA. O caminho não vai ao log: o
        # id basta para achar na Tabela.
        logger.warning(
            "sites_midia_pasta_falhou", tipo=tipo, rc=resp.get("rc"), pasta_id=item_id("", pasta)
        )
        return None
    nomes = [a.get("nome") for a in resp.get("arquivos") or [] if isinstance(a.get("nome"), str)]
    if len(_arquivos_cache) > 500:
        _arquivos_cache.clear()
    _arquivos_cache[chave] = (agora, nomes)
    return nomes


async def itens_das_pastas(
    site: str, pastas: list[str], limite: int | None = MAX_ITENS
) -> list[Item]:
    """Fotos e vídeos das pastas, já filtrados e ordenados (máx. `limite`; o
    aquecimento pede tudo com `limite=None`).

    Ordem: a das pastas; dentro de cada uma, as fotos e depois os vídeos, em
    ordem natural do nome. Uma pasta que falhou é pulada; se TODAS falharam,
    `ListagemIndisponivel`.
    """
    if not pastas:
        return []
    trava = asyncio.Semaphore(6)

    async def _um(pasta: str, tipo: str) -> list[str] | None:
        async with trava:
            return await _arquivos_da_pasta(pasta, tipo)

    resultados = await asyncio.gather(*(_um(p, t) for p in pastas for t in ("imagens", "videos")))
    itens: list[Item] = []
    falhas = 0
    for i, pasta in enumerate(pastas):
        imagens, videos = resultados[2 * i], resultados[2 * i + 1]
        if imagens is None and videos is None:
            falhas += 1
            continue
        for tipo, nomes, exts in (("foto", imagens, EXT_IMAGEM), ("video", videos, EXT_VIDEO)):
            vistos: set[str] = set()
            bons: list[str] = []
            for nome in nomes or []:
                if excluido(nome) or extensao(nome) not in exts:
                    continue
                # O `fone` tem o mesmo arquivo em grafia NFC e NFD.
                chave = unicodedata.normalize("NFC", nome).casefold()
                if chave in vistos:
                    continue
                vistos.add(chave)
                bons.append(nome)
            bons.sort(key=_ordem_natural)
            itens.extend(Item(site, pasta, n, tipo) for n in bons)
    if falhas == len(pastas):
        raise ListagemIndisponivel
    return itens if limite is None else itens[:limite]


# ─────────────── o link cifrado ───────────────

JANELA_S = 3 * 3600
VALIDADE_MAX_S = 7 * 3600
VARIANTES = frozenset({"mini", "grande", "video"})
_PREFIXO_CHAVE = b"sites-midia:v1:"
_TOKEN_MAX = 2000


def expiracao(agora: float) -> int:
    """Fim da janela: de 3 a 6 h à frente, igual para todos no mesmo bloco de
    3 h — a URL fica estável e o navegador reaproveita o cache."""
    return (int(agora) // JANELA_S + 2) * JANELA_S


def _chave(site: str, token_do_site: str) -> bytes:
    segredo = get_settings().jwt_secret.encode("utf-8", "surrogatepass")
    digest = hashlib.sha256(token_do_site.encode("utf-8", "surrogatepass")).digest()
    return hmac.new(
        segredo, _PREFIXO_CHAVE + site.encode() + b":" + digest, hashlib.sha256
    ).digest()


def _chaves() -> list[tuple[str, bytes]]:
    """(site, chave) para cada token atual de cada site com recorte. Ordem
    estável: quem emite usa a 1ª do site."""
    por_site: dict[str, list[str]] = {}
    for token, site in _mapa_tokens().items():
        if site in ESCOPOS and site in RAIZES:
            por_site.setdefault(site, []).append(token)
    return [
        (site, _chave(site, tok)) for site in sorted(por_site) for tok in sorted(por_site[site])
    ]


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def cifrar(site: str, payload: dict[str, Any]) -> str | None:
    """Token do link para o payload, ou None se o site não tem token."""
    chave = next((k for s, k in _chaves() if s == site), None)
    if chave is None:
        return None
    texto = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8", "surrogatepass"
    )
    # Nonce sintético (como no AES-SIV): o mesmo payload dá sempre o mesmo
    # link dentro da janela; payloads diferentes, nonces diferentes.
    nonce = hmac.new(chave, b"nonce:" + texto, hashlib.sha256).digest()[:12]
    cifrado = AESGCM(chave).encrypt(nonce, texto, _PREFIXO_CHAVE + site.encode())
    return _b64e(nonce + cifrado)


def link(item: Item, variante: str, exp: int) -> str | None:
    # `m` = versão da marca. O nonce é determinístico e a rota de bytes manda
    # `Cache-Control: private, max-age=21600`: sem `m`, trocar a marca não
    # mudaria o link da janela, e o navegador serviria a mídia com a marca
    # antiga por até 6 h. `ler_link` não lê `m` (link sem ela continua valendo).
    from app.services.sites_midia_derivados import versao  # importa este módulo

    token = cifrar(
        item.site,
        {
            "s": item.site,
            "p": item.pasta,
            "n": item.nome,
            "v": variante,
            "e": exp,
            "m": versao(item.site),
        },
    )
    return f"/api/sites/midia/a/{token}" if token else None


@dataclass(frozen=True)
class Link:
    site: str
    pasta: str
    nome: str
    variante: str
    exp: int

    @property
    def caminho(self) -> str:
        return caminho_completo(self.pasta, self.nome)


def ler_link(token: str, agora: float | None = None) -> Link | None:
    """O link, se ele é autêntico, está na validade e aponta para algo que o
    site pode ver. Qualquer falha → None (a rota não diz qual)."""
    agora = time.time() if agora is None else agora
    if not token or len(token) > _TOKEN_MAX:
        return None
    try:
        bruto = _b64d(token)
    except (ValueError, binascii.Error):
        return None
    if len(bruto) < 12 + 16 + 2:
        return None
    nonce, cifrado = bruto[:12], bruto[12:]
    payload: dict[str, Any] | None = None
    dono = None
    for site, chave in _chaves():
        try:
            texto = AESGCM(chave).decrypt(nonce, cifrado, _PREFIXO_CHAVE + site.encode())
        except InvalidTag:
            continue
        try:
            payload = json.loads(texto.decode("utf-8", "surrogatepass"))
        except (UnicodeDecodeError, ValueError):
            return None
        dono = site
        break
    if not isinstance(payload, dict):
        return None
    s, p, n, v, e = (payload.get(k) for k in ("s", "p", "n", "v", "e"))
    if not (
        isinstance(s, str)
        and isinstance(p, str)
        and isinstance(n, str)
        and isinstance(v, str)
        and isinstance(e, int)
        and not isinstance(e, bool)
    ):
        return None
    if s != dono or s not in ESCOPOS:
        return None
    if not (agora < e <= agora + VALIDADE_MAX_S):
        return None
    if not pasta_do_site(s, p) or excluido(n):
        return None
    if v not in VARIANTES or (v == "video" and not eh_video(n)):
        return None
    return Link(site=s, pasta=p, nome=n, variante=v, exp=e)
