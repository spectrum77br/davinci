"""Unit tests pro shape do body que BlingClient manda ao Bling.

Mocked via respx — não bate na API real. Cobre:
  * POST /produtos (create_product): campos básicos, preço, composto.
    Custo NÃO entra mais aqui — Bling V3 descarta `fornecedor` no body.
  * POST /produtos/fornecedores (link_supplier_to_product): único
    caminho que persiste precoCusto.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.services.marketplaces.bling import BLING_API_BASE, BlingClient


def _make_client() -> BlingClient:
    """Cliente com creds mínimas — token refresh nunca dispara aqui
    porque o respx intercepta antes."""
    creds = {
        "access_token": "tok",
        "refresh_token": "ref",
        "client_id": "cid",
        "client_secret": "csec",
        "expires_at": 99999999999,  # far future, evita refresh
    }
    return BlingClient(creds)


async def _capture_post_body(
    *, sku: str, name: str, **kwargs: Any,
) -> dict[str, Any]:
    """Roda create_product com respx, devolve o body do POST /produtos."""
    client = _make_client()
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{BLING_API_BASE}/produtos").mock(
            return_value=httpx.Response(200, json={"data": {"id": 42}}),
        )
        await client.create_product(sku=sku, name=name, **kwargs)
    return json.loads(route.calls[0].request.content)


@pytest.mark.asyncio
async def test_basic_shape_has_required_fields():
    body = await _capture_post_body(sku="b042.30", name="Mala teste")
    assert body["nome"] == "Mala teste"
    assert body["codigo"] == "b042.30"
    assert body["tipo"] == "P"
    assert body["situacao"] == "A"
    assert body["formato"] == "S"


@pytest.mark.asyncio
async def test_create_never_sends_cost_or_fornecedor():
    """Custo vai por endpoint separado — o create nunca manda
    `fornecedor`/`precoCusto` (Bling V3 descarta silenciosamente)."""
    body = await _capture_post_body(
        sku="b042.30", name="Mala teste", price=99.9,
    )
    assert "fornecedor" not in body
    assert "precoCusto" not in body


@pytest.mark.asyncio
async def test_price_is_sent_top_level():
    body = await _capture_post_body(sku="x", name="y", price=99.9)
    assert body["preco"] == 99.9


@pytest.mark.asyncio
async def test_composto_sends_estrutura():
    body = await _capture_post_body(
        sku="b045.8.18", name="Kit",
        formato="E",
        estrutura={"tipoEstoque": "V", "lancamentoEstoque": "M", "componentes": []},
    )
    assert body["formato"] == "E"
    assert body["estrutura"]["tipoEstoque"] == "V"


# ── link_supplier_to_product → POST /produtos/fornecedores ──────────


@pytest.mark.asyncio
async def test_link_supplier_sends_correct_body():
    """{idProduto, idContato, precoCusto} — único shape que persiste."""
    client = _make_client()
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{BLING_API_BASE}/produtos/fornecedores").mock(
            return_value=httpx.Response(201, json={"data": {"id": 7}}),
        )
        data = await client.link_supplier_to_product(
            product_id=12345, supplier_id=16980149177, cost_price=49.0,
        )
    body = json.loads(route.calls[0].request.content)
    assert body == {
        "idProduto": 12345,
        "idContato": 16980149177,
        "precoCusto": 49.0,
    }
    assert data == {"id": 7}


@pytest.mark.asyncio
async def test_link_supplier_noop_when_cost_zero_or_negative():
    """Custo <= 0 não chama o endpoint — retorna {} sem POST."""
    client = _make_client()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(f"{BLING_API_BASE}/produtos/fornecedores")
        assert await client.link_supplier_to_product(
            product_id=1, supplier_id=2, cost_price=0,
        ) == {}
        assert await client.link_supplier_to_product(
            product_id=1, supplier_id=2, cost_price=-5,
        ) == {}
    assert route.call_count == 0


# ── Upsert via "Registro duplicado" (code 279) ───────────────────────


_DUPLICATE_PAYLOAD = {
    "error": {
        "type": "VALIDATION_ERROR",
        "message": "Registro duplicado",
        "fields": [
            {"code": 279, "msg": "Registro duplicado", "element": "produto"},
        ],
    },
}


@pytest.mark.asyncio
async def test_link_supplier_duplicate_fallbacks_to_put_via_get():
    """POST 400 + code=279 → GET pra achar link_id existente → PUT
    pra atualizar precoCusto. Shape do PUT é `{produto:{id},
    fornecedor:{id}, precoCusto}` (NÃO `{idProduto, idContato, ...}`
    do POST — V3 segue padrão nested no update)."""
    client = _make_client()
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{BLING_API_BASE}/produtos/fornecedores").mock(
            return_value=httpx.Response(400, json=_DUPLICATE_PAYLOAD),
        )
        get_route = mock.get(f"{BLING_API_BASE}/produtos/fornecedores").mock(
            return_value=httpx.Response(200, json={
                "data": [{"id": 999, "produto": {"id": 12345}, "precoCusto": 10.0}],
            }),
        )
        put_route = mock.put(f"{BLING_API_BASE}/produtos/fornecedores/999").mock(
            return_value=httpx.Response(204),
        )
        data = await client.link_supplier_to_product(
            product_id=12345, supplier_id=16980149177, cost_price=49.0,
        )

    # GET filtra por (idProduto, idFornecedor) — params da query string.
    get_params = dict(get_route.calls[0].request.url.params)
    assert get_params == {"idProduto": "12345", "idFornecedor": "16980149177"}

    # PUT envia shape nested.
    put_body = json.loads(put_route.calls[0].request.content)
    assert put_body == {
        "produto": {"id": 12345},
        "fornecedor": {"id": 16980149177},
        "precoCusto": 49.0,
    }
    # Bling V3 PUT pode retornar 204 sem body — função tolera.
    assert data == {}


@pytest.mark.asyncio
async def test_link_supplier_duplicate_but_get_empty_returns_empty():
    """Race condition rara: POST 400 duplicado mas GET sem registros.
    Função retorna {} sem raise — não derruba caller."""
    client = _make_client()
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{BLING_API_BASE}/produtos/fornecedores").mock(
            return_value=httpx.Response(400, json=_DUPLICATE_PAYLOAD),
        )
        mock.get(f"{BLING_API_BASE}/produtos/fornecedores").mock(
            return_value=httpx.Response(200, json={"data": []}),
        )
        data = await client.link_supplier_to_product(
            product_id=12345, supplier_id=16980149177, cost_price=49.0,
        )
    assert data == {}


@pytest.mark.asyncio
async def test_link_supplier_non_duplicate_400_raises():
    """400 com outro código (ex: 99 = validação genérica) NÃO faz
    fallback — propaga o erro pro caller resolver."""
    other_err = {
        "error": {
            "type": "VALIDATION_ERROR",
            "message": "Outro erro",
            "fields": [{"code": 99, "msg": "qualquer outro"}],
        },
    }
    client = _make_client()
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{BLING_API_BASE}/produtos/fornecedores").mock(
            return_value=httpx.Response(400, json=other_err),
        )
        with pytest.raises(httpx.HTTPStatusError):
            await client.link_supplier_to_product(
                product_id=12345, supplier_id=16980149177, cost_price=49.0,
            )
