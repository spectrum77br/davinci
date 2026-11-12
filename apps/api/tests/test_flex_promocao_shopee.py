"""Shopee recusa ligar o Flex de anúncio em promoção (12/11/2026, 1º teste:
58202772455 — "Cannot update logistic info because the product has
promotion"). O motor lê QUAL promoção (get_item_promotion, só leitura) e a
tela diz em português: Desconto da loja, Oferta Relâmpago, campanha.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services import flex_motor, flex_textos
from app.services.marketplaces import flex_api
from app.services.marketplaces.flex_api import ResultadoFlex

RECUSA = "product.error_busi: Cannot update logistic info because the product has promotion"
FIM = int(datetime(2026, 11, 30, 2, 59, tzinfo=UTC).timestamp())  # 29/11 23:59 em Brasília
INICIO = int(datetime(2026, 11, 15, 13, 0, tzinfo=UTC).timestamp())  # 15/11 10:00


class _ShopeeEmPromocao:
    def __init__(self, promocoes: list[dict] | None, *, falha: bool = False) -> None:
        self.promocoes = promocoes
        self.falha = falha
        self.lidas: list[list] = []

    async def ler_canais_flex(self, ids, canais_flex):
        canais = (
            {"logistic_id": 90001, "enabled": True, "is_free": False},
            {"logistic_id": 90022, "enabled": False, "is_free": False},
        )
        return {str(i): ResultadoFlex(flex_api.OK, has_flex=False, canais=canais) for i in ids}

    async def atualizar_canal_flex(self, item_id, logistic_info, *, ligar, canais_flex):
        return ResultadoFlex(flex_api.INELEGIVEL, status_http=200, detalhe=RECUSA)

    async def promocoes_dos_anuncios(self, ids):
        self.lidas.append(list(ids))
        if self.falha:
            raise RuntimeError("HTTP 500")
        return {int(ids[0]): self.promocoes or []}, {}


def test_nomes_das_promocoes():
    desconto = {"promotion_type": "Discount Promotions", "promotion_staging": "ongoing"}
    assert flex_textos.promocoes_shopee(
        [
            {**desconto, "end_time": FIM},
            # Duas variações na mesma promoção: aparece uma vez.
            {**desconto, "end_time": FIM},
            {"promotion_type": "Flash Sale", "promotion_staging": "upcoming", "start_time": INICIO},
            {"promotion_type": "Campaign"},
            {"promotion_type": "Coisa Nova"},
        ]
    ) == [
        "Desconto da loja até 29/11 23:59",
        "Oferta Relâmpago (agendada, começa 15/11 10:00)",
        "Campanha da Shopee",
        "Coisa Nova",
    ]


@pytest.mark.asyncio
async def test_recusa_por_promocao_diz_qual():
    cli = _ShopeeEmPromocao(
        [{"promotion_type": "Flash Sale", "promotion_staging": "ongoing", "end_time": FIM}]
    )
    res = await flex_motor._chamar(cli, "shopee", "58202772455", ligar=True)
    assert res.tipo == flex_api.INELEGIVEL  # continua recusa: não tenta de novo sozinho
    assert res.texto() == "200 em promoção na Shopee: Oferta Relâmpago até 29/11 23:59"
    assert cli.lidas == [["58202772455"]]
    claro = flex_textos.motivo_claro(f"a plataforma recusou ligar: {res.texto()}")
    assert claro == (
        "A Shopee não deixa mudar o envio de anúncio em promoção — este está em Oferta "
        "Relâmpago até 29/11 23:59. Tire o anúncio da promoção no Seller Center (ou espere "
        "ela acabar) e clique em Tentar novamente."
    )


@pytest.mark.asyncio
async def test_leitura_da_promocao_falhou():
    res = await flex_motor._chamar(_ShopeeEmPromocao(None, falha=True), "shopee", "1", ligar=True)
    assert res.texto() == "200 em promoção na Shopee: não deu para ler qual"


@pytest.mark.asyncio
async def test_outra_recusa_fica_como_veio():
    res = ResultadoFlex(flex_api.INELEGIVEL, status_http=200, detalhe="product.error_busi: outra")
    cli = _ShopeeEmPromocao([])
    assert await flex_motor._qual_promocao(cli, "1", res) is res
    assert cli.lidas == []


def test_recusa_antiga_sem_o_nome_ganha_frase():
    claro = flex_textos.motivo_claro(f"a plataforma recusou ligar: 200 {RECUSA}")
    assert claro.startswith("A Shopee não deixa mudar o envio de anúncio em promoção (")
    assert "Tentar novamente" in claro
