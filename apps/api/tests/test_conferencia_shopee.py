"""Conferência de preços da Tabela — o leitor da Shopee (02/11/2026).

O envio da Tabela grava o preço do "Desconto de Loja", não o preço base: o
comparável é o `current_price` (do `get_item_base_info` em lotes de 50 no
anúncio sem variação; do `get_model_list` na variação). Oferta Relâmpago ou
campanha por cima trocam o preço da vitrine — aí não confere (melhor "não
conferido" do que um (!) falso).

Cliente falso: nada vai à rede. O método novo do cliente
(`promocoes_dos_anuncios`) é testado com o respx.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx

from app.models import IntegrationPlatform, ProductLink
from app.services.marketplaces.shopee import ShopeeClient
from app.services.pricing import conferencia_shopee
from app.services.pricing.conferencia_leitores import LEITORES
from app.services.pricing.conferencia_shopee import ler_precos_shopee

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def esperas(monkeypatch) -> list[float]:
    """As pausas do leitor (limite, folga entre variações), sem esperar."""
    feitas: list[float] = []

    async def _registra(segundos: float) -> None:
        feitas.append(segundos)

    monkeypatch.setattr(conferencia_shopee, "_esperar", _registra)
    return feitas


DESCONTO = {
    "promotion_type": "Discount Promotions",
    "promotion_id": 7,
    "promotion_staging": "ongoing",
}


def _promo(tipo: str, **extra: Any) -> dict:
    return {"promotion_type": tipo, "promotion_id": 9, **extra}


def _vinculo(item_id: int | str, modelo: str | None = None) -> ProductLink:
    return ProductLink(
        id=uuid4(),
        external_id=str(item_id),
        variation_id=modelo,
        platform=IntegrationPlatform.SHOPEE,
    )


def _item(
    item_id: int,
    atual: float | None = 99.9,
    original: float | None = 129.9,
    *,
    status: str = "NORMAL",
) -> dict:
    return {
        "item_id": item_id,
        "item_status": status,
        "has_model": False,
        "price_info": [{"currency": "BRL", "current_price": atual, "original_price": original}],
    }


def _item_com_variacao(item_id: int, status: str = "NORMAL") -> dict:
    # Anúncio com variação: o get_item_base_info não traz preço.
    return {"item_id": item_id, "item_status": status, "has_model": True}


def _modelo(model_id: int, atual: float, original: float) -> dict:
    return {
        "model_id": model_id,
        "model_sku": f"sku-{model_id}",
        "price_info": [{"current_price": atual, "original_price": original}],
    }


class ClienteFalso:
    """Os três métodos que o leitor usa, com o registro das chamadas."""

    def __init__(
        self,
        itens: list[dict] = (),
        *,
        modelos: dict[int, list[dict]] | None = None,
        promocoes: dict[int, list[dict]] | None = None,
        recusas: dict[int, str] | None = None,
        erros_base: list[Exception | None] | None = None,
        erro_modelos: Exception | None = None,
        erro_promocoes: Exception | None = None,
        erros_modelos: list[Exception | None] | None = None,
        erros_promocoes: list[Exception | None] | None = None,
    ) -> None:
        self.itens = {int(i["item_id"]): i for i in itens}
        self.modelos = modelos or {}
        self.promocoes = promocoes or {}
        self.recusas = recusas or {}
        # `erros_*`: um por chamada, na ordem; `erro_*`: em todas.
        self.erros_base = list(erros_base or [])
        self.erro_modelos = erro_modelos
        self.erro_promocoes = erro_promocoes
        self.erros_modelos = list(erros_modelos or [])
        self.erros_promocoes = list(erros_promocoes or [])
        self.chamadas_base: list[list[int]] = []
        self.chamadas_modelos: list[int] = []
        self.chamadas_promocoes: list[list[int]] = []

    async def get_item_base_info(self, item_ids: list[int]) -> list[dict]:
        lote = [int(x) for x in item_ids]
        self.chamadas_base.append(lote)
        erro = self.erros_base.pop(0) if self.erros_base else None
        if erro is not None:
            raise erro
        return [self.itens[i] for i in lote if i in self.itens]

    async def get_model_list(self, item_id: int) -> dict:
        self.chamadas_modelos.append(int(item_id))
        erro = self.erros_modelos.pop(0) if self.erros_modelos else self.erro_modelos
        if erro is not None:
            raise erro
        return {"tier_variation": [], "model": self.modelos.get(int(item_id), [])}

    async def promocoes_dos_anuncios(
        self, item_ids: list[int]
    ) -> tuple[dict[int, list[dict]], dict[int, str]]:
        lote = [int(x) for x in item_ids]
        self.chamadas_promocoes.append(lote)
        erro = self.erros_promocoes.pop(0) if self.erros_promocoes else self.erro_promocoes
        if erro is not None:
            raise erro
        lidas = {i: self.promocoes[i] for i in lote if i in self.promocoes}
        return lidas, {i: r for i, r in self.recusas.items() if i in lote}


async def test_registrado_como_leitor_da_shopee() -> None:
    assert LEITORES["shopee"] is ler_precos_shopee
    assert IntegrationPlatform.SHOPEE.value == "shopee"


async def test_anuncio_sem_variacao_em_lotes_de_50_duas_chamadas() -> None:
    ids = list(range(1, 61))
    cliente = ClienteFalso([_item(i) for i in ids], promocoes={i: [DESCONTO] for i in ids})
    vinculos = [_vinculo(i) for i in ids]

    leituras = await ler_precos_shopee(cliente, vinculos)

    assert [len(lote) for lote in cliente.chamadas_base] == [50, 10]
    assert cliente.chamadas_base[0] == ids[:50] and cliente.chamadas_base[1] == ids[50:]
    assert [len(lote) for lote in cliente.chamadas_promocoes] == [50, 10]
    assert cliente.chamadas_modelos == []
    for v in vinculos:
        assert leituras[v.id].preco == Decimal("99.90")
        assert leituras[v.id].preco_promocional is None
        assert leituras[v.id].motivo is None
        assert leituras[v.id].ativo is True


async def test_variacao_le_o_preco_do_model_list_uma_vez_por_anuncio() -> None:
    cliente = ClienteFalso(
        [_item_com_variacao(10)],
        modelos={10: [_modelo(111, 49.9, 79.9), _modelo(222, 59.0, 79.9)]},
        promocoes={10: [DESCONTO]},
    )
    v222, v111 = _vinculo(10, "222"), _vinculo(10, "111")

    leituras = await ler_precos_shopee(cliente, [v222, v111])

    assert cliente.chamadas_modelos == [10]
    assert leituras[v222.id].preco == Decimal("59.00")
    assert leituras[v111.id].preco == Decimal("49.90")


async def test_sem_desconto_ativo_confere_o_preco_base_com_motivo() -> None:
    cliente = ClienteFalso([_item(5, atual=120.0, original=120.0)])
    v = _vinculo(5, "0")

    leituras = await ler_precos_shopee(cliente, [v])

    assert leituras[v.id].preco == Decimal("120.00")
    assert leituras[v.id].motivo == "sem desconto ativo"
    # Sem promoção valendo, nem pergunta pelas promoções.
    assert cliente.chamadas_promocoes == []


async def test_anuncio_nao_encontrado_nao_confere() -> None:
    cliente = ClienteFalso([_item(1)], promocoes={1: [DESCONTO]})
    achado, sumido = _vinculo(1), _vinculo(2)

    leituras = await ler_precos_shopee(cliente, [achado, sumido])

    assert leituras[achado.id].preco == Decimal("99.90")
    assert leituras[sumido.id].preco is None
    assert "não encontrado" in leituras[sumido.id].motivo


async def test_erro_de_autenticacao_da_loja_levanta() -> None:
    cliente = ClienteFalso(
        [_item(1)],
        erros_base=[RuntimeError("shopee_item_base_info error_auth: Invalid access_token.")],
    )
    with pytest.raises(RuntimeError, match="error_auth"):
        await ler_precos_shopee(cliente, [_vinculo(1)])


async def test_renovacao_do_token_recusada_levanta() -> None:
    recusa = httpx.HTTPStatusError(
        "403 Forbidden",
        request=httpx.Request(
            "POST", "https://partner.shopeemobile.com/api/v2/auth/access_token/get"
        ),
        response=httpx.Response(403),
    )
    cliente = ClienteFalso([_item(1)], erros_base=[recusa])
    with pytest.raises(httpx.HTTPStatusError):
        await ler_precos_shopee(cliente, [_vinculo(1)])


async def test_erro_pontual_de_um_lote_vira_motivo_e_o_resto_confere() -> None:
    ids = list(range(1, 61))
    cliente = ClienteFalso(
        [_item(i) for i in ids],
        promocoes={i: [DESCONTO] for i in ids},
        erros_base=[RuntimeError("shopee_item_base_info error_server: system busy"), None],
    )
    vinculos = {i: _vinculo(i) for i in ids}

    leituras = await ler_precos_shopee(cliente, list(vinculos.values()))

    assert leituras[vinculos[1].id].preco is None
    assert leituras[vinculos[1].id].motivo.startswith("falha ao ler o anúncio")
    assert "error_server" in leituras[vinculos[50].id].motivo
    assert leituras[vinculos[51].id].preco == Decimal("99.90")


async def test_nenhum_lote_lido_levanta() -> None:
    cliente = ClienteFalso(
        [_item(1)], erros_base=[RuntimeError("shopee_item_base_info error_server: busy")]
    )
    with pytest.raises(RuntimeError, match="error_server"):
        await ler_precos_shopee(cliente, [_vinculo(1)])


async def test_oferta_relampago_por_cima_do_desconto_nao_confere() -> None:
    cliente = ClienteFalso(
        [_item(1, atual=79.0)],
        promocoes={1: [DESCONTO, _promo("Flash Sale", promotion_staging="ongoing")]},
    )
    v = _vinculo(1)

    leitura = (await ler_precos_shopee(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert leitura.preco_promocional == Decimal("79.00")
    assert "Oferta Relâmpago" in leitura.motivo


async def test_oferta_relampago_agendada_nao_atrapalha() -> None:
    cliente = ClienteFalso(
        [_item(1)],
        promocoes={1: [DESCONTO, _promo("In ShopFlash Sale", promotion_staging="upcoming")]},
    )
    v = _vinculo(1)

    leitura = (await ler_precos_shopee(cliente, [v]))[v.id]

    assert leitura.preco == Decimal("99.90")
    assert leitura.motivo is None


async def test_oferta_relampago_que_ja_acabou_pelo_horario_nao_atrapalha() -> None:
    agora = int(time.time())
    cliente = ClienteFalso(
        [_item(1)],
        promocoes={
            1: [DESCONTO, _promo("Flash Sale", start_time=agora - 7200, end_time=agora - 3600)]
        },
    )
    v = _vinculo(1)

    assert (await ler_precos_shopee(cliente, [v]))[v.id].preco == Decimal("99.90")


async def test_oferta_relampago_de_outra_variacao_so_tira_aquela() -> None:
    cliente = ClienteFalso(
        [_item_com_variacao(10)],
        modelos={10: [_modelo(111, 39.9, 79.9), _modelo(222, 59.0, 79.9)]},
        promocoes={10: [DESCONTO, _promo("Flash Sale", model_id=111, promotion_staging="ongoing")]},
    )
    v111, v222 = _vinculo(10, "111"), _vinculo(10, "222")

    leituras = await ler_precos_shopee(cliente, [v111, v222])

    assert leituras[v111.id].preco is None
    assert leituras[v111.id].preco_promocional == Decimal("39.90")
    assert leituras[v222.id].preco == Decimal("59.00")


async def test_campanha_por_cima_do_desconto_nao_confere() -> None:
    # Sem etapa nem horário: na dúvida, conta como valendo.
    cliente = ClienteFalso([_item(1)], promocoes={1: [DESCONTO, _promo("Campaign")]})
    v = _vinculo(1)

    leitura = (await ler_precos_shopee(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert "campanha" in leitura.motivo


async def test_abaixo_do_original_sem_desconto_de_loja_nao_confere() -> None:
    # "Leve mais" não mexe no preço: quem baixou o preço não é o desconto.
    cliente = ClienteFalso([_item(1)], promocoes={1: [_promo("Bundle Deal")]})
    v = _vinculo(1)

    leitura = (await ler_precos_shopee(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert "sem Desconto de Loja" in leitura.motivo


async def test_desconto_com_promocao_que_nao_mexe_no_preco_confere() -> None:
    cliente = ClienteFalso([_item(1)], promocoes={1: [DESCONTO, _promo("Add-on Discount")]})
    v = _vinculo(1)

    assert (await ler_precos_shopee(cliente, [v]))[v.id].preco == Decimal("99.90")


async def test_promocao_nao_reconhecida_nao_confere() -> None:
    cliente = ClienteFalso([_item(1)], promocoes={1: [DESCONTO, _promo("Lucky Deal")]})
    v = _vinculo(1)

    leitura = (await ler_precos_shopee(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert "não reconhecida (lucky deal)" in leitura.motivo


async def test_promocoes_que_nao_vieram_nao_conferem() -> None:
    cliente = ClienteFalso([_item(1), _item(2)], recusas={1: "item not found"})
    v1, v2 = _vinculo(1), _vinculo(2)

    leituras = await ler_precos_shopee(cliente, [v1, v2])

    assert leituras[v1.id].preco is None
    assert leituras[v1.id].motivo == "promoções não lidas (item not found)"
    assert leituras[v2.id].preco is None
    assert leituras[v2.id].motivo == "promoções não lidas"


async def test_erro_pontual_nas_promocoes_vira_motivo() -> None:
    cliente = ClienteFalso(
        [_item(1)], erro_promocoes=RuntimeError("shopee_item_promotion error_server: busy")
    )
    v = _vinculo(1)

    leitura = (await ler_precos_shopee(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert leitura.preco_promocional == Decimal("99.90")
    assert leitura.motivo.startswith("promoções não lidas: ")


async def test_erro_de_autenticacao_nas_promocoes_levanta() -> None:
    cliente = ClienteFalso(
        [_item(1)],
        erro_promocoes=RuntimeError("shopee_item_promotion error_token_expired: expired"),
    )
    with pytest.raises(RuntimeError, match="error_token_expired"):
        await ler_precos_shopee(cliente, [_vinculo(1)])


async def test_sem_preco_original_e_sem_promocao_confere_como_sem_desconto() -> None:
    cliente = ClienteFalso([_item(1, original=None)], promocoes={1: []})
    v = _vinculo(1)

    leitura = (await ler_precos_shopee(cliente, [v]))[v.id]

    assert leitura.preco == Decimal("99.90")
    assert leitura.motivo == "sem desconto ativo"


async def test_variacao_que_nao_existe_mais_nao_confere() -> None:
    cliente = ClienteFalso([_item_com_variacao(10)], modelos={10: [_modelo(111, 49.9, 79.9)]})
    v = _vinculo(10, "333")

    leitura = (await ler_precos_shopee(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert leitura.motivo == "variação não encontrada no anúncio"


async def test_vinculo_sem_variacao_num_anuncio_com_variacoes_nao_confere() -> None:
    cliente = ClienteFalso([_item_com_variacao(10)])
    v = _vinculo(10, None)

    leitura = (await ler_precos_shopee(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert "sem a variação" in leitura.motivo
    assert cliente.chamadas_modelos == []


async def test_vinculo_com_variacao_num_anuncio_sem_variacoes_nao_confere() -> None:
    cliente = ClienteFalso([_item(1)])
    v = _vinculo(1, "555")

    leitura = (await ler_precos_shopee(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert "variação não encontrada" in leitura.motivo


async def test_falha_pontual_nas_variacoes_vira_motivo(esperas: list[float]) -> None:
    cliente = ClienteFalso(
        [_item_com_variacao(10), _item(2)],
        promocoes={2: [DESCONTO]},
        erro_modelos=RuntimeError("shopee_model_list error_server: busy"),
    )
    variacao, simples = _vinculo(10, "111"), _vinculo(2)

    leituras = await ler_precos_shopee(cliente, [variacao, simples])

    assert leituras[variacao.id].preco is None
    assert leituras[variacao.id].motivo.startswith("falha ao ler as variações")
    assert leituras[simples.id].preco == Decimal("99.90")
    # Erro que não é de limite: sem nova tentativa.
    assert cliente.chamadas_modelos == [10]
    assert esperas == []


async def test_limite_da_shopee_espera_e_tenta_de_novo_nas_tres_chamadas(
    esperas: list[float],
) -> None:
    cliente = ClienteFalso(
        [_item_com_variacao(10), _item(2)],
        modelos={10: [_modelo(111, 49.9, 79.9)]},
        promocoes={2: [DESCONTO], 10: [DESCONTO]},
        erros_base=[RuntimeError("shopee_item_base_info HTTP 429: <html>slow down</html>")],
        erros_modelos=[RuntimeError("shopee_model_list error_busy: The system is busy.")],
        erros_promocoes=[RuntimeError("shopee_item_promotion error_rate_limit: too fast")],
    )
    variacao, simples = _vinculo(10, "111"), _vinculo(2)

    leituras = await ler_precos_shopee(cliente, [variacao, simples])

    assert leituras[variacao.id].preco == Decimal("49.90")
    assert leituras[simples.id].preco == Decimal("99.90")
    assert len(cliente.chamadas_base) == len(cliente.chamadas_promocoes) == 2
    assert cliente.chamadas_modelos == [10, 10]
    assert esperas == [1.0, 1.0, 1.0]


async def test_limite_que_nao_passa_vira_motivo_depois_de_1_2_4_s(esperas: list[float]) -> None:
    cliente = ClienteFalso(
        [_item_com_variacao(10)],
        erro_modelos=RuntimeError("shopee_model_list error_busy: The system is busy."),
    )
    v = _vinculo(10, "111")

    leitura = (await ler_precos_shopee(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert leitura.motivo.startswith("falha ao ler as variações")
    assert "error_busy" in leitura.motivo
    assert cliente.chamadas_modelos == [10] * 4
    assert esperas == list(conferencia_shopee.ESPERAS_LIMITE_S)


async def test_folga_entre_as_leituras_de_variacao(esperas: list[float]) -> None:
    ids = (10, 20, 30)
    cliente = ClienteFalso(
        [_item_com_variacao(i) for i in ids],
        modelos={i: [_modelo(i + 1, 50.0, 80.0)] for i in ids},
        promocoes={i: [DESCONTO] for i in ids},
    )
    vinculos = [_vinculo(i, str(i + 1)) for i in ids]

    leituras = await ler_precos_shopee(cliente, vinculos)

    assert cliente.chamadas_modelos == list(ids)
    # Uma folga ENTRE as chamadas: a primeira não espera.
    assert esperas == [conferencia_shopee.INTERVALO_MODELOS_S] * (len(ids) - 1)
    assert all(leituras[v.id].preco == Decimal("50.00") for v in vinculos)


async def test_anuncio_excluido_e_pausado() -> None:
    cliente = ClienteFalso(
        [_item(1, status="DELETED"), _item(2, status="UNLIST")], promocoes={2: [DESCONTO]}
    )
    excluido, pausado = _vinculo(1), _vinculo(2)

    leituras = await ler_precos_shopee(cliente, [excluido, pausado])

    assert leituras[excluido.id].preco is None
    assert leituras[excluido.id].ativo is False
    # Pausado ainda tem o preço do desconto: confere, e diz que não está ativo.
    assert leituras[pausado.id].preco == Decimal("99.90")
    assert leituras[pausado.id].ativo is False


async def test_id_do_anuncio_invalido_nao_vai_a_shopee() -> None:
    cliente = ClienteFalso()
    v = _vinculo("abc")

    leitura = (await ler_precos_shopee(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert leitura.motivo == "ID do anúncio inválido"
    assert cliente.chamadas_base == []


# ---------------------------------------------------------------- o método novo do cliente


def _cliente_shopee() -> ShopeeClient:
    return ShopeeClient(
        {
            "shop_id": 99,
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_at": int(time.time()) + 3600,
            "partner_id": 1,
            "partner_key": "chave",
        }
    )


async def test_promocoes_dos_anuncios_em_lote() -> None:
    cliente = _cliente_shopee()
    with respx.mock(base_url=cliente._base) as router:
        rota = router.get("/api/v2/product/get_item_promotion").mock(
            return_value=httpx.Response(
                200,
                json={
                    "error": "",
                    "response": {
                        "success_list": [
                            {"item_id": 1, "promotion": [DESCONTO]},
                            {"item_id": 2, "promotion": None},
                        ],
                        "failure_list": [{"item_id": 3, "failed_reason": "item not found"}],
                    },
                },
            )
        )
        lidas, recusas = await cliente.promocoes_dos_anuncios([1, "2", 3])

    assert rota.calls.last.request.url.params["item_id_list"] == "1,2,3"
    assert lidas == {1: [DESCONTO], 2: []}
    assert recusas == {3: "item not found"}


async def test_promocoes_dos_anuncios_levanta_em_erro_de_api() -> None:
    cliente = _cliente_shopee()
    with respx.mock(base_url=cliente._base) as router:
        router.get("/api/v2/product/get_item_promotion").mock(
            return_value=httpx.Response(200, json={"error": "error_server", "message": "busy"})
        )
        with pytest.raises(RuntimeError, match="error_server"):
            await cliente.promocoes_dos_anuncios([1])


async def test_promocoes_dos_anuncios_sem_ids_nao_chama() -> None:
    cliente = _cliente_shopee()
    with respx.mock(base_url=cliente._base, assert_all_called=False) as router:
        assert await cliente.promocoes_dos_anuncios([]) == ({}, {})
        assert len(router.calls) == 0


async def test_leitor_com_o_cliente_de_verdade_sem_rede() -> None:
    """A fiação inteira: get_item_base_info → get_model_list → get_item_promotion."""
    cliente = _cliente_shopee()
    simples, variacao = _vinculo(1), _vinculo(10, "222")
    with respx.mock(base_url=cliente._base) as router:
        base = router.get("/api/v2/product/get_item_base_info").mock(
            return_value=httpx.Response(
                200,
                json={"error": "", "response": {"item_list": [_item(1), _item_com_variacao(10)]}},
            )
        )
        router.get("/api/v2/product/get_model_list").mock(
            return_value=httpx.Response(
                200, json={"error": "", "response": {"model": [_modelo(222, 59.0, 79.9)]}}
            )
        )
        promocoes = router.get("/api/v2/product/get_item_promotion").mock(
            return_value=httpx.Response(
                200,
                json={
                    "error": "",
                    "response": {
                        "success_list": [
                            {"item_id": 1, "promotion": [DESCONTO]},
                            {"item_id": 10, "promotion": [DESCONTO]},
                        ]
                    },
                },
            )
        )
        leituras = await ler_precos_shopee(cliente, [simples, variacao])

    assert base.calls.last.request.url.params["item_id_list"] == "1,10"
    assert promocoes.calls.last.request.url.params["item_id_list"] == "1,10"
    assert leituras[simples.id].preco == Decimal("99.90")
    assert leituras[variacao.id].preco == Decimal("59.00")


async def test_429_do_cliente_de_verdade_espera_e_tenta_de_novo(esperas: list[float]) -> None:
    """O 429 fora do JSON vira "HTTP 429" no texto do erro (`_corpo_json`)."""
    cliente = _cliente_shopee()
    v = _vinculo(1)
    with respx.mock(base_url=cliente._base) as router:
        base = router.get("/api/v2/product/get_item_base_info").mock(
            side_effect=[
                httpx.Response(429, text="<html>slow down</html>"),
                httpx.Response(200, json={"error": "", "response": {"item_list": [_item(1)]}}),
            ]
        )
        router.get("/api/v2/product/get_item_promotion").mock(
            return_value=httpx.Response(
                200,
                json={
                    "error": "",
                    "response": {"success_list": [{"item_id": 1, "promotion": [DESCONTO]}]},
                },
            )
        )
        leituras = await ler_precos_shopee(cliente, [v])

    assert base.call_count == 2
    assert leituras[v.id].preco == Decimal("99.90")
    assert esperas == [1.0]
