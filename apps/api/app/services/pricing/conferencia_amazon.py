"""Leitor do preço VIVO na Amazon, para a Conferência de preços.

O envio da Tabela (`AmazonClient.update_price`) endereça o anúncio pelo SKU do
PRODUTO vinculado (products.sku — `push._dispatch_price_update_link`; sem ele,
o SKU do anúncio) e grava o `our_price` da oferta B2C (`purchasable_offer`, em
reais inteiros). A Amazon pode guardar esse atributo e NÃO aplicar (16/09/2026,
`services/pricing_confirmacao_amazon.py`: enviou 445, a loja seguiu a 599) — por
isso o preço que vale é o da OFERTA viva (`offers[]`, a B2C). Sem oferta
(anúncio inativo, sem estoque…), vale o `our_price` gravado, com o motivo
"sem oferta ativa".

Promoção: `discounted_price` com a agenda valendo agora → `preco_promocional`
e o motivo "em promoção"; `preco` continua sendo o preço normal (o que o envio
grava). Se a oferta viva já mostra o preço da promoção, o normal é o
`our_price` gravado.

Um anúncio por chamada (`GET /listings/2021-08-01/items/{seller}/{sku}`), em
série e com folga entre as chamadas (o teto do getListingsItem é de poucas por
segundo). 401/403 = a conta inteira sem acesso (levanta); o resto vira motivo
no anúncio.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
import structlog

from app.models import ProductLink
from app.services.pricing.conferencia_leitores import LeituraAnuncio, dinheiro, registrar

logger = structlog.get_logger()

# O núcleo (`conferencia.py`) põe em cada vínculo o SKU do produto vinculado
# (products.sku), o mesmo que o envio usa para achar o anúncio.
ATRIBUTO_SKU_DO_PRODUTO = "sku_do_produto"

INCLUIR = "summaries,attributes,offers"
OFERTA_B2C = "B2C"
# `audience` da oferta B2C ("Sell on Amazon"); a B2B é a Amazon Business.
PUBLICO_B2C = "ALL"
STATUS_COMPRAVEL = "BUYABLE"

HTTP_OK = 200
HTTP_NAO_ENCONTRADO = 404
HTTP_LIMITE = 429
HTTP_ERRO_SERVIDOR = 500
HTTP_ACESSO_RECUSADO = (401, 403)

# Limite de taxa (429) ou erro do servidor: espera e tenta de novo.
ESPERAS_S = (1.0, 2.0, 4.0)
# Folga entre um anúncio e outro.
INTERVALO_S = 0.2
# Anúncios seguidos sem resposta da Amazon: para de insistir na conta e marca
# o resto como "erro ao ler" (senão uma API fora do ar come o tempo do job).
FALHAS_SEGUIDAS_MAX = 5

MOTIVO_NAO_ENCONTRADO = "anúncio não encontrado"
MOTIVO_ERRO_AO_LER = "erro ao ler"
MOTIVO_LIMITE = "limite de leituras da Amazon"
MOTIVO_SEM_SKU = "vínculo sem SKU"
MOTIVO_SEM_PRECO = "anúncio sem preço"
MOTIVO_SEM_OFERTA = "sem oferta ativa"
MOTIVO_PROMOCAO = "em promoção"
MOTIVO_DESISTIU = "erro ao ler (a Amazon parou de responder)"


class AcessoRecusadoError(RuntimeError):
    """A Amazon recusou o acesso da conta inteira (token/permissão)."""


async def _esperar(segundos: float) -> None:
    await asyncio.sleep(segundos)


def sku_do_envio(link: ProductLink) -> str:
    """O SKU que o envio usa para este vínculo: o do produto, senão o do anúncio."""
    return str(
        getattr(link, ATRIBUTO_SKU_DO_PRODUTO, None) or link.external_sku or link.external_id or ""
    ).strip()


def _instante(valor: Any) -> datetime | None:
    """Data da agenda da Amazon (ISO, às vezes em {"value": ...}). Levanta
    ValueError se não der para ler."""
    if isinstance(valor, dict):
        valor = valor.get("value")
    if valor in (None, ""):
        return None
    quando = datetime.fromisoformat(str(valor).strip().replace("Z", "+00:00"))
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def _em_vigor(agenda: dict, agora: datetime) -> bool:
    try:
        inicio = _instante(agenda.get("start_at"))
        fim = _instante(agenda.get("end_at"))
    except (TypeError, ValueError):
        return False  # agenda ilegível: não conta como promoção
    return (inicio is None or inicio <= agora) and (fim is None or agora <= fim)


def _valor(blocos: Any, agora: datetime | None = None) -> Decimal | None:
    """O primeiro `value_with_tax` de uma lista de {"schedule": [...]}; com
    `agora`, só de agenda valendo nesse instante."""
    for bloco in blocos if isinstance(blocos, list) else []:
        agendas = bloco.get("schedule") if isinstance(bloco, dict) else None
        for agenda in agendas if isinstance(agendas, list) else []:
            if not isinstance(agenda, dict) or dinheiro(agenda.get("value_with_tax")) is None:
                continue
            if agora is None or _em_vigor(agenda, agora):
                return dinheiro(agenda.get("value_with_tax"))
    return None


def _do_marketplace(entrada: dict, chave: str, marketplace_id: str) -> bool:
    valor = entrada.get(chave)
    return not valor or not marketplace_id or valor == marketplace_id


def _oferta_b2c(oferta: dict) -> bool:
    tipo = str(oferta.get("offerType") or "").upper()
    if tipo:
        return tipo == OFERTA_B2C
    publico = oferta.get("audience")
    publico = publico.get("value") if isinstance(publico, dict) else publico
    return str(publico or PUBLICO_B2C).upper() == PUBLICO_B2C


def _preco_da_oferta(corpo: dict, marketplace_id: str) -> Decimal | None:
    """Preço da oferta B2C viva no marketplace, ou None."""
    ofertas = corpo.get("offers")
    for oferta in ofertas if isinstance(ofertas, list) else []:
        if not isinstance(oferta, dict) or not _oferta_b2c(oferta):
            continue
        if not _do_marketplace(oferta, "marketplaceId", marketplace_id):
            continue
        preco = oferta.get("price")
        valor = dinheiro(preco.get("amount")) if isinstance(preco, dict) else None
        if valor is not None:
            return valor
    return None


def _precos_do_atributo(
    corpo: dict, marketplace_id: str, agora: datetime
) -> tuple[Decimal | None, Decimal | None]:
    """(our_price gravado, promoção valendo agora) da oferta B2C no atributo
    `purchasable_offer` — o que o envio grava."""
    atributos = corpo.get("attributes")
    entradas = atributos.get("purchasable_offer") if isinstance(atributos, dict) else None
    for entrada in entradas if isinstance(entradas, list) else []:
        if not isinstance(entrada, dict) or not _do_marketplace(
            entrada, "marketplace_id", marketplace_id
        ):
            continue
        if str(entrada.get("audience") or PUBLICO_B2C).upper() != PUBLICO_B2C:
            continue
        return _valor(entrada.get("our_price")), _valor(entrada.get("discounted_price"), agora)
    return None, None


def _ativo(corpo: dict, marketplace_id: str) -> bool | None:
    resumos = corpo.get("summaries")
    for resumo in resumos if isinstance(resumos, list) else []:
        if not isinstance(resumo, dict) or not _do_marketplace(
            resumo, "marketplaceId", marketplace_id
        ):
            continue
        status = resumo.get("status")
        if isinstance(status, list):
            return STATUS_COMPRAVEL in {str(s).upper() for s in status}
    return None


def leitura_do_anuncio(corpo: dict, marketplace_id: str, *, agora: datetime) -> LeituraAnuncio:
    """Preço comparável com a Tabela, a partir do anúncio lido."""
    oferta = _preco_da_oferta(corpo, marketplace_id)
    normal, promocional = _precos_do_atributo(corpo, marketplace_id, agora)
    ativo = _ativo(corpo, marketplace_id)
    motivos: list[str] = []
    if oferta is not None:
        preco = oferta
        if promocional is not None and oferta == promocional and (normal or 0) > promocional:
            # A oferta viva já mostra a promoção: o preço normal é o gravado.
            preco = normal
    else:
        preco = normal
        motivos.append(MOTIVO_SEM_OFERTA)
    if preco is None:
        return LeituraAnuncio(preco=None, ativo=ativo, motivo=MOTIVO_SEM_PRECO)
    em_promocao = promocional is not None and promocional < preco
    if em_promocao:
        motivos.insert(0, MOTIVO_PROMOCAO)
    return LeituraAnuncio(
        preco=preco,
        preco_promocional=promocional if em_promocao else None,
        ativo=ativo,
        motivo="; ".join(motivos) or None,
    )


async def _ler_sku(cliente: Any, sku: str) -> tuple[LeituraAnuncio, bool]:
    """(leitura, a Amazon respondeu?). Levanta `AcessoRecusadoError` em 401/403."""
    caminho = f"/listings/2021-08-01/items/{cliente.seller_id}/{quote(sku, safe='')}"
    params = {"marketplaceIds": cliente.marketplace_id, "includedData": INCLUIR}
    motivo = MOTIVO_ERRO_AO_LER
    for espera in (0.0, *ESPERAS_S):
        if espera:
            await _esperar(espera)
        try:
            r = await cliente._request("GET", caminho, params=params)
        except httpx.TransportError as e:
            logger.warning("conferencia_amazon_leitura_falhou", erro=type(e).__name__)
            motivo = MOTIVO_ERRO_AO_LER
            continue
        if r.status_code in HTTP_ACESSO_RECUSADO:
            raise AcessoRecusadoError(f"a Amazon recusou o acesso da conta ({r.status_code})")
        if r.status_code == HTTP_NAO_ENCONTRADO:
            return LeituraAnuncio(preco=None, motivo=MOTIVO_NAO_ENCONTRADO), True
        if r.status_code == HTTP_LIMITE or r.status_code >= HTTP_ERRO_SERVIDOR:
            motivo = MOTIVO_LIMITE if r.status_code == HTTP_LIMITE else MOTIVO_ERRO_AO_LER
            continue
        if r.status_code != HTTP_OK:
            erro = f"{MOTIVO_ERRO_AO_LER} ({r.status_code})"
            return LeituraAnuncio(preco=None, motivo=erro), True
        try:
            corpo = r.json()
        except ValueError:
            corpo = None
        if not isinstance(corpo, dict):
            return LeituraAnuncio(preco=None, motivo=MOTIVO_ERRO_AO_LER), True
        agora = datetime.now(UTC)
        return leitura_do_anuncio(corpo, cliente.marketplace_id, agora=agora), True
    return LeituraAnuncio(preco=None, motivo=motivo), False


@registrar("amazon")
async def ler_precos_amazon(
    cliente: Any, vinculos: list[ProductLink]
) -> dict[UUID, LeituraAnuncio]:
    """Uma `LeituraAnuncio` por vínculo (chave = `ProductLink.id`)."""
    if not cliente.seller_id or not cliente.marketplace_id:
        raise AcessoRecusadoError("credencial da Amazon sem seller_id/marketplace_id")
    por_sku: dict[str, LeituraAnuncio] = {}
    saida: dict[UUID, LeituraAnuncio] = {}
    falhas_seguidas = 0
    for lk in vinculos:
        sku = sku_do_envio(lk)
        if not sku:
            saida[lk.id] = LeituraAnuncio(preco=None, motivo=MOTIVO_SEM_SKU)
            continue
        if sku not in por_sku:
            if falhas_seguidas >= FALHAS_SEGUIDAS_MAX:
                por_sku[sku] = LeituraAnuncio(preco=None, motivo=MOTIVO_DESISTIU)
            else:
                if por_sku:
                    await _esperar(INTERVALO_S)
                por_sku[sku], respondeu = await _ler_sku(cliente, sku)
                falhas_seguidas = 0 if respondeu else falhas_seguidas + 1
        saida[lk.id] = por_sku[sku]
    return saida
