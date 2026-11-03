"""Leitor do preço vivo no Mercado Livre (Conferência de preços, 02/11/2026).

O ML é SIMULADO (`_request` falso): nenhuma chamada de verdade.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.models import IntegrationPlatform, ProductLink
from app.services.pricing import conferencia_ml
from app.services.pricing.conferencia_leitores import LEITORES, LeituraAnuncio


def _vinculo(mlb: str, variacao: str | None = None, *, tipo: str | None = None) -> ProductLink:
    return ProductLink(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        integration_id=uuid.uuid4(),
        platform=IntegrationPlatform.ML,
        external_id=mlb,
        variation_id=variacao,
        listing_type=tipo,
    )


def _item(mlb: str, **campos: Any) -> dict[str, Any]:
    return {
        "id": mlb,
        "price": 100,
        "original_price": None,
        "status": "active",
        "variations": [],
        **campos,
    }


class MLFalso:
    """Multiget do ML: devolve os itens cadastrados e 404 para os outros."""

    def __init__(self) -> None:
        self.itens: dict[str, dict] = {}
        self.chamadas: list[dict[str, Any]] = []
        # Resposta fixa (status) da chamada N (0, 1, ...).
        self.status_da_chamada: dict[int, int] = {}
        self.sem_rede_na_chamada: set[int] = set()

    async def _request(self, method: str, path: str, *, params=None, json=None):
        n = len(self.chamadas)
        self.chamadas.append({"method": method, "path": path, "params": dict(params or {})})
        req = httpx.Request(method, f"https://ml.falso{path}")
        if n in self.sem_rede_na_chamada:
            raise httpx.ConnectError("sem rede", request=req)
        if n in self.status_da_chamada:
            return httpx.Response(self.status_da_chamada[n], json={"message": "x"}, request=req)
        corpo = []
        for mlb in params["ids"].split(","):
            if mlb in self.itens:
                corpo.append({"code": 200, "body": self.itens[mlb]})
            else:
                corpo.append(
                    {
                        "code": 404,
                        "body": {"message": f"Item with id {mlb} not found", "error": "not_found"},
                    }
                )
        return httpx.Response(200, json=corpo, request=req)


async def _ler(ml: MLFalso, vinculos: list[ProductLink]) -> dict[uuid.UUID, LeituraAnuncio]:
    return await conferencia_ml.ler_precos_ml(ml, vinculos)


def test_leitor_registrado_para_o_ml():
    assert LEITORES["ml"] is conferencia_ml.ler_precos_ml


@pytest.mark.asyncio
async def test_preco_do_anuncio_sem_variacao_e_status():
    ml = MLFalso()
    ml.itens["MLB1"] = _item("MLB1", price=59)
    ml.itens["MLB2"] = _item("MLB2", price=99.9, status="paused")
    v1, v2 = _vinculo("MLB1"), _vinculo("MLB2")
    out = await _ler(ml, [v1, v2])
    assert out[v1.id] == LeituraAnuncio(preco=Decimal("59.00"), ativo=True)
    assert out[v2.id] == LeituraAnuncio(preco=Decimal("99.90"), ativo=False)
    # Uma chamada só, com os campos que a conferência usa.
    assert ml.chamadas == [
        {
            "method": "GET",
            "path": "/items",
            "params": {"ids": "MLB1,MLB2", "attributes": conferencia_ml.CAMPOS},
        }
    ]


@pytest.mark.asyncio
async def test_preco_da_variacao_do_vinculo_e_variacao_que_sumiu():
    """O envio grava o MESMO preço em todas as variações: vale a do vínculo; se
    ela não existe mais, o preço do anúncio (com o aviso)."""
    ml = MLFalso()
    ml.itens["MLB1"] = _item(
        "MLB1",
        price=80,
        variations=[{"id": 111, "price": 80}, {"id": 222, "price": 85}],
    )
    com_var = _vinculo("MLB1", "222")
    sumiu = _vinculo("MLB1", "999")
    out = await _ler(ml, [com_var, sumiu])
    assert out[com_var.id].preco == Decimal("85.00")
    assert out[com_var.id].motivo is None
    assert out[sumiu.id].preco == Decimal("80.00")
    assert out[sumiu.id].motivo == conferencia_ml.MOTIVO_VARIACAO_SUMIU
    # O mesmo anúncio em dois vínculos: lido uma vez.
    assert ml.chamadas[0]["params"]["ids"] == "MLB1"


@pytest.mark.asyncio
async def test_promocao_compara_o_preco_original():
    ml = MLFalso()
    ml.itens["MLB1"] = _item("MLB1", price=89, original_price=99)
    ml.itens["MLB2"] = _item(
        "MLB2", price=70, original_price=90, variations=[{"id": 5, "price": 70}]
    )
    # original_price igual ao preço: não é promoção.
    ml.itens["MLB3"] = _item("MLB3", price=50, original_price=50)
    v1, v2, v3 = _vinculo("MLB1"), _vinculo("MLB2", "5"), _vinculo("MLB3")
    out = await _ler(ml, [v1, v2, v3])
    assert out[v1.id] == LeituraAnuncio(
        preco=Decimal("99.00"),
        preco_promocional=Decimal("89.00"),
        ativo=True,
        motivo="em promoção",
    )
    assert (out[v2.id].preco, out[v2.id].preco_promocional) == (Decimal("90.00"), Decimal("70.00"))
    assert out[v3.id] == LeituraAnuncio(preco=Decimal("50.00"), ativo=True)


@pytest.mark.asyncio
async def test_anuncio_que_nao_voltou_e_sem_preco():
    ml = MLFalso()
    ml.itens["MLB2"] = _item("MLB2", price=None)
    sumido, sem_preco, sem_id = _vinculo("MLB1"), _vinculo("MLB2"), _vinculo("  ")
    out = await _ler(ml, [sumido, sem_preco, sem_id])
    assert out[sumido.id] == LeituraAnuncio(preco=None, motivo="anúncio não encontrado")
    assert out[sem_preco.id].preco is None
    assert out[sem_preco.id].motivo == conferencia_ml.MOTIVO_SEM_PRECO
    assert out[sem_id.id].motivo == "anúncio não encontrado"
    assert ml.chamadas[0]["params"]["ids"] == "MLB1,MLB2"


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.asyncio
async def test_acesso_recusado_levanta(status: int):
    ml = MLFalso()
    ml.status_da_chamada[0] = status
    with pytest.raises(conferencia_ml.AcessoRecusadoError, match=str(status)):
        await _ler(ml, [_vinculo("MLB1")])


@pytest.mark.asyncio
async def test_de_20_em_20_e_erro_num_lote_marca_so_o_lote():
    ml = MLFalso()
    vinculos = [_vinculo(f"MLB{i}") for i in range(45)]
    for v in vinculos:
        ml.itens[v.external_id] = _item(v.external_id, price=10)
    ml.status_da_chamada[1] = 500  # o 2º lote (MLB20..MLB39)
    ml.sem_rede_na_chamada.add(2)  # o 3º lote (MLB40..MLB44)
    out = await _ler(ml, vinculos)
    assert [len(c["params"]["ids"].split(",")) for c in ml.chamadas] == [20, 20, 5]
    for i, v in enumerate(vinculos):
        if i < 20:
            assert out[v.id].preco == Decimal("10.00"), i
        else:
            assert out[v.id] == LeituraAnuncio(preco=None, motivo="erro ao ler"), i


@pytest.mark.asyncio
async def test_corpo_invalido_vira_erro_ao_ler():
    class CorpoQuebrado(MLFalso):
        async def _request(self, method, path, *, params=None, json=None):
            self.chamadas.append({"params": params})
            return httpx.Response(
                200, content=b"<html>", request=httpx.Request(method, "https://ml.falso")
            )

    ml = CorpoQuebrado()
    v = _vinculo("MLB1")
    out = await _ler(ml, [v])
    assert out[v.id] == LeituraAnuncio(preco=None, motivo="erro ao ler")


def test_leitura_do_item_sem_status():
    assert conferencia_ml.leitura_do_item({"price": 10}, None) == LeituraAnuncio(
        preco=Decimal("10.00")
    )


@pytest.mark.asyncio
async def test_anuncio_encerrado_ou_moderado_nao_confere():
    """O envio pula sem gravar (ml_item_closed / ml_item_forbidden): o preço
    congelado daria um (!) que nenhum envio corrige."""
    ml = MLFalso()
    ml.itens["MLB1"] = _item("MLB1", price=199, status="closed", sub_status=["deleted"])
    ml.itens["MLB2"] = _item("MLB2", price=199, status="under_review", sub_status=["forbidden"])
    ml.itens["MLB3"] = _item(
        "MLB3", price=199, status="under_review", sub_status=["waiting_for_patch"]
    )
    encerrado, moderado, em_revisao = _vinculo("MLB1"), _vinculo("MLB2"), _vinculo("MLB3")
    out = await _ler(ml, [encerrado, moderado, em_revisao])
    assert out[encerrado.id] == LeituraAnuncio(
        preco=None, ativo=False, motivo="anúncio encerrado — o envio pula"
    )
    assert out[moderado.id] == LeituraAnuncio(
        preco=None, ativo=False, motivo="removido por moderação — o envio pula"
    )
    # Em revisão sem moderação o envio grava: confere.
    assert out[em_revisao.id] == LeituraAnuncio(preco=Decimal("199.00"), ativo=False)
    # Os campos do multiget trazem o que a decisão usa.
    campos = ml.chamadas[0]["params"]["attributes"].split(",")
    assert {"status", "sub_status", "listing_type_id"} <= set(campos)
    # sub_status em texto solto também vale.
    solto = {"price": 10, "status": "under_review", "sub_status": "forbidden"}
    assert conferencia_ml.leitura_do_item(solto, None).preco is None


@pytest.mark.asyncio
async def test_tipo_trocado_no_ml_nao_confere_como_no_envio():
    """O resolvedor só põe na célula o vínculo do tipo da conta; se o anúncio
    vivo é de outro tipo, o envio pula (`_conferir_tipo_ml`)."""
    ml = MLFalso()
    ml.itens["MLB1"] = _item("MLB1", price=99, listing_type_id="gold_pro")
    # gold_premium é o nome antigo do gold_pro: o mesmo tipo.
    ml.itens["MLB2"] = _item("MLB2", price=99, listing_type_id="gold_premium")
    ml.itens["MLB3"] = _item("MLB3", price=99, listing_type_id="gold_pro")
    trocado = _vinculo("MLB1", tipo="gold_special")
    mesmo_tipo = _vinculo("MLB2", tipo="gold_pro")
    sem_tipo = _vinculo("MLB3")
    out = await _ler(ml, [trocado, mesmo_tipo, sem_tipo])
    assert out[trocado.id].preco is None
    assert "premium" in out[trocado.id].motivo and "clássico" in out[trocado.id].motivo
    assert out[mesmo_tipo.id].preco == Decimal("99.00")
    # Sem tipo no vínculo não há o que conferir (como o envio sem tipo na conta).
    assert out[sem_tipo.id].preco == Decimal("99.00")
