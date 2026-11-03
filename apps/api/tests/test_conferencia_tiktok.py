"""Conferência de preços da Tabela — o leitor do TikTok Shop (02/11/2026).

O envio da Tabela grava o preço da Promoção de Preço Fixo
(`activity_price_amount`), não o preço base. O leitor lê as promoções em
andamento (a mesma busca + detalhe do envio, o mesmo casamento produto/SKU):
SKU numa Preço Fixo → o preço dela; fora de promoção → o preço base do SKU
CERTO (nunca o primeiro da lista), com o motivo "sem promoção ativa". Valores
em reais; inteiro grande demais (centavos?) não confere.

Cliente falso: nada vai à rede. O método novo do cliente
(`promocoes_em_andamento`) é testado com o respx.
"""

from __future__ import annotations

import functools
import time
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx

from app.models import IntegrationPlatform, ProductLink
from app.services.marketplaces.tiktok import TIKTOK_BASE_URL, TikTokClient
from app.services.pricing.conferencia_leitores import LEITORES
from app.services.pricing.conferencia_tiktok import ler_precos_tiktok

pytestmark = pytest.mark.asyncio


def _vinculo(produto_id: str, sku_id: str | None) -> ProductLink:
    return ProductLink(
        id=uuid4(),
        external_id=produto_id,
        variation_id=sku_id,
        platform=IntegrationPlatform.TIKTOK,
    )


def _por_sku(
    activity_id: str, tipo: str, produto_id: str, precos: dict[str, Any]
) -> tuple[dict, dict]:
    """Promoção no nível VARIATION: o preço é de cada SKU."""
    skus = [
        {"id": sku, "activity_price": {"amount": preco, "currency": "BRL"}}
        for sku, preco in precos.items()
    ]
    return (
        {"id": activity_id, "activity_type": tipo, "status": "ONGOING"},
        {
            "activity_id": activity_id,
            "activity_type": tipo,
            "product_level": "VARIATION",
            "products": [{"id": produto_id, "skus": skus}],
        },
    )


def _por_produto(activity_id: str, tipo: str, produto_id: str, preco: str) -> tuple[dict, dict]:
    """Promoção no nível PRODUCT: um preço para o produto todo."""
    return (
        {"id": activity_id, "activity_type": tipo, "status": "ONGOING"},
        {
            "activity_id": activity_id,
            "activity_type": tipo,
            "product_level": "PRODUCT",
            "products": [
                {
                    "id": produto_id,
                    "activity_price": {"amount": preco, "currency": "BRL"},
                    "skus": [],
                }
            ],
        },
    )


def _produto(produto_id: str, precos: dict[str, Any], status: str = "ACTIVATE") -> dict:
    """Resposta do GET /product/202309/products/{id} (o formato real do SKU)."""
    skus = [
        {"id": sku, "seller_sku": f"s-{sku}", "price": {"currency": "BRL", "sale_price": preco}}
        for sku, preco in precos.items()
    ]
    return {
        "code": 0,
        "message": "Success",
        "data": {"id": produto_id, "status": status, "skus": skus},
    }


class ClienteFalso:
    """Os dois métodos que o leitor usa, com o registro das chamadas."""

    def __init__(
        self,
        atividades: list[tuple[dict, dict | None]] = (),
        produtos: dict[str, dict] | None = None,
        *,
        erro_busca: Exception | None = None,
        erro_produto: Exception | None = None,
    ) -> None:
        self.atividades = list(atividades)
        self.produtos = produtos or {}
        self.erro_busca = erro_busca
        self.erro_produto = erro_produto
        self.chamadas_produto: list[str] = []
        self.buscas = 0

    async def promocoes_em_andamento(self) -> list[tuple[dict, dict | None]]:
        self.buscas += 1
        if self.erro_busca is not None:
            raise self.erro_busca
        return self.atividades

    async def produto_bruto(self, product_id: str) -> dict:
        self.chamadas_produto.append(product_id)
        if self.erro_produto is not None:
            raise self.erro_produto
        return self.produtos.get(
            product_id, {"code": 12052048, "message": "product not found", "data": None}
        )


async def test_registrado_como_leitor_do_tiktok() -> None:
    assert LEITORES["tiktok"] is ler_precos_tiktok
    assert IntegrationPlatform.TIKTOK.value == "tiktok"


async def test_sku_em_promocao_de_preco_fixo_le_o_preco_dela() -> None:
    cliente = ClienteFalso([_por_sku("A1", "FIXED_PRICE", "P1", {"S1": "299.00", "S2": "310.00"})])
    s1, s2 = _vinculo("P1", "S1"), _vinculo("P1", "S2")

    leituras = await ler_precos_tiktok(cliente, [s1, s2])

    assert leituras[s1.id].preco == Decimal("299.00")
    assert leituras[s2.id].preco == Decimal("310.00")
    assert leituras[s1.id].preco_promocional is None
    assert leituras[s1.id].motivo is None
    # Em promoção não precisa do preço base: nenhum GET de produto.
    assert cliente.chamadas_produto == []


async def test_preco_fixo_no_nivel_do_produto_vale_para_todos_os_skus() -> None:
    cliente = ClienteFalso([_por_produto("A2", "FIXED_PRICE", "P2", "150.00")])
    vinculos = [_vinculo("P2", "S1"), _vinculo("P2", "S2"), _vinculo("P2", None)]

    leituras = await ler_precos_tiktok(cliente, vinculos)

    assert {leituras[v.id].preco for v in vinculos} == {Decimal("150.00")}


async def test_fora_de_promocao_le_o_preco_base_do_sku_certo() -> None:
    cliente = ClienteFalso(produtos={"P3": _produto("P3", {"S1": "100.00", "S2": "150.00"})})
    v = _vinculo("P3", "S2")

    leitura = (await ler_precos_tiktok(cliente, [v]))[v.id]

    assert leitura.preco == Decimal("150.00")
    assert leitura.motivo == "sem promoção ativa"
    assert leitura.ativo is True


async def test_sku_inexistente_nao_pega_o_primeiro_da_lista() -> None:
    cliente = ClienteFalso(produtos={"P3": _produto("P3", {"S1": "100.00", "S2": "150.00"})})
    v = _vinculo("P3", "S9")

    leitura = (await ler_precos_tiktok(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert leitura.motivo == "SKU não encontrado no anúncio"


async def test_vinculo_sem_sku_so_serve_no_produto_de_sku_unico() -> None:
    cliente = ClienteFalso(
        produtos={
            "UNICO": _produto("UNICO", {"S1": "75.50"}),
            "VARIOS": _produto("VARIOS", {"S1": "75.50", "S2": "80.00"}),
        }
    )
    unico, varios = _vinculo("UNICO", None), _vinculo("VARIOS", "")

    leituras = await ler_precos_tiktok(cliente, [unico, varios])

    assert leituras[unico.id].preco == Decimal("75.50")
    assert leituras[varios.id].preco is None
    assert "vários" in leituras[varios.id].motivo


async def test_um_get_por_produto_para_os_skus_fora_de_promocao() -> None:
    cliente = ClienteFalso(produtos={"P3": _produto("P3", {"S1": "100.00", "S2": "150.00"})})
    s1, s2 = _vinculo("P3", "S1"), _vinculo("P3", "S2")

    leituras = await ler_precos_tiktok(cliente, [s1, s2])

    assert cliente.chamadas_produto == ["P3"]
    assert leituras[s1.id].preco == Decimal("100.00")
    assert leituras[s2.id].preco == Decimal("150.00")


async def test_valores_em_reais_e_centavos_ambiguos_nao_conferem() -> None:
    cliente = ClienteFalso(
        [
            # Texto com casas é reais: não divide por 100.
            _por_sku("A1", "FIXED_PRICE", "P1", {"S1": "1226.7"}),
            # Inteiro gigante pode ser centavos: não adivinha.
            _por_sku("A2", "FIXED_PRICE", "P2", {"S1": "29990000"}),
        ],
        # Inteiro pequeno no preço base: reais.
        produtos={"P3": _produto("P3", {"S1": 89})},
    )
    reais, centavos, base = _vinculo("P1", "S1"), _vinculo("P2", "S1"), _vinculo("P3", "S1")

    leituras = await ler_precos_tiktok(cliente, [reais, centavos, base])

    assert leituras[reais.id].preco == Decimal("1226.70")
    assert leituras[centavos.id].preco is None
    assert "leitura não suportada" in leituras[centavos.id].motivo
    assert leituras[base.id].preco == Decimal("89.00")


async def test_moeda_diferente_nao_confere() -> None:
    resumo, detalhe = _por_sku("A1", "FIXED_PRICE", "P1", {"S1": "299.00"})
    detalhe["products"][0]["skus"][0]["activity_price"]["currency"] = "USD"
    v = _vinculo("P1", "S1")

    leitura = (await ler_precos_tiktok(ClienteFalso([(resumo, detalhe)]), [v]))[v.id]

    assert leitura.preco is None
    assert "moeda USD" in leitura.motivo


async def test_preco_da_promocao_com_o_nome_da_escrita() -> None:
    resumo, detalhe = _por_sku("A1", "FIXED_PRICE", "P1", {})
    detalhe["products"][0]["skus"] = [{"id": "S1", "activity_price_amount": "199.90"}]
    v = _vinculo("P1", "S1")

    leitura = (await ler_precos_tiktok(ClienteFalso([(resumo, detalhe)]), [v]))[v.id]

    assert leitura.preco == Decimal("199.90")


async def test_promocao_sem_o_preco_nao_confere() -> None:
    resumo, detalhe = _por_sku("A1", "FIXED_PRICE", "P1", {})
    detalhe["products"][0]["skus"] = [{"id": "S1"}]
    v = _vinculo("P1", "S1")

    leitura = (await ler_precos_tiktok(ClienteFalso([(resumo, detalhe)]), [v]))[v.id]

    assert leitura.preco is None
    assert "leitura não suportada" in leitura.motivo


async def test_so_em_oferta_relampago_nao_confere() -> None:
    cliente = ClienteFalso([_por_sku("F1", "FLASHSALE", "P1", {"S1": "80.00"})])
    v = _vinculo("P1", "S1")

    leitura = (await ler_precos_tiktok(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert leitura.preco_promocional == Decimal("80.00")
    assert "Oferta Relâmpago" in leitura.motivo
    assert cliente.chamadas_produto == []


async def test_oferta_relampago_por_cima_do_preco_fixo_confere_o_fixo() -> None:
    cliente = ClienteFalso(
        [
            _por_sku("F1", "FLASHSALE", "P1", {"S1": "250.00"}),
            _por_sku("A1", "FIXED_PRICE", "P1", {"S1": "299.00"}),
        ]
    )
    v = _vinculo("P1", "S1")

    leitura = (await ler_precos_tiktok(cliente, [v]))[v.id]

    assert leitura.preco == Decimal("299.00")
    assert leitura.preco_promocional == Decimal("250.00")
    assert "Oferta Relâmpago" in leitura.motivo


async def test_vale_a_primeira_preco_fixo_da_busca_como_no_envio() -> None:
    cliente = ClienteFalso(
        [
            _por_sku("A1", "FIXED_PRICE", "P1", {"S1": "299.00"}),
            _por_sku("A2", "FIXED_PRICE", "P1", {"S1": "199.00"}),
        ]
    )
    v = _vinculo("P1", "S1")

    assert (await ler_precos_tiktok(cliente, [v]))[v.id].preco == Decimal("299.00")


async def test_desconto_percentual_nao_suportado() -> None:
    resumo, detalhe = _por_sku("D1", "DIRECT_DISCOUNT", "P1", {})
    detalhe["products"][0]["skus"] = [{"id": "S1", "discount": "10"}]
    cliente = ClienteFalso([(resumo, detalhe)])
    v = _vinculo("P1", "S1")

    leitura = (await ler_precos_tiktok(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert "DIRECT_DISCOUNT" in leitura.motivo
    assert "leitura não suportada" in leitura.motivo
    assert cliente.chamadas_produto == []


async def test_promocao_no_nivel_do_sku_exige_o_sku_como_no_envio() -> None:
    # Vínculo sem SKU não casa com promoção por SKU: cai no preço base.
    cliente = ClienteFalso(
        [_por_sku("A1", "FIXED_PRICE", "P1", {"S1": "299.00"})],
        produtos={"P1": _produto("P1", {"S1": "350.00"})},
    )
    v = _vinculo("P1", None)

    leitura = (await ler_precos_tiktok(cliente, [v]))[v.id]

    assert leitura.preco == Decimal("350.00")
    assert leitura.motivo == "sem promoção ativa"


async def test_promocao_sem_detalhe_nao_deixa_conferir_o_preco_base() -> None:
    cliente = ClienteFalso(
        [
            _por_sku("A1", "FIXED_PRICE", "P1", {"S1": "299.00"}),
            ({"id": "A2", "activity_type": "FIXED_PRICE"}, None),
        ],
        produtos={"P4": _produto("P4", {"S1": "100.00"})},
    )
    na_lida, fora = _vinculo("P1", "S1"), _vinculo("P4", "S1")

    leituras = await ler_precos_tiktok(cliente, [na_lida, fora])

    assert leituras[na_lida.id].preco == Decimal("299.00")
    # P4 pode estar na A2, que não veio: o preço base seria um (!) falso.
    assert leituras[fora.id].preco is None
    assert "não lida" in leituras[fora.id].motivo
    assert cliente.chamadas_produto == []


async def test_busca_das_promocoes_que_falha_levanta() -> None:
    cliente = ClienteFalso(
        erro_busca=RuntimeError("TikTok search-activities error: Expired credentials")
    )
    with pytest.raises(RuntimeError, match="search-activities"):
        await ler_precos_tiktok(cliente, [_vinculo("P1", "S1")])


async def test_produto_nao_lido_vira_motivo() -> None:
    cliente = ClienteFalso()
    v = _vinculo("P404", "S1")

    leitura = (await ler_precos_tiktok(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert "produto não lido" in leitura.motivo and "12052048" in leitura.motivo


async def test_recusa_da_conta_na_leitura_do_produto_levanta() -> None:
    cliente = ClienteFalso(produtos={"P1": {"code": 401, "message": "Unauthorized"}})
    with pytest.raises(RuntimeError, match="401"):
        await ler_precos_tiktok(cliente, [_vinculo("P1", "S1")])


async def test_erro_de_rede_na_leitura_do_produto_vira_motivo() -> None:
    cliente = ClienteFalso(erro_produto=httpx.ConnectError("conexão caiu"))
    v = _vinculo("P1", "S1")

    leitura = (await ler_precos_tiktok(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert leitura.motivo.startswith("falha ao ler o produto")


async def test_produto_excluido_e_desativado() -> None:
    cliente = ClienteFalso(
        produtos={
            "APAGADO": _produto("APAGADO", {"S1": "100.00"}, status="DELETED"),
            "PAUSADO": _produto("PAUSADO", {"S1": "100.00"}, status="SELLER_DEACTIVATED"),
        }
    )
    apagado, pausado = _vinculo("APAGADO", "S1"), _vinculo("PAUSADO", "S1")

    leituras = await ler_precos_tiktok(cliente, [apagado, pausado])

    assert leituras[apagado.id].preco is None
    assert leituras[apagado.id].ativo is False
    assert leituras[pausado.id].preco == Decimal("100.00")
    assert leituras[pausado.id].ativo is False


async def test_vinculo_sem_produto_nao_vai_ao_tiktok() -> None:
    cliente = ClienteFalso()
    v = _vinculo("", "S1")

    leitura = (await ler_precos_tiktok(cliente, [v]))[v.id]

    assert leitura.preco is None
    assert leitura.motivo == "vínculo sem o ID do produto"
    # Nada para ler: nem a busca das promoções vai.
    assert cliente.buscas == 0
    assert cliente.chamadas_produto == []


# ---------------------------------------------------------------- o método novo do cliente


def _creds() -> dict[str, Any]:
    return {
        "app_key": "ak",
        "app_secret": "as",
        "access_token": "tok",
        "refresh_token": "ref",
        "shop_cipher": "cipher",
        # validade longe: o `_ensure_fresh_token` não renova
        "token_expires_at": int(time.time()) + 3600,
    }


def _busca(atividades: list[dict]) -> httpx.Response:
    return httpx.Response(
        200, json={"code": 0, "data": {"activities": atividades, "next_page_token": ""}}
    )


async def test_promocoes_em_andamento_le_a_busca_e_o_detalhe_de_cada_uma() -> None:
    cliente = TikTokClient(_creds())
    with respx.mock(base_url=TIKTOK_BASE_URL) as router:
        router.post("/promotion/202309/activities/search").mock(
            return_value=_busca(
                [
                    {"id": "A1", "activity_type": "FIXED_PRICE"},
                    {"id": "D1", "activity_type": "DIRECT_DISCOUNT"},
                ]
            )
        )
        router.get("/promotion/202309/activities/A1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"activity_id": "A1", "product_level": "VARIATION", "products": []},
                },
            )
        )
        router.get("/promotion/202309/activities/D1").mock(
            return_value=httpx.Response(200, json={"code": 36009999, "message": "falhou"})
        )
        saida = await cliente.promocoes_em_andamento()

    assert [resumo["id"] for resumo, _ in saida] == ["A1", "D1"]
    assert saida[0][1]["activity_id"] == "A1"
    # Detalhe que não veio fica None (quem lê não confere o que pode estar nela).
    assert saida[1][1] is None


def _detalhe_ok(activity_id: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "code": 0,
            "data": {"activity_id": activity_id, "product_level": "VARIATION", "products": []},
        },
    )


async def test_detalhe_que_falha_nao_derruba_a_loja_e_tenta_de_novo_uma_vez() -> None:
    """Timeout, 429 ou 5xx no detalhe de UMA promoção: uma nova tentativa e,
    se não vier, None — sem levantar e sem guardar a falha no cache."""
    cliente = TikTokClient(_creds())
    with respx.mock(base_url=TIKTOK_BASE_URL) as router:
        router.post("/promotion/202309/activities/search").mock(
            return_value=_busca(
                [
                    {"id": "A1", "activity_type": "FIXED_PRICE"},
                    {"id": "D1", "activity_type": "DIRECT_DISCOUNT"},
                    {"id": "A2", "activity_type": "FIXED_PRICE"},
                    {"id": "A3", "activity_type": "FIXED_PRICE"},
                ]
            )
        )
        router.get("/promotion/202309/activities/A1").mock(return_value=_detalhe_ok("A1"))
        lento = router.get("/promotion/202309/activities/D1").mock(side_effect=httpx.ReadTimeout)
        piscou = router.get("/promotion/202309/activities/A2").mock(
            side_effect=[httpx.Response(503, text="indisponível"), _detalhe_ok("A2")]
        )
        limite = router.get("/promotion/202309/activities/A3").mock(
            return_value=httpx.Response(429, text="devagar")
        )
        saida = await cliente.promocoes_em_andamento(espera_s=0)

    detalhes = {resumo["id"]: detalhe for resumo, detalhe in saida}
    assert list(detalhes) == ["A1", "D1", "A2", "A3"]
    assert detalhes["A1"]["activity_id"] == "A1"
    assert detalhes["A2"]["activity_id"] == "A2"
    assert detalhes["D1"] is None and detalhes["A3"] is None
    assert (lento.call_count, piscou.call_count, limite.call_count) == (2, 2, 2)
    # A falha não fica no cache: o envio que usar este cliente lê de novo.
    assert "D1" not in cliente._promo_activity_cache
    assert "A3" not in cliente._promo_activity_cache
    assert cliente._promo_activity_cache["A2"]["activity_id"] == "A2"


async def test_leitor_segue_quando_o_detalhe_de_uma_promocao_estoura() -> None:
    """O detalhe da DIRECT_DISCOUNT estoura: o SKU da Preço Fixo é lido, o de
    fora fica "não lida" e a loja não vira erro_conta."""
    cliente = TikTokClient(_creds())
    cliente.promocoes_em_andamento = functools.partial(cliente.promocoes_em_andamento, espera_s=0)
    resumo, detalhe = _por_sku("A1", "FIXED_PRICE", "P1", {"S1": "199.00"})
    na_fixa, fora = _vinculo("P1", "S1"), _vinculo("P2", "S1")
    with respx.mock(base_url=TIKTOK_BASE_URL) as router:
        router.post("/promotion/202309/activities/search").mock(
            return_value=_busca([resumo, {"id": "A2", "activity_type": "DIRECT_DISCOUNT"}])
        )
        router.get("/promotion/202309/activities/A1").mock(
            return_value=httpx.Response(200, json={"code": 0, "data": detalhe})
        )
        router.get("/promotion/202309/activities/A2").mock(side_effect=httpx.ReadTimeout)
        leituras = await ler_precos_tiktok(cliente, [na_fixa, fora])

    assert leituras[na_fixa.id].preco == Decimal("199.00")
    assert leituras[fora.id].preco is None
    assert "não lida" in leituras[fora.id].motivo


async def test_promocoes_em_andamento_levanta_se_a_busca_falha() -> None:
    cliente = TikTokClient(_creds())
    with respx.mock(base_url=TIKTOK_BASE_URL) as router:
        router.post("/promotion/202309/activities/search").mock(
            return_value=httpx.Response(200, json={"code": 105005, "message": "no permission"})
        )
        with pytest.raises(RuntimeError, match="no permission"):
            await cliente.promocoes_em_andamento()


async def test_leitor_com_o_cliente_de_verdade_sem_rede() -> None:
    """A fiação inteira: busca → detalhe → GET do produto fora de promoção."""
    cliente = TikTokClient(_creds())
    resumo, detalhe = _por_sku("A1", "FIXED_PRICE", "P1", {"S1": "299.00"})
    em_promocao, fora = _vinculo("P1", "S1"), _vinculo("P2", "S2")
    with respx.mock(base_url=TIKTOK_BASE_URL) as router:
        router.post("/promotion/202309/activities/search").mock(return_value=_busca([resumo]))
        router.get("/promotion/202309/activities/A1").mock(
            return_value=httpx.Response(200, json={"code": 0, "data": detalhe})
        )
        router.get("/product/202309/products/P2").mock(
            return_value=httpx.Response(200, json=_produto("P2", {"S1": "90.00", "S2": "120.00"}))
        )
        leituras = await ler_precos_tiktok(cliente, [em_promocao, fora])

    assert leituras[em_promocao.id].preco == Decimal("299.00")
    assert leituras[fora.id].preco == Decimal("120.00")
    assert leituras[fora.id].motivo == "sem promoção ativa"
