"""Leitor do preço vivo na Amazon (Conferência de preços, 02/11/2026).

A Amazon é SIMULADA (cliente falso com `_request`): nenhuma chamada de verdade.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import unquote

import httpx
import pytest

from app.models import IntegrationPlatform, ProductLink
from app.services.pricing import conferencia, conferencia_amazon
from app.services.pricing.conferencia_leitores import LEITORES, LeituraAnuncio

MP = "A2Q3Y263D00KWC"


def _vinculo(
    *, sku_produto: str | None = None, external_sku: str | None = None, external_id: str = "B0X"
) -> ProductLink:
    lk = ProductLink(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        integration_id=uuid.uuid4(),
        platform=IntegrationPlatform.AMAZON,
        external_id=external_id,
        external_sku=external_sku,
    )
    if sku_produto is not None:
        setattr(lk, conferencia_amazon.ATRIBUTO_SKU_DO_PRODUTO, sku_produto)
    return lk


def _corpo(
    *,
    oferta: str | None = None,
    oferta_b2b: str | None = None,
    our_price: float | None = None,
    desconto: float | None = None,
    inicio: datetime | None = None,
    fim: datetime | None = None,
    status: tuple[str, ...] | None = ("BUYABLE", "DISCOVERABLE"),
) -> dict[str, Any]:
    corpo: dict[str, Any] = {"sku": "x", "summaries": [], "attributes": {}, "offers": []}
    if status is not None:
        corpo["summaries"].append({"marketplaceId": MP, "asin": "B0X", "status": list(status)})
    if oferta_b2b is not None:
        corpo["offers"].append(
            {
                "marketplaceId": MP,
                "offerType": "B2B",
                "price": {"currencyCode": "BRL", "amount": oferta_b2b},
            }
        )
    if oferta is not None:
        corpo["offers"].append(
            {
                "marketplaceId": MP,
                "offerType": "B2C",
                "price": {"currencyCode": "BRL", "amount": oferta},
                "audience": {"value": "ALL", "displayName": "Sell on Amazon"},
            }
        )
    entrada: dict[str, Any] = {"marketplace_id": MP, "currency": "BRL", "audience": "ALL"}
    if our_price is not None:
        entrada["our_price"] = [{"schedule": [{"value_with_tax": our_price}]}]
    if desconto is not None:
        agenda: dict[str, Any] = {"value_with_tax": desconto}
        if inicio is not None:
            agenda["start_at"] = inicio.isoformat().replace("+00:00", "Z")
        if fim is not None:
            agenda["end_at"] = fim.isoformat().replace("+00:00", "Z")
        entrada["discounted_price"] = [{"schedule": [agenda]}]
    if our_price is not None or desconto is not None:
        corpo["attributes"]["purchasable_offer"] = [entrada]
    return corpo


class AmazonFalsa:
    """Listings Items API: respostas por SKU, em fila (a última se repete)."""

    def __init__(self, *, seller_id: str = "SELLER1", marketplace_id: str = MP) -> None:
        self.seller_id = seller_id
        self.marketplace_id = marketplace_id
        self.respostas: dict[str, list[tuple[int, Any]]] = {}
        self.chamadas: list[dict[str, Any]] = []
        self.sem_rede = False

    def anuncio(self, sku: str, corpo: Any = None, status: int = 200) -> None:
        self.respostas.setdefault(sku, []).append((status, corpo))

    async def _request(self, method: str, path: str, *, params=None, json=None):
        self.chamadas.append({"method": method, "path": path, "params": dict(params or {})})
        req = httpx.Request(method, f"https://amazon.falsa{path}")
        if self.sem_rede:
            raise httpx.ConnectTimeout("sem rede", request=req)
        sku = unquote(path.rsplit("/", 1)[-1])
        fila = self.respostas.get(sku) or [(404, {"errors": [{"code": "NOT_FOUND"}]})]
        status, corpo = fila.pop(0) if len(fila) > 1 else fila[0]
        return httpx.Response(status, json=corpo if corpo is not None else {}, request=req)


@pytest.fixture
def esperas(monkeypatch) -> list[float]:
    feitas: list[float] = []

    async def _esperar(segundos: float) -> None:
        feitas.append(segundos)

    monkeypatch.setattr(conferencia_amazon, "_esperar", _esperar)
    return feitas


async def _ler(amz: AmazonFalsa, vinculos: list[ProductLink]) -> dict[uuid.UUID, LeituraAnuncio]:
    return await conferencia_amazon.ler_precos_amazon(amz, vinculos)


def test_leitor_registrado_para_a_amazon_e_o_nucleo_usa_o_mesmo_atributo():
    assert LEITORES["amazon"] is conferencia_amazon.ler_precos_amazon
    assert conferencia.ATRIBUTO_SKU_DO_PRODUTO == conferencia_amazon.ATRIBUTO_SKU_DO_PRODUTO


@pytest.mark.asyncio
async def test_le_pelo_sku_do_envio(esperas):
    """O envio usa o SKU do PRODUTO (products.sku); sem ele, o do anúncio."""
    amz = AmazonFalsa()
    amz.anuncio("a003.sa+a001", _corpo(oferta="56.00"))
    amz.anuncio("SKU-ANUNCIO", _corpo(oferta="70.00"))
    amz.anuncio("B0ONLY", _corpo(oferta="80.00"))
    do_produto = _vinculo(sku_produto="a003.sa+a001", external_sku="OUTRO")
    do_anuncio = _vinculo(external_sku="SKU-ANUNCIO")
    so_id = _vinculo(external_id="B0ONLY")
    out = await _ler(amz, [do_produto, do_anuncio, so_id])
    assert [out[v.id].preco for v in (do_produto, do_anuncio, so_id)] == [
        Decimal("56.00"),
        Decimal("70.00"),
        Decimal("80.00"),
    ]
    primeira = amz.chamadas[0]
    # O SKU vai codificado no caminho (o "+" do kit), como no envio.
    assert primeira["path"] == "/listings/2021-08-01/items/SELLER1/a003.sa%2Ba001"
    assert primeira["params"] == {
        "marketplaceIds": MP,
        "includedData": "summaries,attributes,offers",
    }
    # Folga entre um anúncio e outro (não antes do primeiro).
    assert esperas == [conferencia_amazon.INTERVALO_S] * 2


@pytest.mark.asyncio
async def test_oferta_viva_vale_mais_que_o_atributo_gravado(esperas):
    """16/09/2026: a Amazon guardou 445 no our_price e seguiu vendendo a 599 —
    o preço que vale é o da oferta B2C viva (a B2B não conta)."""
    amz = AmazonFalsa()
    amz.anuncio("x1", _corpo(oferta="599.00", oferta_b2b="500.00", our_price=445))
    v = _vinculo(sku_produto="x1")
    out = await _ler(amz, [v])
    assert out[v.id] == LeituraAnuncio(preco=Decimal("599.00"), ativo=True)


@pytest.mark.asyncio
async def test_sem_oferta_usa_o_our_price_com_motivo(esperas):
    amz = AmazonFalsa()
    amz.anuncio("x1", _corpo(our_price=445, status=("DISCOVERABLE",)))
    amz.anuncio("x2", _corpo(oferta_b2b="500.00"))
    v1, v2 = _vinculo(sku_produto="x1"), _vinculo(sku_produto="x2")
    out = await _ler(amz, [v1, v2])
    assert out[v1.id] == LeituraAnuncio(
        preco=Decimal("445.00"), ativo=False, motivo="sem oferta ativa"
    )
    # Só a oferta B2B e nada gravado: sem preço comparável.
    assert out[v2.id].preco is None
    assert out[v2.id].motivo == conferencia_amazon.MOTIVO_SEM_PRECO


@pytest.mark.asyncio
async def test_promocao_em_vigor(esperas):
    agora = datetime.now(UTC)
    ontem, amanha = agora - timedelta(days=1), agora + timedelta(days=1)
    amz = AmazonFalsa()
    # A oferta viva mostra o preço normal; a promoção vem do atributo.
    amz.anuncio("normal", _corpo(oferta="599.00", our_price=599, desconto=499, inicio=ontem))
    # A oferta viva já mostra a promoção: o normal é o our_price gravado.
    amz.anuncio("promo", _corpo(oferta="499.00", our_price=599, desconto=499, fim=amanha))
    # Promoção vencida não conta.
    amz.anuncio("vencida", _corpo(oferta="599.00", our_price=599, desconto=499, fim=ontem))
    # Promoção que ainda não começou também não.
    amz.anuncio("futura", _corpo(oferta="599.00", our_price=599, desconto=499, inicio=amanha))
    vs = {s: _vinculo(sku_produto=s) for s in ("normal", "promo", "vencida", "futura")}
    out = await _ler(amz, list(vs.values()))
    esperado = LeituraAnuncio(
        preco=Decimal("599.00"),
        preco_promocional=Decimal("499.00"),
        ativo=True,
        motivo="em promoção",
    )
    assert out[vs["normal"].id] == esperado
    assert out[vs["promo"].id] == esperado
    for s in ("vencida", "futura"):
        assert out[vs[s].id] == LeituraAnuncio(preco=Decimal("599.00"), ativo=True), s


@pytest.mark.asyncio
async def test_nao_encontrado_e_outros_erros_por_anuncio(esperas):
    amz = AmazonFalsa()
    amz.anuncio("ruim", {"errors": [{"code": "InvalidInput"}]}, status=400)
    sumido, ruim = _vinculo(sku_produto="sumido"), _vinculo(sku_produto="ruim")
    sem_sku = _vinculo(external_id="  ")
    out = await _ler(amz, [sumido, ruim, sem_sku])
    assert out[sumido.id] == LeituraAnuncio(preco=None, motivo="anúncio não encontrado")
    assert out[ruim.id] == LeituraAnuncio(preco=None, motivo="erro ao ler (400)")
    assert out[sem_sku.id] == LeituraAnuncio(preco=None, motivo="vínculo sem SKU")
    assert len(amz.chamadas) == 2


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.asyncio
async def test_acesso_recusado_levanta(esperas, status: int):
    amz = AmazonFalsa()
    amz.anuncio("x1", {"errors": [{"code": "Unauthorized"}]}, status=status)
    with pytest.raises(conferencia_amazon.AcessoRecusadoError, match=str(status)):
        await _ler(amz, [_vinculo(sku_produto="x1")])


@pytest.mark.asyncio
async def test_credencial_incompleta_levanta(esperas):
    with pytest.raises(conferencia_amazon.AcessoRecusadoError):
        await _ler(AmazonFalsa(seller_id=""), [_vinculo(sku_produto="x1")])


@pytest.mark.asyncio
async def test_limite_de_taxa_espera_e_tenta_de_novo(esperas):
    amz = AmazonFalsa()
    amz.anuncio("x1", {"errors": [{"code": "QuotaExceeded"}]}, status=429)
    amz.anuncio("x1", {"errors": []}, status=503)
    amz.anuncio("x1", _corpo(oferta="56.00"))
    v = _vinculo(sku_produto="x1")
    out = await _ler(amz, [v])
    assert out[v.id].preco == Decimal("56.00")
    assert len(amz.chamadas) == 3
    assert esperas == list(conferencia_amazon.ESPERAS_S[:2])


@pytest.mark.asyncio
async def test_amazon_fora_do_ar_desiste_da_conta(esperas):
    """Anúncios seguidos sem resposta: para de insistir e marca o resto."""
    amz = AmazonFalsa()
    vs = [_vinculo(sku_produto=f"s{i}") for i in range(conferencia_amazon.FALHAS_SEGUIDAS_MAX + 2)]
    for v in vs:
        amz.anuncio(v.sku_do_produto, {"errors": []}, status=429)
    out = await _ler(amz, vs)
    tentativas = 1 + len(conferencia_amazon.ESPERAS_S)
    assert len(amz.chamadas) == conferencia_amazon.FALHAS_SEGUIDAS_MAX * tentativas
    lidos = vs[: conferencia_amazon.FALHAS_SEGUIDAS_MAX]
    assert {out[v.id].motivo for v in lidos} == {"limite de leituras da Amazon"}
    assert {out[v.id].motivo for v in vs[len(lidos) :]} == {conferencia_amazon.MOTIVO_DESISTIU}

    sem_rede = AmazonFalsa()
    sem_rede.sem_rede = True
    v = _vinculo(sku_produto="x1")
    assert (await _ler(sem_rede, [v]))[v.id] == LeituraAnuncio(preco=None, motivo="erro ao ler")


@pytest.mark.asyncio
async def test_mesmo_sku_le_uma_vez(esperas):
    amz = AmazonFalsa()
    amz.anuncio("x1", _corpo(oferta="56.00"))
    v1, v2 = _vinculo(sku_produto="x1"), _vinculo(sku_produto="x1", external_id="B0Y")
    out = await _ler(amz, [v1, v2])
    assert out[v1.id] == out[v2.id]
    assert len(amz.chamadas) == 1


def test_leitura_do_anuncio_de_outro_marketplace_e_sem_resumo():
    corpo = _corpo(oferta="56.00", status=None)
    corpo["offers"][0]["marketplaceId"] = "ATVPDKIKX0DER"
    leitura = conferencia_amazon.leitura_do_anuncio(corpo, MP, agora=datetime.now(UTC))
    assert leitura == LeituraAnuncio(preco=None, motivo=conferencia_amazon.MOTIVO_SEM_PRECO)
