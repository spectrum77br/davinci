"""Unit tests pro shape do body que BlingClient.create_product manda.

Mocked via respx — não bate na API real do Bling. Foco: verificar que
`cost_price` é enviado como `precoCusto` top-level só quando > 0.
"""
from __future__ import annotations

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
    """Roda create_product com respx, devolve o body do POST."""
    client = _make_client()
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{BLING_API_BASE}/produtos").mock(
            return_value=httpx.Response(200, json={"data": {"id": 42}}),
        )
        await client.create_product(sku=sku, name=name, **kwargs)
    # respx guarda o body como bytes em route.calls[0].request.content
    import json
    return json.loads(route.calls[0].request.content)


@pytest.mark.asyncio
async def test_cost_price_sent_as_preco_custo_top_level():
    body = await _capture_post_body(sku="b042.30", name="Mala teste", cost_price=49.0)
    assert body["precoCusto"] == 49.0


@pytest.mark.asyncio
async def test_cost_price_omitted_when_none():
    body = await _capture_post_body(sku="b042.30", name="Mala teste", cost_price=None)
    assert "precoCusto" not in body


@pytest.mark.asyncio
async def test_cost_price_omitted_when_zero():
    """0 é tratado como 'não informado' — Bling pode interpretar 0 como
    'preço de custo é zero', o que é diferente da nossa intent."""
    body = await _capture_post_body(sku="b042.30", name="Mala teste", cost_price=0)
    assert "precoCusto" not in body


@pytest.mark.asyncio
async def test_cost_price_negative_also_omitted():
    body = await _capture_post_body(sku="b042.30", name="Mala teste", cost_price=-5)
    assert "precoCusto" not in body


@pytest.mark.asyncio
async def test_basic_shape_has_required_fields():
    body = await _capture_post_body(sku="b042.30", name="Mala teste", cost_price=49.0)
    assert body["nome"] == "Mala teste"
    assert body["codigo"] == "b042.30"
    assert body["tipo"] == "P"
    assert body["situacao"] == "A"
    assert body["formato"] == "S"


@pytest.mark.asyncio
async def test_composto_sends_estrutura():
    body = await _capture_post_body(
        sku="b045.8.18", name="Kit",
        formato="E",
        estrutura={"tipoEstoque": "V", "lancamentoEstoque": "M", "componentes": []},
    )
    assert body["formato"] == "E"
    assert body["estrutura"]["tipoEstoque"] == "V"


@pytest.mark.asyncio
async def test_price_and_cost_can_coexist():
    """price = preço de venda; precoCusto = preço de custo."""
    body = await _capture_post_body(
        sku="x", name="y", price=99.9, cost_price=49.0,
    )
    assert body["preco"] == 99.9
    assert body["precoCusto"] == 49.0
