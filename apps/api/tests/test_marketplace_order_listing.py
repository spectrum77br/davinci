# ruff: noqa: S105, S106 — page_token / next_token são cursores de paginação, não senhas
"""Listagem de pedidos por período nos clients de marketplace + conferência
por `numeroLoja` no Bling — a base do Vigia de importação multi-plataforma
(Ouvidoria › Robôs).

Mockado via respx — não bate em API real. Trava o que foi validado em
produção em 21/09/2026: os parâmetros que cada API espera, o formato cru
que cada método devolve (Shopee `response`, TikTok `data`, Amazon `payload`,
Bling `data[]`), a paginação (cursor/more, next_page_token, NextToken), a
cota da Amazon (pausa entre páginas + 1 retentativa em 429), os lotes de 20
do Bling e — importante pro vigia — que erro de API LEVANTA em vez de virar
"zero pedidos".
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest
import respx

from app.services.marketplaces import amazon as amazon_mod
from app.services.marketplaces.amazon import SP_API_BASE_NA, AmazonClient
from app.services.marketplaces.bling import BLING_API_BASE, BlingClient
from app.services.marketplaces.shopee import ShopeeClient
from app.services.marketplaces.tiktok import TIKTOK_BASE_URL, TikTokClient

# ---------------------------------------------------------------- credenciais


def _shopee_creds() -> dict[str, Any]:
    return {
        "shop_id": 99,
        "access_token": "tok",
        "refresh_token": "ref",
        "expires_at": int(time.time()) + 3600,
    }


def _tiktok_creds() -> dict[str, Any]:
    return {
        "app_key": "ak",
        "app_secret": "as",
        "access_token": "tok",
        "refresh_token": "ref",
        "shop_cipher": "cipher",
        # expiração longe: _ensure_fresh_token nunca dispara refresh
        "token_expires_at": int(time.time()) + 3600,
    }


BR_MARKETPLACE = "A2Q3Y263D00KWC"


def _amz_creds() -> dict[str, Any]:
    return {
        "lwa_app_id": "amzn1.app.x",
        "lwa_client_secret": "secret",
        "refresh_token": "Atzr|...",
        "seller_id": "ASELLER123",
        "marketplace_id": BR_MARKETPLACE,
        "access_token": "Atza|tok",
        "expires_at": int(time.time()) + 3600,
    }


def _bling_client() -> BlingClient:
    return BlingClient(
        {
            "access_token": "tok",
            "refresh_token": "ref",
            "client_id": "cid",
            "client_secret": "csec",
            "expires_at": 99999999999,  # longe: evita refresh
        }
    )


@pytest.fixture
def sem_redis(monkeypatch):
    """O `_request` do Bling passa pelo token-bucket no Redis; aqui não há
    Redis, então o slot é liberado na hora (mesmo truque do
    test_bling_client_create)."""
    from app.services.marketplaces import bling as bling_mod

    async def _slot():
        return None

    monkeypatch.setattr(bling_mod, "_acquire_bling_rate_slot", _slot)


@pytest.fixture
def sono_gravado(monkeypatch):
    """Troca o `asyncio.sleep` que o client da Amazon usa por um que só
    anota quanto dormiria (cede o loop com sleep(0)) — o teste não espera
    de verdade os 1 s/2 s e ainda confere que a pausa aconteceu."""
    dormiu: list[float] = []
    real_sleep = asyncio.sleep

    async def _fake(segundos, *args, **kwargs):
        dormiu.append(float(segundos))
        await real_sleep(0)

    monkeypatch.setattr(amazon_mod.asyncio, "sleep", _fake)
    return dormiu


# ---------------------------------------------------------------- Shopee


def _shopee_pagina(order_list: list[dict], *, more: bool, next_cursor: str = "") -> dict:
    return {
        "error": "",
        "message": "",
        "request_id": "r",
        "response": {"order_list": order_list, "more": more, "next_cursor": next_cursor},
    }


@pytest.mark.asyncio
async def test_shopee_get_order_list_params_e_response_cru() -> None:
    client = ShopeeClient(_shopee_creds())
    ini, fim = 1_758_400_000, 1_758_486_400
    with respx.mock(base_url=client._base) as router:
        route = router.get("/api/v2/order/get_order_list").mock(
            return_value=httpx.Response(
                200,
                json=_shopee_pagina(
                    [{"order_sn": "2509A1", "order_status": "READY_TO_SHIP"}],
                    more=True, next_cursor="c2",
                ),
            )
        )
        resp = await client.get_order_list(
            time_from=ini, time_to=fim, order_status="READY_TO_SHIP"
        )

    assert resp["order_list"] == [{"order_sn": "2509A1", "order_status": "READY_TO_SHIP"}]
    assert resp["more"] is True and resp["next_cursor"] == "c2"
    q = route.calls.last.request.url.params
    assert q["time_range_field"] == "create_time"
    assert q["time_from"] == str(ini) and q["time_to"] == str(fim)
    assert q["page_size"] == "100"
    assert q["cursor"] == ""
    assert q["response_optional_fields"] == "order_status"
    assert q["order_status"] == "READY_TO_SHIP"
    # assinatura da Shopee vai junto, como em toda chamada do _request
    assert q["shop_id"] == "99" and "sign" in q


@pytest.mark.asyncio
async def test_shopee_get_order_list_recusa_janela_maior_que_15_dias() -> None:
    client = ShopeeClient(_shopee_creds())
    with pytest.raises(ValueError):
        await client.get_order_list(time_from=0, time_to=16 * 24 * 3600)


@pytest.mark.asyncio
async def test_shopee_get_order_list_erro_de_api_levanta() -> None:
    """Conta sem acesso (token vencido, permissão) não pode virar "zero
    pedidos": o vigia fecharia as ocorrências da conta como sumidas."""
    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        router.get("/api/v2/order/get_order_list").mock(
            return_value=httpx.Response(
                200, json={"error": "error_param", "message": "wrong params"}
            )
        )
        with pytest.raises(RuntimeError, match="error_param"):
            await client.get_order_list(time_from=0, time_to=3600)


@pytest.mark.asyncio
async def test_shopee_iter_orders_segue_cursor_ate_more_false() -> None:
    client = ShopeeClient(_shopee_creds())
    with respx.mock(base_url=client._base) as router:
        route = router.get("/api/v2/order/get_order_list").mock(
            side_effect=[
                httpx.Response(200, json=_shopee_pagina(
                    [{"order_sn": "A", "order_status": "READY_TO_SHIP"},
                     {"order_sn": "B", "order_status": "UNPAID"}],
                    more=True, next_cursor="c2",
                )),
                httpx.Response(200, json=_shopee_pagina(
                    [{"order_sn": "C", "order_status": "COMPLETED"}], more=False,
                )),
            ]
        )
        vistos = [o async for o in client.iter_orders(time_from=0, time_to=3600)]

    assert [o["order_sn"] for o in vistos] == ["A", "B", "C"]
    assert route.call_count == 2
    assert route.calls[0].request.url.params["cursor"] == ""
    assert route.calls[1].request.url.params["cursor"] == "c2"


@pytest.mark.asyncio
async def test_shopee_iter_orders_fatia_janela_maior_que_15_dias() -> None:
    """Quem chama pede 40 dias; o iterador fatia em pedaços que a Shopee
    aceita (≤ 15 dias), encostados e sem buraco."""
    client = ShopeeClient(_shopee_creds())
    ini = 1_700_000_000
    fim = ini + 40 * 24 * 3600
    with respx.mock(base_url=client._base) as router:
        route = router.get("/api/v2/order/get_order_list").mock(
            return_value=httpx.Response(200, json=_shopee_pagina([], more=False))
        )
        _ = [o async for o in client.iter_orders(time_from=ini, time_to=fim)]

    janelas = [
        (int(c.request.url.params["time_from"]), int(c.request.url.params["time_to"]))
        for c in route.calls
    ]
    assert janelas[0][0] == ini and janelas[-1][1] == fim
    for de, ate in janelas:
        assert ate - de <= 15 * 24 * 3600
    for (_, ate_anterior), (de, _) in zip(janelas, janelas[1:], strict=False):
        assert de == ate_anterior + 1  # encostadas, sem repetir a borda


# ---------------------------------------------------------------- TikTok


def _tiktok_pagina(orders: list[dict], *, next_page_token: str = "") -> dict:
    return {
        "code": 0,
        "message": "Success",
        "data": {"orders": orders, "next_page_token": next_page_token, "total_count": len(orders)},
    }


@pytest.mark.asyncio
async def test_tiktok_search_orders_body_query_e_data_cru() -> None:
    client = TikTokClient(_tiktok_creds())
    ge, lt = 1_758_400_000, 1_758_486_400
    pedido = {
        "id": "586188801488290979", "status": "AWAITING_SHIPMENT",
        "create_time": ge + 10, "paid_time": ge + 20,
    }
    with respx.mock(base_url=TIKTOK_BASE_URL) as router:
        route = router.post("/order/202309/orders/search").mock(
            return_value=httpx.Response(200, json=_tiktok_pagina([pedido], next_page_token="t2"))
        )
        data = await client.search_orders(
            create_time_ge=ge, create_time_lt=lt, order_status="AWAITING_SHIPMENT"
        )

    assert data["orders"] == [pedido]
    assert data["next_page_token"] == "t2"
    req = route.calls.last.request
    body = req.content
    assert b'"create_time_ge":' + str(ge).encode() in body
    assert b'"create_time_lt":' + str(lt).encode() in body
    assert b'"order_status":"AWAITING_SHIPMENT"' in body
    q = req.url.params
    assert q["page_size"] == "100"
    assert q["sort_field"] == "create_time" and q["sort_order"] == "DESC"
    assert "page_token" not in q
    assert q["shop_cipher"] == "cipher" and "sign" in q


@pytest.mark.asyncio
async def test_tiktok_search_orders_sem_order_status_nao_manda_o_campo() -> None:
    client = TikTokClient(_tiktok_creds())
    with respx.mock(base_url=TIKTOK_BASE_URL) as router:
        route = router.post("/order/202309/orders/search").mock(
            return_value=httpx.Response(200, json=_tiktok_pagina([]))
        )
        await client.search_orders(create_time_ge=1, create_time_lt=2, page_token="t9")
    assert b"order_status" not in route.calls.last.request.content
    assert route.calls.last.request.url.params["page_token"] == "t9"


@pytest.mark.asyncio
async def test_tiktok_search_orders_erro_de_api_levanta() -> None:
    client = TikTokClient(_tiktok_creds())
    with respx.mock(base_url=TIKTOK_BASE_URL) as router:
        router.post("/order/202309/orders/search").mock(
            return_value=httpx.Response(
                200, json={"code": 105005, "message": "no permission", "data": {}}
            )
        )
        with pytest.raises(RuntimeError, match="105005"):
            await client.search_orders(create_time_ge=1, create_time_lt=2)


@pytest.mark.asyncio
async def test_tiktok_search_orders_http_fora_do_200_levanta() -> None:
    client = TikTokClient(_tiktok_creds())
    with respx.mock(base_url=TIKTOK_BASE_URL) as router:
        router.post("/order/202309/orders/search").mock(
            return_value=httpx.Response(401, text="unauthorized")
        )
        with pytest.raises(RuntimeError, match="401"):
            await client.search_orders(create_time_ge=1, create_time_lt=2)


@pytest.mark.asyncio
async def test_tiktok_iter_orders_segue_next_page_token() -> None:
    client = TikTokClient(_tiktok_creds())
    with respx.mock(base_url=TIKTOK_BASE_URL) as router:
        route = router.post("/order/202309/orders/search").mock(
            side_effect=[
                httpx.Response(200, json=_tiktok_pagina(
                    [{"id": "1", "status": "ON_HOLD"}, {"id": "2", "status": "AWAITING_SHIPMENT"}],
                    next_page_token="t2",
                )),
                httpx.Response(200, json=_tiktok_pagina([{"id": "3", "status": "CANCELLED"}])),
            ]
        )
        vistos = [o async for o in client.iter_orders(create_time_ge=1, create_time_lt=2)]

    assert [o["id"] for o in vistos] == ["1", "2", "3"]
    assert route.call_count == 2
    assert "page_token" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["page_token"] == "t2"


# ---------------------------------------------------------------- Amazon


def _amz_pagina(orders: list[dict], *, next_token: str | None = None) -> dict:
    payload: dict[str, Any] = {"Orders": orders}
    if next_token:
        payload["NextToken"] = next_token
    return {"payload": payload}


@pytest.mark.asyncio
async def test_amazon_get_orders_params_e_payload_cru() -> None:
    client = AmazonClient(_amz_creds())
    pedido = {
        "AmazonOrderId": "701-3967231-6921832", "OrderStatus": "Unshipped",
        "PurchaseDate": "2026-09-21T12:00:00Z",
    }
    with respx.mock(base_url=SP_API_BASE_NA) as router:
        route = router.get("/orders/v0/orders").mock(
            return_value=httpx.Response(200, json=_amz_pagina([pedido], next_token="N2"))
        )
        payload = await client.get_orders(
            created_after="2026-09-18T00:00:00Z",
            order_statuses=["Unshipped", "PartiallyShipped", "Shipped"],
        )

    assert payload["Orders"] == [pedido]
    assert payload["NextToken"] == "N2"
    req = route.calls.last.request
    q = req.url.params
    assert q["MarketplaceIds"] == BR_MARKETPLACE
    assert q["CreatedAfter"] == "2026-09-18T00:00:00Z"
    assert q["OrderStatuses"] == "Unshipped,PartiallyShipped,Shipped"
    assert q["MaxResultsPerPage"] == "100"
    assert "NextToken" not in q
    assert req.headers["x-amz-access-token"] == "Atza|tok"


@pytest.mark.asyncio
async def test_amazon_get_orders_com_next_token_manda_so_token_e_marketplace() -> None:
    """Com NextToken a Amazon ignora os filtros; MarketplaceIds continua
    obrigatório em toda chamada — os demais não vão."""
    client = AmazonClient(_amz_creds())
    with respx.mock(base_url=SP_API_BASE_NA) as router:
        route = router.get("/orders/v0/orders").mock(
            return_value=httpx.Response(200, json=_amz_pagina([]))
        )
        await client.get_orders(
            created_after="2026-09-18T00:00:00Z", order_statuses=["Shipped"], next_token="N2"
        )
    q = route.calls.last.request.url.params
    assert q["NextToken"] == "N2"
    assert q["MarketplaceIds"] == BR_MARKETPLACE
    assert "CreatedAfter" not in q and "OrderStatuses" not in q and "MaxResultsPerPage" not in q


@pytest.mark.asyncio
async def test_amazon_get_orders_429_espera_2s_e_tenta_uma_vez(sono_gravado) -> None:
    client = AmazonClient(_amz_creds())
    with respx.mock(base_url=SP_API_BASE_NA) as router:
        route = router.get("/orders/v0/orders").mock(
            side_effect=[
                httpx.Response(429, json={"errors": [{"code": "QuotaExceeded"}]}),
                httpx.Response(
                    200, json=_amz_pagina([{"AmazonOrderId": "X", "OrderStatus": "Shipped"}])
                ),
            ]
        )
        payload = await client.get_orders(created_after="2026-09-18T00:00:00Z")

    assert [o["AmazonOrderId"] for o in payload["Orders"]] == ["X"]
    assert route.call_count == 2
    assert sono_gravado == [2.0]


@pytest.mark.asyncio
async def test_amazon_get_orders_429_duas_vezes_levanta(sono_gravado) -> None:
    """Só UMA retentativa: 429 de novo vira erro pra quem chamou (a conta
    entra como "falhou" na rodada em vez de derrubar a cota mais ainda)."""
    client = AmazonClient(_amz_creds())
    with respx.mock(base_url=SP_API_BASE_NA) as router:
        route = router.get("/orders/v0/orders").mock(
            return_value=httpx.Response(429, json={"errors": [{"code": "QuotaExceeded"}]})
        )
        with pytest.raises(RuntimeError, match="429"):
            await client.get_orders(created_after="2026-09-18T00:00:00Z")
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_amazon_get_orders_403_levanta() -> None:
    client = AmazonClient(_amz_creds())
    with respx.mock(base_url=SP_API_BASE_NA) as router:
        router.get("/orders/v0/orders").mock(
            return_value=httpx.Response(403, json={"errors": [{"code": "Unauthorized"}]})
        )
        with pytest.raises(RuntimeError, match="403"):
            await client.get_orders(created_after="2026-09-18T00:00:00Z")


@pytest.mark.asyncio
async def test_amazon_iter_orders_pausa_1s_entre_paginas(sono_gravado) -> None:
    client = AmazonClient(_amz_creds())
    with respx.mock(base_url=SP_API_BASE_NA) as router:
        route = router.get("/orders/v0/orders").mock(
            side_effect=[
                httpx.Response(200, json=_amz_pagina(
                    [{"AmazonOrderId": "A", "OrderStatus": "Unshipped"}], next_token="N2",
                )),
                httpx.Response(200, json=_amz_pagina(
                    [{"AmazonOrderId": "B", "OrderStatus": "Shipped"}], next_token="N3",
                )),
                httpx.Response(200, json=_amz_pagina(
                    [{"AmazonOrderId": "C", "OrderStatus": "Canceled"}],
                )),
            ]
        )
        vistos = [o async for o in client.iter_orders(created_after="2026-09-18T00:00:00Z")]

    assert [o["AmazonOrderId"] for o in vistos] == ["A", "B", "C"]
    assert route.call_count == 3
    assert route.calls[1].request.url.params["NextToken"] == "N2"
    assert route.calls[2].request.url.params["NextToken"] == "N3"
    # 3 páginas → 2 pausas de 1 s (nenhuma depois da última)
    assert sono_gravado == [1.0, 1.0]


# ---------------------------------------------------------------- Bling


def _bling_pedido(numero_loja: str, *, numero: int = 1, loja: int = 204_000_000) -> dict:
    return {
        "id": 26_000_000_000 + numero,
        "numero": numero,
        "numeroLoja": numero_loja,
        "data": "2026-09-21",
        "loja": {"id": loja},
        "situacao": {"id": 6},
    }


def _bling_responde_pelos_numeros(existentes: set[str]):
    """Handler do respx que imita o Bling: devolve só os `numerosLojas[]`
    pedidos que existem."""

    def _h(request: httpx.Request) -> httpx.Response:
        pedidos_lote = request.url.params.get_list("numerosLojas[]")
        data = [
            _bling_pedido(n, numero=i + 1)
            for i, n in enumerate(pedidos_lote)
            if n in existentes
        ]
        return httpx.Response(200, json={"data": data})

    return _h


@pytest.mark.asyncio
@pytest.mark.usefixtures("sem_redis")
async def test_bling_list_pedidos_vendas_repete_numeros_lojas_na_query() -> None:
    client = _bling_client()
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{BLING_API_BASE}/pedidos/vendas").mock(
            return_value=httpx.Response(200, json={"data": [_bling_pedido("586175317994276425")]})
        )
        pedidos = await client.list_pedidos_vendas(
            numeros_lojas=["586175317994276425", "2509A1"]
        )

    assert pedidos[0]["numeroLoja"] == "586175317994276425"
    q = route.calls.last.request.url.params
    assert q.get_list("numerosLojas[]") == ["586175317994276425", "2509A1"]
    assert q["pagina"] == "1" and q["limite"] == "100"
    assert "dataInicial" not in q  # conferência por número não precisa de data


@pytest.mark.asyncio
@pytest.mark.usefixtures("sem_redis")
async def test_bling_list_pedidos_vendas_sem_numeros_lojas_nao_manda_o_param() -> None:
    client = _bling_client()
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{BLING_API_BASE}/pedidos/vendas").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        await client.list_pedidos_vendas(data_inicial="2026-09-01", numeros_lojas=[])
    q = route.calls.last.request.url.params
    assert "numerosLojas[]" not in q
    assert q["dataInicial"] == "2026-09-01"


@pytest.mark.asyncio
@pytest.mark.usefixtures("sem_redis")
async def test_bling_pedidos_por_numero_loja_lotes_de_20_e_mapa_por_numero() -> None:
    client = _bling_client()
    numeros = [f"5861{i:015d}" for i in range(45)]  # 45 → 20 + 20 + 5
    existentes = {numeros[0], numeros[21], numeros[44]}
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{BLING_API_BASE}/pedidos/vendas").mock(
            side_effect=_bling_responde_pelos_numeros(existentes)
        )
        achados = await client.pedidos_por_numero_loja(numeros)

    assert route.call_count == 3
    tamanhos = [len(c.request.url.params.get_list("numerosLojas[]")) for c in route.calls]
    assert tamanhos == [20, 20, 5]
    assert set(achados) == existentes
    assert achados[numeros[21]]["loja"]["id"] == 204_000_000
    assert "numero" in achados[numeros[0]] and "data" in achados[numeros[0]]


@pytest.mark.asyncio
@pytest.mark.usefixtures("sem_redis")
async def test_bling_pedidos_por_numero_loja_limpa_vazios_e_repetidos() -> None:
    client = _bling_client()
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{BLING_API_BASE}/pedidos/vendas").mock(
            side_effect=_bling_responde_pelos_numeros({"A1"})
        )
        achados = await client.pedidos_por_numero_loja(["A1", " A1 ", "", None, "B2"])  # type: ignore[list-item]

    assert route.call_count == 1
    assert route.calls.last.request.url.params.get_list("numerosLojas[]") == ["A1", "B2"]
    assert list(achados) == ["A1"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("sem_redis")
async def test_bling_pedidos_por_numero_loja_lista_vazia_nao_chama() -> None:
    client = _bling_client()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{BLING_API_BASE}/pedidos/vendas").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        assert await client.pedidos_por_numero_loja([]) == {}
        assert await client.pedidos_por_numero_loja(["", "  "]) == {}
    assert route.call_count == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("sem_redis")
async def test_bling_pedidos_por_numero_loja_erro_http_levanta() -> None:
    """"Não consegui conferir" ≠ "não está no Bling": o vigia não pode abrir
    ocorrência a partir de um erro do Bling."""
    client = _bling_client()
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{BLING_API_BASE}/pedidos/vendas").mock(
            return_value=httpx.Response(500, json={"error": {"type": "SERVER_ERROR"}})
        )
        with pytest.raises(httpx.HTTPStatusError):
            await client.pedidos_por_numero_loja(["A1"])
