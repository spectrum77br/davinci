"""A FOTO do modelo sugerido na oferta de troca (item 4, 03/11/2026).

O Eduardo (03/11/2026): "daria pra gente no copiar anexar uma imagem do
modelo de troca já junto seria melhor" — o comprador vê o que está aceitando.
Cada sugestão elegível do bloco (`troca_sugestoes`) ganha a `foto` (a
miniatura na tela); o "copiar oferta" baixa a foto pela rota GET
/pedidos/{n}/troca/foto e a põe como anexo na caixa de resposta; o "enviar
oferta" (`troca_oferta.enviar_oferta`) manda a foto antes do texto.

DE ONDE VEM (nenhuma ida à loja; só o que o DaVinci já guardou):
  1. o RETRATO de um pedido da MESMA loja da conversa
     (`conversa.dados.pedido_mkt.itens[]`, `enriquecer`): o `sku` é o do
     Bling e a `imagem` é a da VARIAÇÃO que o comprador daquele pedido viu
     no anúncio — origem `loja`;
  2. o retrato de OUTRA loja (a mesma plataforma antes) — `outra_loja`;
  3. o catálogo: a miniatura do anúncio em `listings` e a `products.image_url`
     — `catalogo`.
  A foto da MESMA composição (o aparelho com os mesmos acessórios, em
  qualquer lote) vem antes da loja: a do kit com fone mostraria ao comprador
  um fone que não vai. Sem ela, vale a do principal (o aparelho, a mala) —
  a que mostra menos acessório a mais primeiro (`chaves_da_foto`, `escolher`).
  Medido em 03/11/2026: com a loja na frente da composição, ~60% das fotos
  eram de outra composição.

A FOTO GENÉRICA: na Shopee o anúncio pode usar a MESMA imagem para todas as
variações (a capa) — medido em 03/11/2026: 107 das 1.103 imagens dos
retratos aparecem para mais de um modelo (o dg053 e o dg056 com a mesma
foto). Ela pode mostrar OUTRA cor: vem por último (`generica`), a tela avisa
e o "enviar oferta" só a manda se a pessoa marcar.

Medido em produção (03/11/2026): `products.image_url` vazio nos 5.538
produtos; os `listings` parados desde 15/05 (e a Shopee lá com o SKU do
anúncio, não o do Bling); os retratos cobrem 526 dos 1.865 SKUs com estoque
pelo SKU exato e 1.360 pelo principal — a consulta leva ~75 ms.

A FOTO DO KIT: o aparelho sozinho que só tem foto do kit (com fone, com
relógio) — ou o kit que só tem a de outro kit — fica com
`acessorio_a_mais`: a tela avisa e o "enviar oferta" só a manda marcada,
como a genérica.

A FOTO QUE VAI: a da CDN da plataforma em tamanho cheio (`url_para_enviar`:
a Shopee sem o `_tn` de 320 px — 958 px; o ML no `-O`), baixada pelo
servidor (`baixar`): só https, só os hosts das CDNs das plataformas
(`HOSTS_DAS_FOTOS`), sem seguir redirecionamento, até 10 MB, JPG ou PNG
conferido pelos bytes (`foto.validar_foto`). A URL nunca vem da tela: o
servidor acha a foto de novo pelo SKU.

Log só com SKUs e códigos — nunca a URL nem bytes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import structlog
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Listing, Product
from app.services.atendimento import foto as foto_mod
from app.services.estoque_familia import lote_de

logger = structlog.get_logger()

# ── De onde veio a foto (a tela diz) ──────────────────────────────────────
ORIGEM_LOJA = "loja"
ORIGEM_OUTRA_LOJA = "outra_loja"
ORIGEM_CATALOGO = "catalogo"
ORIGENS = (ORIGEM_LOJA, ORIGEM_OUTRA_LOJA, ORIGEM_CATALOGO)
_RETRATO = "retrato"
_CATALOGO = "catalogo"

# As CDNs de imagem das plataformas (o host ou um subdomínio dele): nenhuma
# outra URL é baixada pelo servidor.
HOSTS_DAS_FOTOS = (
    "shopee.com.br",
    "susercontent.com",
    "mlstatic.com",
    "ibyteimg.com",
    "tiktokcdn.com",
    "tiktokcdn-us.com",
)
TEMPO_MAXIMO_S = 10.0
# Quantos SKUs (padrões do LIKE) numa leitura: a lista Ag. cancelamento junta
# todos os pedidos; acima disto, as fotos ficam de fora (a lista segue).
MAX_SKUS = 120

# A miniatura do ML (`-I`, 90 px) → `-O` (500 px), como o cartão de produto.
_RE_FOTO_ML_PEQUENA = re.compile(r"-[A-Z]\.(jpe?g|png|webp)$", re.I)
# A miniatura da Shopee: o mesmo arquivo com `_tn` (320 px).
_RE_SHOPEE_TN = re.compile(r"_tn$")


class FotoIndisponivel(Exception):  # noqa: N818 — nome do domínio, como FotoInvalida
    """Não deu para baixar a foto. `code` estável; `detail` = a frase da tela."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


@dataclass(frozen=True)
class Candidata:
    """Uma foto que pode ser a do SKU: de um retrato de pedido ou do catálogo."""

    sku: str
    url: str
    fonte: str
    integration_id: UUID | None = None
    plataforma: str | None = None
    quando: datetime | None = None


@dataclass(frozen=True)
class FotoEscolhida:
    """A foto do SKU: a miniatura (`url`), a que vai ao comprador e de onde veio."""

    url: str
    url_envio: str
    origem: str
    # A mesma imagem vale para mais de um modelo no anúncio: pode mostrar outra cor.
    generica: bool = False
    # A foto é de um kit com acessório que este produto NÃO leva (o aparelho
    # sozinho só com a foto do kit com fone): pode mostrar o que não vai.
    acessorio_a_mais: bool = False

    def para_tela(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "origem": self.origem,
            "generica": self.generica,
            "acessorio_a_mais": self.acessorio_a_mais,
        }


# ── PURAS ─────────────────────────────────────────────────────────────────


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def url_permitida(url: Any) -> bool:
    """https e de uma CDN de imagem das plataformas (`HOSTS_DAS_FOTOS`). PURA."""
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in HOSTS_DAS_FOTOS)


def url_da_previa(url: Any) -> str | None:
    """A miniatura em https (o `http://` do catálogo do ML vira conteúdo misto); o ML no `-O`."""
    if not isinstance(url, str) or not url.strip():
        return None
    u = url.strip()
    if u.startswith("http://"):
        u = "https://" + u[len("http://") :]
    if _host(u).endswith("mlstatic.com"):
        u = _RE_FOTO_ML_PEQUENA.sub(r"-O.\1", u)
    return u


def url_para_enviar(url: str) -> str:
    """A foto em tamanho cheio para o comprador: a Shopee sem o `_tn`. PURA."""
    u = url_da_previa(url) or url
    if _host(u).endswith(("shopee.com.br", "susercontent.com")):
        partes = urlsplit(u)
        caminho = _RE_SHOPEE_TN.sub("", partes.path)
        u = partes._replace(path=caminho).geturl()
    return u


def _sem_lote(pedaco: str) -> str:
    lote = lote_de(pedaco)
    return pedaco[: -(len(lote) + 1)] if lote else pedaco


def chaves_da_foto(sku: str | None) -> tuple[str, str, str]:
    """(o SKU, sem o lote, o principal sem o lote) em minúsculas. PURA.

    "dg088.ci+a001.ci" → ("dg088.ci+a001.ci", "dg088+a001", "dg088"): o mesmo
    aparelho em qualquer lote e com qualquer acessório tem a mesma foto. A
    mala e o eletro não têm lote (o número é tamanho/voltagem): as três iguais.
    """
    low = (sku or "").strip().lower()
    pedacos = [p.strip() for p in low.split("+") if p.strip()]
    bases = [_sem_lote(p) for p in pedacos]
    return low, "+".join(bases), bases[0] if bases else ""


def modelo_da_foto(sku: str | None) -> str:
    """O principal sem o lote — o que diz se a foto é de UM modelo só. PURA."""
    return chaves_da_foto(sku)[2]


def _nivel(chaves: tuple[str, str, str], sku_candidato: str) -> int | None:
    """0 = o SKU exato, 1 = sem o lote, 2 = o principal; None = outro produto."""
    outras = chaves_da_foto(sku_candidato)
    return next((i for i in range(3) if chaves[i] and chaves[i] == outras[i]), None)


def _acessorios(sku: str | None) -> set[str]:
    """Os acessórios do kit, sem o lote ("dg085.ra+a003.ra" → {"a003"}). PURA."""
    return set(chaves_da_foto(sku)[1].split("+")[1:])


def escolher(
    candidatas: Iterable[Candidata],
    sku: str,
    *,
    integration_id: UUID | None,
    plataforma: str | None,
    modelos_da_url: Mapping[str, set[str]] | None = None,
) -> FotoEscolhida | None:
    """A melhor foto do SKU para a conversa (da loja `integration_id`). PURA.

    A ordem: a foto de UM modelo só antes da genérica (a capa do anúncio
    com várias cores); a da MESMA composição (o aparelho com os mesmos
    acessórios, em qualquer lote) antes de qualquer outra, de qualquer loja —
    a foto do kit com fone mostraria um fone que não vai —, e, sem ela, a que
    mostra menos acessório a mais e depois a que falta menos; a da mesma
    loja, a de outra loja, a do catálogo; o SKU exato antes de outro lote; a
    mesma plataforma; a mais nova. `modelos_da_url` = {url: os modelos
    (principal) que aparecem com ela em TODOS os retratos}.
    """
    chaves = chaves_da_foto(sku)
    if not chaves[0]:
        return None
    acess = _acessorios(sku)
    modelos = modelos_da_url or {}
    melhor: tuple[tuple, Candidata, bool] | None = None
    for c in candidatas:
        if not url_permitida(url_da_previa(c.url)):
            continue
        nivel = _nivel(chaves, c.sku)
        if nivel is None:
            continue
        generica = len(modelos.get(c.url) or {modelo_da_foto(c.sku)}) > 1
        if c.fonte == _RETRATO:
            origem = 0 if integration_id is not None and c.integration_id == integration_id else 1
        else:
            origem = 2
        do_kit = _acessorios(c.sku)
        ordem = (
            generica,
            len(do_kit - acess),
            len(acess - do_kit),
            origem,
            nivel,
            plataforma is None or c.plataforma != plataforma,
            -(c.quando.timestamp() if c.quando is not None else 0.0),
        )
        if melhor is None or ordem < melhor[0]:
            melhor = (ordem, c, generica)
    if melhor is None:
        return None
    ordem, c, generica = melhor
    previa = url_da_previa(c.url) or c.url
    return FotoEscolhida(
        url=previa,
        url_envio=url_para_enviar(previa),
        origem=ORIGENS[ordem[3]],
        generica=generica,
        acessorio_a_mais=ordem[1] > 0,
    )


def _padrao(sku: str) -> str | None:
    """O LIKE do principal ("dg088%"), com `%`, `_` e `\\` escapados."""
    principal = modelo_da_foto(sku)
    if not principal:
        return None
    return re.sub(r"([\\%_])", r"\\\1", principal) + "%"


# ── As leituras ───────────────────────────────────────────────────────────


def _sql_retratos():
    """Os itens dos retratos com foto cujo SKU começa por um dos principais, e os
    modelos de cada foto achada em TODOS os retratos (a capa compartilhada).

    SQL cru (os `jsonb_array_elements`), com o schema das settings: a conexão
    da api não tem `search_path` (o mesmo cuidado de `avaliacoes`).
    """
    schema = get_settings().database_schema
    return text(
        f"""
        WITH itens AS (
            SELECT lower(it->>'sku') AS sku, it->>'imagem' AS url,
                   c.integration_id, c.plataforma, c.updated_at AS quando
              FROM "{schema}".atendimento_conversas c
             CROSS JOIN LATERAL jsonb_array_elements(
                   CASE WHEN jsonb_typeof(c.dados->'pedido_mkt'->'itens') = 'array'
                        THEN c.dados->'pedido_mkt'->'itens' ELSE '[]'::jsonb END) AS it
             WHERE COALESCE(it->>'imagem', '') <> '' AND COALESCE(it->>'sku', '') <> ''
        ), achados AS (
            SELECT DISTINCT ON (sku, url, integration_id) sku, url, integration_id,
                   plataforma, quando
              FROM itens
             WHERE sku LIKE ANY(:padroes)
             ORDER BY sku, url, integration_id, quando DESC
        ), por_url AS (
            SELECT url, array_agg(DISTINCT sku) AS skus
              FROM itens
             WHERE url IN (SELECT url FROM achados)
             GROUP BY url
        )
        SELECT a.sku, a.url, a.integration_id, a.plataforma, a.quando, p.skus
          FROM achados a JOIN por_url p USING (url)
        """  # noqa: S608 — o schema vem das settings, nunca de fora
    )


async def ler_candidatas(
    session: AsyncSession, skus: Iterable[str]
) -> tuple[list[Candidata], dict[str, set[str]]]:
    """As fotos que podem ser destes SKUs (retratos e catálogo) e os modelos de cada uma.

    Três consultas, para todos os SKUs de uma vez (a lista lê uma vez só).
    """
    padroes = sorted({p for p in (_padrao(s) for s in skus) if p})
    if not padroes:
        return [], {}
    if len(padroes) > MAX_SKUS:
        logger.info("atendimento_troca_foto_skus_demais", skus=len(padroes))
        padroes = padroes[:MAX_SKUS]
    candidatas: list[Candidata] = []
    modelos: dict[str, set[str]] = {}
    for r in (await session.execute(_sql_retratos(), {"padroes": padroes})).all():
        candidatas.append(
            Candidata(
                sku=r.sku,
                url=r.url,
                fonte=_RETRATO,
                integration_id=r.integration_id,
                plataforma=r.plataforma,
                quando=r.quando,
            )
        )
        modelos[r.url] = {modelo_da_foto(s) for s in (r.skus or [])}
    sku_l = func.lower(Listing.sku)
    for r in (
        await session.execute(
            select(sku_l, Listing.thumbnail_url, Listing.platform, Listing.imported_at).where(
                func.coalesce(Listing.thumbnail_url, "") != "",
                or_(*(sku_l.like(p) for p in padroes)),
            )
        )
    ).all():
        plataforma = getattr(r.platform, "value", r.platform)
        candidatas.append(
            Candidata(
                sku=r[0],
                url=r.thumbnail_url,
                fonte=_CATALOGO,
                plataforma=plataforma,
                quando=r.imported_at,
            )
        )
    sku_p = func.lower(Product.sku)
    for r in (
        await session.execute(
            select(sku_p, Product.image_url, Product.updated_at).where(
                func.coalesce(Product.image_url, "") != "",
                or_(*(sku_p.like(p) for p in padroes)),
            )
        )
    ).all():
        candidatas.append(
            Candidata(sku=r[0], url=r.image_url, fonte=_CATALOGO, quando=r.updated_at)
        )
    # O catálogo não tem a régua dos retratos: a capa conta pelo que veio aqui.
    for c in candidatas:
        if c.fonte == _CATALOGO:
            modelos.setdefault(c.url, set()).add(modelo_da_foto(c.sku))
    return candidatas, modelos


async def fotos_dos_skus(
    session: AsyncSession,
    skus: Sequence[str],
    *,
    integration_id: UUID | None,
    plataforma: str | None,
) -> dict[str, FotoEscolhida]:
    """{sku em minúsculas: a foto} — só os que têm."""
    candidatas, modelos = await ler_candidatas(session, skus)
    saida: dict[str, FotoEscolhida] = {}
    for sku in skus:
        f = escolher(
            candidatas,
            sku,
            integration_id=integration_id,
            plataforma=plataforma,
            modelos_da_url=modelos,
        )
        if f is not None:
            saida[sku.strip().lower()] = f
    return saida


def _skus_do_bloco(bloco: Mapping[str, Any] | None) -> list[str]:
    return [
        str(s["sku"])
        for it in (bloco or {}).get("itens") or []
        for s in it.get("sugestoes") or []
        if s.get("texto_oferta") and s.get("sku")
    ]


def preencher(
    bloco: dict[str, Any] | None,
    candidatas: Sequence[Candidata],
    modelos: Mapping[str, set[str]],
    *,
    integration_id: UUID | None,
    plataforma: str | None,
) -> None:
    """Põe a `foto` em cada sugestão COM oferta do bloco (a de fora e o nível 0, não)."""
    for it in (bloco or {}).get("itens") or []:
        for s in it.get("sugestoes") or []:
            if not s.get("texto_oferta"):
                continue
            f = escolher(
                candidatas,
                str(s.get("sku") or ""),
                integration_id=integration_id,
                plataforma=plataforma,
                modelos_da_url=modelos,
            )
            s["foto"] = f.para_tela() if f is not None else None


async def fotos_nos_blocos(
    session: AsyncSession,
    blocos: Sequence[tuple[dict[str, Any] | None, UUID | None, str | None]],
) -> bool:
    """As fotos de vários blocos (bloco, integração, plataforma) com UMA leitura.

    Num SAVEPOINT e sem levantar: a foto fora nunca derruba as sugestões
    (sem ela, a oferta segue só com o texto e a tela avisa). False = falhou.
    """
    skus = sorted({s for b, _, _ in blocos for s in _skus_do_bloco(b)})
    if not skus:
        return True
    try:
        async with session.begin_nested():
            candidatas, modelos = await ler_candidatas(session, skus)
    except Exception as e:  # noqa: BLE001 — a foto é complemento da oferta
        logger.warning("atendimento_troca_foto_leitura_falhou", err=type(e).__name__)
        return False
    for bloco, integ, plat in blocos:
        preencher(bloco, candidatas, modelos, integration_id=integ, plataforma=plat)
    return True


# ── O download (a rota da cópia e o envio) ────────────────────────────────


def _http() -> httpx.AsyncClient:
    """O cliente da CDN (o teste troca por um transporte falso)."""
    return httpx.AsyncClient(timeout=TEMPO_MAXIMO_S, follow_redirects=False)


async def baixar(url: str, nome: str) -> foto_mod.Foto:
    """A foto da CDN, conferida como a da caixa (JPG/PNG, até 10 MB). Levanta `FotoIndisponivel`."""
    if not url_permitida(url):
        raise FotoIndisponivel("foto_fora_da_cdn", "A foto não é de uma CDN das plataformas.")
    dados = bytearray()
    tipo = None
    try:
        async with _http() as cli, cli.stream("GET", url) as r:
            if r.status_code != 200:
                raise FotoIndisponivel(
                    "foto_http", f"A CDN da foto respondeu {r.status_code}: não consegui baixá-la."
                )
            tipo = r.headers.get("content-type")
            async for parte in r.aiter_bytes():
                dados += parte
                if len(dados) > foto_mod.MAX_BYTES_UPLOAD:
                    raise FotoIndisponivel("foto_grande", "A foto do anúncio passa de 10 MB.")
    except httpx.HTTPError as e:
        raise FotoIndisponivel("foto_rede", "Não consegui baixar a foto do anúncio agora.") from e
    try:
        f = foto_mod.validar_foto(bytes(dados), nome, tipo)
    except foto_mod.FotoInvalida as e:
        raise FotoIndisponivel(e.code, e.detail) from e
    if f.mime == foto_mod.PDF:
        raise FotoIndisponivel("foto_tipo", "A foto do anúncio não é JPG nem PNG.")
    return f


async def foto_do_sku(
    session: AsyncSession, sku: str, *, integration_id: UUID | None, plataforma: str | None
) -> FotoEscolhida | None:
    """A foto escolhida de UM SKU (o envio e a rota da cópia acham de novo pelo SKU)."""
    return (
        await fotos_dos_skus(session, [sku], integration_id=integration_id, plataforma=plataforma)
    ).get(sku.strip().lower())
