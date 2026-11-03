"""O anúncio PÚBLICO da conversa: o botão "Ver anúncio" do cabeçalho (03/11/2026).

O Eduardo: "um botão pra abrir ele não só no AdsPower mas no navegador
normal, só o anúncio mesmo, como uma pessoa vê ele". É a página que o
COMPRADOR vê — nem o painel do vendedor, nem o perfil do AdsPower —, numa
aba nova. Vai no GET /conversas/{id}/painel (`anuncios`), ao lado do perfil
do AdsPower.

Qual anúncio, nesta ordem (a 1ª fonte com algum ganha; várias linhas = o
menu do botão):

  1. ``pedido``: os itens do pedido da conversa —
     a. o retrato na plataforma (`dados.pedido_mkt.itens[].item_id`, Shopee
        e ML);
     b. o retrato de OUTRA conversa do mesmo pedido na mesma loja (a de
        e-mail da ponte não tem o dela; o chat do pedido tem);
     c. os SKUs do pedido no Bling → `product_links` da mesma loja, só
        quando o produto tem UM anúncio vivo lá (com dois, não dá para saber
        qual o comprador comprou — melhor nenhum que o errado). É o que cobre
        a TikTok e a Amazon, que não têm retrato;
  2. ``chat``: o último cartão de produto que o COMPRADOR mandou;
  3. ``conversa``: o anúncio da própria conversa — o cartão da pergunta
     (`dados.produto`, ML e Magalu) ou o `anuncio_id` (avaliação,
     reclamação).

O link, sem ida à loja e sem login:

  - o que a loja já deu (o `link` do cartão, o `permalink` do ML em
    `listings`), só https e do domínio da própria plataforma;
  - senão, montado pelo id: Shopee `shopee.com.br/product/<shop_id>/<item>`
    (o mesmo do cartão, `enriquecer._produtos_shopee`; o shop_id é o da
    conta), ML `produto.mercadolivre.com.br/MLB-<n>` (o ML leva ao anúncio),
    TikTok `shop.tiktok.com/view/product/<id>` e Amazon `amazon.com.br/dp/<ASIN>`
    (o ASIN é o `variation_id` do vínculo; o `external_id` lá é o SKU do
    vendedor);
  - Magalu, Temu, AliExpress e o site: só o link que a própria loja deu.
    O `external_id` da Magalu é o SKU do vendedor, não a página.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    Listing,
    Product,
    ProductLink,
)
from app.security.cipher import decrypt_json
from app.services.atendimento.constantes import AUTOR_CLIENTE

ORIGEM_PEDIDO = "pedido"
ORIGEM_CHAT = "chat"
ORIGEM_CONVERSA = "conversa"
# O menu do botão (pedido grande): o resto fica de fora.
MAX_ANUNCIOS = 10
_TITULO_MAX = 200

# Os domínios em que o link que a loja deu vale (o anúncio público de cada
# plataforma). Nada de link de fora no botão.
_DOMINIOS = {
    "shopee": ("shopee.com.br",),
    "ml": ("mercadolivre.com.br",),
    "tiktok": ("tiktok.com",),
    "amazon": ("amazon.com.br",),
    "magalu": ("magazineluiza.com.br", "magalu.com"),
}
_ROTULO = {
    "shopee": "Shopee",
    "ml": "Mercado Livre",
    "tiktok": "TikTok",
    "amazon": "Amazon",
    "magalu": "Magalu",
    "temu": "Temu",
    "aliexpress": "AliExpress",
    "carrefour": "Carrefour",
    "site": "site",
}
# As que têm o link montável pelo id (as outras, só o que a loja deu).
_MONTAVEIS = frozenset({"shopee", "ml", "tiktok", "amazon"})

_RE_DIGITOS = re.compile(r"\d{5,25}")
_RE_ML = re.compile(r"MLB-?(\d{6,15})", re.IGNORECASE)
_RE_ASIN = re.compile(r"[A-Z0-9]{10}")

SEM_ANUNCIO = "sem_anuncio"
SEM_LINK = "sem_link"
FALHOU = "falhou"


def _texto(valor: Any) -> str | None:
    if valor is None or isinstance(valor, bool):
        return None
    s = str(valor).strip()
    return s or None


def _dict(valor: Any) -> dict:
    return valor if isinstance(valor, dict) else {}


def _lista(valor: Any) -> list:
    return valor if isinstance(valor, list) else []


def link_da_loja(plataforma: str, url: Any) -> str | None:
    """O link que a loja deu, se for https do domínio da PRÓPRIA plataforma; senão None."""
    s = _texto(url)
    dominios = _DOMINIOS.get(plataforma)
    if s is None or dominios is None:
        return None
    try:
        partes = urlsplit(s)
    except ValueError:
        return None
    host = (partes.hostname or "").lower()
    if partes.scheme != "https" or partes.username or partes.password:
        return None
    return s if any(host == d or host.endswith(f".{d}") for d in dominios) else None


def link_montado(plataforma: str, item_id: Any, *, shop_id: Any = None) -> str | None:
    """A página pública do anúncio pelo id (só id limpo), ou None se não dá para montar."""
    item = _texto(item_id)
    if item is None:
        return None
    if plataforma == "shopee":
        loja = _texto(shop_id)
        if _RE_DIGITOS.fullmatch(item) and loja and loja.isdigit() and int(loja) > 0:
            return f"https://shopee.com.br/product/{int(loja)}/{item}"
        return None
    if plataforma == "ml":
        m = _RE_ML.fullmatch(item)
        return f"https://produto.mercadolivre.com.br/MLB-{m.group(1)}" if m else None
    if plataforma == "tiktok":
        if _RE_DIGITOS.fullmatch(item):
            return f"https://shop.tiktok.com/view/product/{item}?region=BR&locale=pt-BR"
        return None
    if plataforma == "amazon":
        asin = item.upper()
        return f"https://www.amazon.com.br/dp/{asin}" if _RE_ASIN.fullmatch(asin) else None
    return None


@dataclass
class _Candidato:
    item_id: str
    titulo: str | None
    variacao: str | None
    link: str | None
    origem: str


def _cartao(plataforma: str, bruto: Any, origem: str) -> _Candidato | None:
    """Um cartão de produto (anexo ou `dados.produto`) → candidato; sem id → None."""
    c = _dict(bruto)
    item = _texto(c.get("item_id")) or _texto(c.get("id"))
    if item is None:
        return None
    return _Candidato(
        item_id=item,
        titulo=_texto(c.get("titulo")),
        variacao=None,
        link=link_da_loja(plataforma, c.get("link")),
        origem=origem,
    )


def _do_retrato(dados: Any) -> list[_Candidato]:
    """Os itens do retrato do pedido com o anúncio (`item_id`), na ordem do pedido."""
    retrato = _dict(_dict(dados).get("pedido_mkt"))
    saida = []
    for it in _lista(retrato.get("itens")):
        it = _dict(it)
        item = _texto(it.get("item_id"))
        if item is None:
            continue
        saida.append(
            _Candidato(
                item_id=item,
                titulo=_texto(it.get("titulo")),
                variacao=_texto(it.get("variacao")),
                link=None,
                origem=ORIGEM_PEDIDO,
            )
        )
    return saida


async def _retrato_irmao(session: AsyncSession, conversa: AtendimentoConversa) -> list[_Candidato]:
    """O retrato de outra conversa do MESMO pedido na MESMA loja (o chat da de e-mail)."""
    pedido = _texto(conversa.pedido_marketplace)
    if pedido is None or conversa.integration_id is None:
        return []
    dados = await session.scalar(
        select(AtendimentoConversa.dados)
        .where(
            AtendimentoConversa.integration_id == conversa.integration_id,
            AtendimentoConversa.plataforma == conversa.plataforma,
            AtendimentoConversa.pedido_marketplace == pedido,
            AtendimentoConversa.id != conversa.id,
            AtendimentoConversa.dados.has_key("pedido_mkt"),
        )
        .order_by(AtendimentoConversa.updated_at.desc())
        .limit(1)
    )
    return _do_retrato(dados)


async def _dos_vinculos(
    session: AsyncSession, conversa: AtendimentoConversa, skus: Iterable[Any]
) -> list[_Candidato]:
    """SKUs do pedido no Bling → o anúncio da loja (`product_links`), só o sem dúvida.

    Um anúncio vivo do produto naquela loja = ele; dois ou mais = não se sabe
    qual o comprador comprou, fica de fora. Na Amazon o anúncio é o ASIN
    (`variation_id`).
    """
    ordem = [s for s in dict.fromkeys((_texto(s) or "").lower() for s in skus) if s]
    if not ordem or conversa.integration_id is None:
        return []
    anuncio = (
        ProductLink.variation_id if conversa.plataforma == "amazon" else ProductLink.external_id
    )
    linhas = (
        await session.execute(
            select(func.lower(Product.sku), anuncio, ProductLink.listing_title)
            .join(ProductLink, ProductLink.product_id == Product.id)
            .where(
                func.lower(Product.sku).in_(ordem),
                ProductLink.integration_id == conversa.integration_id,
                ProductLink.morto_desde.is_(None),
                anuncio.is_not(None),
            )
            .limit(200)
        )
    ).all()
    por_sku: dict[str, dict[str, str | None]] = {}
    for sku, item, titulo in linhas:
        item = _texto(item)
        if item is not None:
            por_sku.setdefault(sku, {}).setdefault(item, _texto(titulo))
    saida = []
    for sku in ordem:
        anuncios = por_sku.get(sku) or {}
        if len(anuncios) == 1:
            ((item, titulo),) = anuncios.items()
            saida.append(_Candidato(item, titulo, None, None, ORIGEM_PEDIDO))
    return saida


async def _ultimo_cartao_do_comprador(
    session: AsyncSession, conversa: AtendimentoConversa
) -> list[_Candidato]:
    """O último cartão de produto que o COMPRADOR mandou na conversa."""
    anexos = await session.scalar(
        select(AtendimentoMensagem.anexos)
        .where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.autor == AUTOR_CLIENTE,
            AtendimentoMensagem.tipo == "produto",
        )
        .order_by(
            func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at).desc()
        )
        .limit(1)
    )
    for a in reversed(_lista(anexos)):
        if _dict(a).get("tipo") == "produto":
            c = _cartao(conversa.plataforma, a, ORIGEM_CHAT)
            if c is not None:
                return [c]
    return []


def _da_conversa(conversa: AtendimentoConversa) -> list[_Candidato]:
    """O anúncio da conversa: o cartão da pergunta, senão o `anuncio_id`."""
    c = _cartao(conversa.plataforma, _dict(conversa.dados).get("produto"), ORIGEM_CONVERSA)
    if c is not None:
        return [c]
    item = _texto(conversa.anuncio_id)
    if item is None:
        return []
    return [_Candidato(item, _texto(conversa.anuncio_titulo), None, None, ORIGEM_CONVERSA)]


async def _shop_id(session: AsyncSession, integration_id: UUID | None) -> int | None:
    """O id da loja na Shopee (das credenciais da conta: só ele sai daqui)."""
    if integration_id is None:
        return None
    integ = await session.get(Integration, integration_id)
    if integ is None:
        return None
    try:
        bruto = decrypt_json(integ.credentials).get("shop_id")
    except Exception:  # noqa: BLE001 — credencial ilegível: sem o link montado
        return None
    try:
        n = int(bruto)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


async def _permalinks_ml(
    session: AsyncSession, integration_id: UUID | None, ids: list[str]
) -> dict[str, str]:
    """O `permalink` que o ML deu, guardado no catálogo (`listings.raw_data`)."""
    if integration_id is None or not ids:
        return {}
    linhas = (
        await session.execute(
            select(Listing.external_id, Listing.raw_data["permalink"].astext)
            .where(Listing.integration_id == integration_id, Listing.external_id.in_(ids))
            .limit(len(ids) * 5)
        )
    ).all()
    saida: dict[str, str] = {}
    for item, link in linhas:
        if (ok := link_da_loja("ml", link)) is not None:
            saida.setdefault(item, ok)
    return saida


def _sem(codigo: str, motivo: str) -> dict:
    return {"itens": [], "motivo": motivo, "codigo": codigo}


async def anuncios_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa, *, skus_bling: Iterable[Any] = ()
) -> dict:
    """O bloco `anuncios` do painel: `{itens: [...], motivo, codigo}`.

    Cada item: `item_id`, `titulo`, `variacao`, `link` (sempre https da
    plataforma) e `origem` (pedido | chat | conversa). Sem link nenhum,
    `itens` vazio e o porquê em `motivo` (o botão fica desligado com ele).
    """
    plataforma = conversa.plataforma
    candidatos = _do_retrato(conversa.dados)
    if not candidatos:
        candidatos = await _retrato_irmao(session, conversa)
    if not candidatos:
        candidatos = await _dos_vinculos(session, conversa, skus_bling)
    if not candidatos:
        candidatos = await _ultimo_cartao_do_comprador(session, conversa)
    if not candidatos:
        candidatos = _da_conversa(conversa)
    rotulo = _ROTULO.get(plataforma, plataforma)
    if not candidatos:
        return _sem(
            SEM_ANUNCIO,
            "Não achei o anúncio desta conversa: nem no pedido, nem cartão de produto"
            " do comprador.",
        )

    unicos: dict[str, _Candidato] = {}
    for c in candidatos:
        unicos.setdefault(c.item_id, c)
    lista = list(unicos.values())[:MAX_ANUNCIOS]

    # Só o que faltar: o shop_id (uma leitura) e os permalinks do ML (uma consulta).
    faltam = [c for c in lista if c.link is None]
    shop_id = None
    if plataforma == "shopee" and faltam:
        shop_id = await _shop_id(session, conversa.integration_id)
    if plataforma == "ml" and faltam:
        permalinks = await _permalinks_ml(
            session, conversa.integration_id, [c.item_id for c in faltam]
        )
        for c in faltam:
            c.link = permalinks.get(c.item_id)
    for c in lista:
        if c.link is None:
            c.link = link_montado(plataforma, c.item_id, shop_id=shop_id)

    itens = [
        {
            "item_id": c.item_id,
            "titulo": (c.titulo or "")[:_TITULO_MAX] or None,
            "variacao": c.variacao,
            "link": c.link,
            "origem": c.origem,
        }
        for c in lista
        if c.link is not None
    ]
    if itens:
        return {"itens": itens, "motivo": None, "codigo": None}
    if plataforma not in _MONTAVEIS:
        return _sem(SEM_LINK, f"Sem o link público deste anúncio ({rotulo}): a loja não informou.")
    if plataforma == "shopee" and shop_id is None:
        return _sem(SEM_LINK, "Sem o id da loja na Shopee, não dá para montar o link do anúncio.")
    return _sem(SEM_LINK, "O número do anúncio não está num formato que dê para abrir.")
