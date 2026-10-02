"""Clientes do Flex por anúncio (projeto Flex, etapa 3): ML e Shopee simulados com
respx — nenhuma chamada sai da máquina.

ML (`/flex/sites/MLB/items/{id}/v2`): 204 liga/desliga; 400 "already in flex"
é sucesso; 403 "item down" é inelegível; 404 indisponível; 409/429 tentar
depois (a escrita NÃO repete no mesmo segundo); 401 renova o token uma vez.
Shopee: lê `logistic_info` do get_item_base_info e escreve pelo update_item
SEMPRE com a lista completa de canais.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.config import get_settings
from app.services.marketplaces import flex_api
from app.services.marketplaces import ml as ml_mod
from app.services.marketplaces.ml import ML_API_BASE, MercadoLivreClient
from app.services.marketplaces.shopee import SHOPEE_LIVE_BASE, ShopeeClient

ITEM = "MLB123"
CAMINHO = f"/flex/sites/MLB/items/{ITEM}/v2"


def _ml(**extra: Any) -> MercadoLivreClient:
    creds = {
        "client_id": "x",
        "client_secret": "y",
        "access_token": "tok",
        "refresh_token": "ref",
        "user_id": 1,
        "expires_at": 9_999_999_999,
        **extra,
    }
    return MercadoLivreClient(creds)


@pytest.fixture
def sem_espera(monkeypatch):
    """O `_request` do ML espera 1 s, 2 s, 4 s entre as repetições de 429."""
    esperas: list[float] = []

    async def _dorme(s):
        esperas.append(s)

    monkeypatch.setattr(ml_mod.asyncio, "sleep", _dorme)
    return esperas


# ---- ML: ler -------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("valor", [True, False])
async def test_ml_ler_flex(valor):
    with respx.mock(base_url=ML_API_BASE) as router:
        rota = router.get(CAMINHO).mock(return_value=httpx.Response(200, json={"has_flex": valor}))
        r = await _ml().ler_flex(ITEM)
    assert (r.tipo, r.has_flex, r.status_http) == ("ok", valor, 200)
    assert rota.call_count == 1
    assert rota.calls[0].request.headers["authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_ml_ler_flex_resposta_estranha_nao_vira_desligado():
    with respx.mock(base_url=ML_API_BASE) as router:
        router.get(CAMINHO).mock(return_value=httpx.Response(200, json={}))
        r = await _ml().ler_flex(ITEM)
    assert (r.tipo, r.has_flex) == ("erro", None)


@pytest.mark.asyncio
async def test_ml_ler_flex_429_repete_com_espera_e_desiste(sem_espera):
    with respx.mock(base_url=ML_API_BASE) as router:
        rota = router.get(CAMINHO).mock(return_value=httpx.Response(429, json={}))
        r = await _ml().ler_flex(ITEM)
    assert (r.tipo, r.status_http) == ("repetir", 429)
    assert rota.call_count == 3  # leitura repete (é inofensiva)
    assert sem_espera == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_ml_ler_flex_rede_caida_e_tentar_depois():
    with respx.mock(base_url=ML_API_BASE) as router:
        router.get(CAMINHO).mock(side_effect=httpx.ConnectTimeout("timeout"))
        r = await _ml().ler_flex(ITEM)
    assert r.tipo == "repetir"


@pytest.mark.asyncio
async def test_ml_ler_flex_404_indisponivel():
    with respx.mock(base_url=ML_API_BASE) as router:
        router.get(CAMINHO).mock(
            return_value=httpx.Response(404, json={"message": "item not found"})
        )
        r = await _ml().ler_flex(ITEM)
    assert (r.tipo, r.status_http) == ("indisponivel", 404)


# ---- ML: ligar -----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "corpo", "tipo", "has_flex"),
    [
        (204, None, "ok", True),
        (400, {"message": "item is already in flex", "status": 400}, "ok", True),
        (400, {"message": "bad request"}, "erro", None),
        (403, {"message": "item down"}, "inelegivel", None),
        (403, {"message": "invalid access token"}, "sem_permissao", None),
        (404, {"message": "item not found"}, "indisponivel", None),
        (409, {"message": "can't activate item"}, "repetir", None),
        (429, {"message": "too many requests"}, "repetir", None),
        (500, {"message": "internal"}, "repetir", None),
    ],
)
async def test_ml_ligar_flex_classifica(status, corpo, tipo, has_flex):
    with respx.mock(base_url=ML_API_BASE) as router:
        rota = router.post(CAMINHO).mock(
            return_value=httpx.Response(status, json=corpo) if corpo else httpx.Response(status)
        )
        r = await _ml().ligar_flex(ITEM)
    assert (r.tipo, r.has_flex, r.status_http) == (tipo, has_flex, status)
    # A escrita NÃO repete no mesmo segundo (nem no 429/409/5xx): quem decide
    # quando tentar de novo é o motor, na próxima rodada.
    assert rota.call_count == 1


@pytest.mark.asyncio
async def test_ml_ligar_flex_401_renova_o_token_uma_vez():
    with respx.mock(base_url=ML_API_BASE) as router:
        token = router.post("/oauth/token").mock(
            return_value=httpx.Response(
                200, json={"access_token": "novo", "refresh_token": "ref2", "expires_in": 21600}
            )
        )
        rota = router.post(CAMINHO).mock(
            side_effect=[httpx.Response(401, json={"message": "expired"}), httpx.Response(204)]
        )
        cli = _ml()
        r = await cli.ligar_flex(ITEM)
    assert (r.tipo, r.has_flex) == ("ok", True)
    assert token.call_count == 1
    assert rota.call_count == 2
    assert rota.calls[1].request.headers["authorization"] == "Bearer novo"


@pytest.mark.asyncio
async def test_ml_ligar_flex_401_de_novo_e_sem_permissao():
    with respx.mock(base_url=ML_API_BASE) as router:
        router.post("/oauth/token").mock(
            return_value=httpx.Response(200, json={"access_token": "novo", "expires_in": 21600})
        )
        rota = router.post(CAMINHO).mock(
            return_value=httpx.Response(401, json={"message": "scope"})
        )
        r = await _ml().ligar_flex(ITEM)
    assert (r.tipo, r.status_http) == ("sem_permissao", 401)
    assert rota.call_count == 2


@pytest.mark.asyncio
async def test_ml_refresh_recusado_e_sem_permissao():
    with respx.mock(base_url=ML_API_BASE) as router:
        router.post("/oauth/token").mock(return_value=httpx.Response(400, json={"error": "x"}))
        router.post(CAMINHO).mock(return_value=httpx.Response(401, json={}))
        r = await _ml().ligar_flex(ITEM)
    assert r.tipo == "sem_permissao"
    assert "refresh" in r.detalhe


# ---- ML: desligar --------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "corpo", "tipo"),
    [
        (204, None, "ok"),
        (403, {"message": "item down"}, "inelegivel"),
        (404, {"message": "item not found"}, "indisponivel"),
        (409, {"message": "conflict"}, "repetir"),
        (429, {}, "repetir"),
    ],
)
async def test_ml_desligar_flex_classifica(status, corpo, tipo):
    with respx.mock(base_url=ML_API_BASE, assert_all_called=False) as router:
        rota = router.delete(CAMINHO).mock(
            return_value=httpx.Response(status, json=corpo) if corpo else httpx.Response(status)
        )
        leitura = router.get(CAMINHO).mock(
            return_value=httpx.Response(200, json={"has_flex": False})
        )
        r = await _ml().desligar_flex(ITEM)
    assert r.tipo == tipo
    assert r.has_flex is (False if tipo == "ok" else None)
    assert rota.call_count == 1
    assert leitura.call_count == 0  # só o 400 confere com leitura


@pytest.mark.asyncio
@pytest.mark.parametrize(("has_flex", "tipo"), [(False, "ok"), (True, "erro")])
async def test_ml_desligar_400_confere_com_leitura(has_flex, tipo):
    # O retorno do DELETE num anúncio já desligado não está documentado: o
    # GET decide.
    with respx.mock(base_url=ML_API_BASE) as router:
        router.delete(CAMINHO).mock(
            return_value=httpx.Response(400, json={"message": "item is not in flex"})
        )
        leitura = router.get(CAMINHO).mock(
            return_value=httpx.Response(200, json={"has_flex": has_flex})
        )
        r = await _ml().desligar_flex(ITEM)
    assert r.tipo == tipo
    assert leitura.call_count == 1


def test_resultado_texto():
    r = flex_api.ResultadoFlex("inelegivel", status_http=403, detalhe="item down")
    assert r.texto() == "403 item down"
    assert flex_api.ResultadoFlex("repetir").texto() == "repetir"


# ---- Shopee ----------------------------------------------------------------------


def _shopee(**extra: Any) -> ShopeeClient:
    return ShopeeClient(
        {
            "shop_id": 111,
            "partner_id": 9,
            "partner_key": "k",
            "access_token": "t",
            "refresh_token": "r",
            "expires_at": 9_999_999_999,
            **extra,
        }
    )


@pytest.fixture(autouse=True)
def _shopee_producao(monkeypatch):
    monkeypatch.setattr(get_settings(), "shopee_use_sandbox", False)


CANAIS = [
    {"logistic_id": 90001, "logistic_name": "Shopee Xpress", "enabled": True, "is_free": False,
     "shipping_fee": 0, "size_id": 0, "estimated_shipping_fee": 12.5},
    {"logistic_id": 90022, "logistic_name": "Shopee Entrega Direta", "enabled": False,
     "is_free": False},
    {"logistic_id": 90016, "logistic_name": "Retirada", "enabled": True, "is_free": True},
]


def _base_info(*itens):
    return {"error": "", "message": "", "response": {"item_list": list(itens)}}


@pytest.mark.asyncio
async def test_shopee_le_o_canal_flex_e_guarda_a_lista_inteira():
    ligado = [dict(c, enabled=True) if c["logistic_id"] == 90022 else c for c in CANAIS]
    with respx.mock(base_url=SHOPEE_LIVE_BASE) as router:
        rota = router.get("/api/v2/product/get_item_base_info").mock(
            return_value=httpx.Response(
                200,
                json=_base_info(
                    {"item_id": 1, "logistic_info": CANAIS},
                    {"item_id": 2, "logistic_info": ligado},
                    {"item_id": 3},  # sem logistic_info: não sei
                ),
            )
        )
        res = await _shopee().ler_canais_flex([1, "2", 3, "x"], {"90022"})
    assert rota.call_count == 1
    assert rota.calls[0].request.url.params["item_id_list"] == "1,2,3"
    assert (res["1"].tipo, res["1"].has_flex) == ("ok", False)
    assert (res["2"].tipo, res["2"].has_flex) == ("ok", True)
    assert len(res["1"].canais) == 3
    assert (res["3"].tipo, res["3"].has_flex) == ("erro", None)


@pytest.mark.asyncio
async def test_shopee_leitura_com_erro_marca_todos_do_lote():
    with respx.mock(base_url=SHOPEE_LIVE_BASE) as router:
        router.get("/api/v2/product/get_item_base_info").mock(
            return_value=httpx.Response(200, json={"error": "error_server", "message": "x"})
        )
        res = await _shopee().ler_canais_flex([1, 2], {"90022"})
    assert {r.tipo for r in res.values()} == {"erro"}


@pytest.mark.asyncio
async def test_shopee_escreve_com_a_lista_completa_de_canais():
    with respx.mock(base_url=SHOPEE_LIVE_BASE) as router:
        rota = router.post("/api/v2/product/update_item").mock(
            return_value=httpx.Response(200, json={"error": "", "response": {"item_id": 1}})
        )
        r = await _shopee().atualizar_canal_flex(1, CANAIS, ligar=True, canais_flex={"90022"})
    assert (r.tipo, r.has_flex) == ("ok", True)
    enviado = json.loads(rota.calls[0].request.content)
    assert enviado["item_id"] == 1
    # TODOS os canais, na mesma ordem, só o 90022 trocado; campos só de
    # leitura (nome, frete estimado) ficam de fora.
    assert enviado["logistic_info"] == [
        {"logistic_id": 90001, "enabled": True, "shipping_fee": 0, "size_id": 0, "is_free": False},
        {"logistic_id": 90022, "enabled": True, "is_free": False},
        {"logistic_id": 90016, "enabled": True, "is_free": True},
    ]


@pytest.mark.asyncio
async def test_shopee_sem_o_canal_flex_no_anuncio_nao_chama():
    sem_flex = [c for c in CANAIS if c["logistic_id"] != 90022]
    with respx.mock(base_url=SHOPEE_LIVE_BASE, assert_all_called=False) as router:
        rota = router.post("/api/v2/product/update_item")
        r = await _shopee().atualizar_canal_flex(1, sem_flex, ligar=True, canais_flex={"90022"})
        assert r.tipo == "inelegivel"
        # Desligar o que nem existe: já está como se quer.
        r = await _shopee().atualizar_canal_flex(1, sem_flex, ligar=False, canais_flex={"90022"})
        assert (r.tipo, r.has_flex) == ("ok", False)
        r = await _shopee().atualizar_canal_flex(1, [], ligar=False, canais_flex={"90022"})
        assert r.tipo == "inelegivel"
    assert rota.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resposta", "tipo"),
    [
        (httpx.Response(429, text="too many"), "repetir"),
        (httpx.Response(503, text="x"), "repetir"),
        (
            httpx.Response(
                200, json={"error": "error_invalid_price_for_logistic", "message": "price"}
            ),
            "inelegivel",
        ),
        (
            httpx.Response(200, json={"error": "error_param", "message": "logistic_info"}),
            "inelegivel",
        ),
        (httpx.Response(200, json={"error": "error_server", "message": "boom"}), "erro"),
    ],
)
async def test_shopee_escrita_classifica(resposta, tipo):
    with respx.mock(base_url=SHOPEE_LIVE_BASE) as router:
        rota = router.post("/api/v2/product/update_item").mock(return_value=resposta)
        r = await _shopee().atualizar_canal_flex(1, CANAIS, ligar=True, canais_flex={"90022"})
    assert r.tipo == tipo
    assert rota.call_count == 1


@pytest.mark.asyncio
async def test_shopee_escrita_renova_token_e_repete_uma_vez():
    with respx.mock(base_url=SHOPEE_LIVE_BASE) as router:
        auth = router.post("/api/v2/auth/access_token/get").mock(
            return_value=httpx.Response(
                200, json={"access_token": "t2", "refresh_token": "r2", "expire_in": 14400}
            )
        )
        rota = router.post("/api/v2/product/update_item").mock(
            side_effect=[
                httpx.Response(200, json={"error": "error_auth", "message": "expired"}),
                httpx.Response(200, json={"error": "", "response": {}}),
            ]
        )
        r = await _shopee().atualizar_canal_flex(1, CANAIS, ligar=False, canais_flex={"90022"})
    assert (r.tipo, r.has_flex) == ("ok", False)
    assert auth.call_count == 1
    assert rota.call_count == 2
    assert rota.calls[1].request.url.params["access_token"] == "t2"  # noqa: S105
