"""MagaluClient unit tests (conector nativo Magazine Luiza).

Cobre:
  * authorize_url — choose_tenants=true + scopes + client_id (login de seller)
  * exchange_code — corpo JSON (o refresh, ao contrário, é form-urlencoded)
  * refresh — persiste o refresh_token rotacionado (single-use) via callback
  * update_stock — 202 = OK, guard B1 (nunca zera origem positiva), force libera
  * update_price — PATCH em centavos (normalizer=100), reais→cents
  * regra de SKU — external_id fica com hífen (id do recurso na Magalu) e
    sku vira a forma canônica com ponto (casa com products.sku)

respx stuba a superfície HTTP da Magalu; nenhuma chamada real de rede.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from app.services.marketplaces.base import SyncStatus
from app.services.marketplaces.magalu import (
    MAGALU_API_BASE,
    MAGALU_TOKEN_URL,
    MagaluClient,
    _canonical_sku,
    _normalize_magalu_sku,
)


def _creds(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        # app compartilhado: em produção client_id/secret vêm de settings, mas
        # no teste do refresh eles moram nas creds pra não depender do env.
        "client_id": "cid-test",
        "client_secret": "csec-test",
        "access_token": "tok",
        "refresh_token": "ref-old",
        "expires_at": int(time.time()) + 3600,
    }
    base.update(over)
    return base


def _link(external_id: str = "b001-20", stock: int | None = 10) -> SimpleNamespace:
    """update_stock só lê link.external_id e link.stock — um namespace basta."""
    return SimpleNamespace(external_id=external_id, stock=stock)


# --------------------------------------------------------------- authorize_url


def test_authorize_url_has_choose_tenants_and_scopes() -> None:
    url = MagaluClient.authorize_url(
        "state-xyz", client_id="cid-test", redirect_uri="https://app/cb"
    )
    assert url.startswith("https://id.magalu.com/login?")
    # choose_tenants=true é OBRIGATÓRIO p/ seller (escolhe o tenant no login).
    assert "choose_tenants=true" in url
    assert "response_type=code" in url
    assert "client_id=cid-test" in url
    assert "state=state-xyz" in url
    assert "scope=" in url
    # pelo menos um scope de portfólio presente (encodado no querystring).
    assert "portfolio-skus-seller" in url


# --------------------------------------------------------------- exchange_code


@pytest.mark.asyncio
async def test_exchange_code_sends_json_body() -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.post(MAGALU_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "AT",
                    "refresh_token": "RT",
                    "expires_in": 7200,
                    "scope": "open:portfolio-skus-seller:read",
                    "token_type": "Bearer",
                },
            )
        )
        creds = await MagaluClient.exchange_code(
            "the-code",
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://app/cb",
        )

    assert route.called
    req = route.calls.last.request
    # A troca do code é JSON (não form-urlencoded).
    assert req.headers["content-type"].startswith("application/json")
    body = json.loads(req.content)
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "the-code"
    assert creds["access_token"] == "AT"
    assert creds["refresh_token"] == "RT"
    assert creds["expires_at"] > int(time.time())


# --------------------------------------------------------- refresh (rotação SU)


@pytest.mark.asyncio
async def test_refresh_persists_rotated_refresh_token() -> None:
    persisted: dict[str, Any] = {}

    async def _on_refresh(new_creds: dict) -> None:
        persisted.update(new_creds)

    client = MagaluClient(_creds(refresh_token="ref-old"), on_token_refresh=_on_refresh)

    with respx.mock(assert_all_called=False) as router:
        route = router.post(MAGALU_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "AT2",
                    "refresh_token": "ref-new",  # rotacionado (single-use)
                    "expires_in": 7200,
                },
            )
        )
        await client.refresh()

    assert route.called
    req = route.calls.last.request
    # O refresh usa form-urlencoded (ao contrário do exchange, que é JSON).
    assert req.headers["content-type"].startswith("application/x-www-form-urlencoded")
    # O refresh_token NOVO foi persistido via callback e atualizado no client.
    assert persisted["refresh_token"] == "ref-new"
    assert client.creds["refresh_token"] == "ref-new"
    assert client.creds["access_token"] == "AT2"


# ---------------------------------------------------------------- update_stock


@pytest.mark.asyncio
async def test_update_stock_202_is_ok() -> None:
    client = MagaluClient(_creds())
    link = _link(external_id="b001-20", stock=3)
    with respx.mock(base_url=MAGALU_API_BASE) as router:
        route = router.patch("/seller/v1/portfolios/stocks/b001-20").mock(
            return_value=httpx.Response(202, json={})
        )
        result = await client.update_stock(link, 42)

    assert route.called
    body = json.loads(route.calls.last.request.content)
    # type="AVAILABLE" é obrigatório no PATCH de estoque da Magalu.
    assert body == {"quantity": 42, "type": "AVAILABLE"}
    assert result.status == SyncStatus.OK
    assert result.qty_after == 42


@pytest.mark.asyncio
async def test_update_stock_b1_guard_blocks_zero() -> None:
    client = MagaluClient(_creds())
    link = _link(external_id="b001-20", stock=8)  # origem positiva
    with respx.mock(base_url=MAGALU_API_BASE, assert_all_called=False) as router:
        result = await client.update_stock(link, 0)
        assert len(router.calls) == 0  # guard curto-circuita ANTES do HTTP

    assert result.status == SyncStatus.SKIPPED
    assert result.error_code == "b1_guard_zero_block"


@pytest.mark.asyncio
async def test_update_stock_force_allows_zero() -> None:
    client = MagaluClient(_creds())
    link = _link(external_id="b001-20", stock=8)
    with respx.mock(base_url=MAGALU_API_BASE) as router:
        router.patch("/seller/v1/portfolios/stocks/b001-20").mock(
            return_value=httpx.Response(202, json={})
        )
        result = await client.update_stock(link, 0, force=True)

    assert result.status == SyncStatus.OK
    assert result.qty_after == 0


# ---------------------------------------------------------------- update_price


@pytest.mark.asyncio
async def test_update_price_converts_reais_to_cents() -> None:
    client = MagaluClient(_creds())
    with respx.mock(base_url=MAGALU_API_BASE) as router:
        route = router.patch("/seller/v1/portfolios/prices/b001-20").mock(
            return_value=httpx.Response(202, json={})
        )
        result = await client.update_price("b001-20", 99.80)

    assert route.called
    body = json.loads(route.calls.last.request.content)
    # R$ 99,80 → 9980 centavos (normalizer=100 é o divisor).
    assert body["price"] == 9980
    assert body["list_price"] == 9980
    assert body["currency"] == "BRL"
    assert body["normalizer"] == 100
    assert result.status == SyncStatus.OK


@pytest.mark.asyncio
async def test_update_price_skips_nonpositive() -> None:
    client = MagaluClient(_creds())
    with respx.mock(base_url=MAGALU_API_BASE, assert_all_called=False) as router:
        result = await client.update_price("b001-20", 0)
        assert len(router.calls) == 0

    assert result.status == SyncStatus.SKIPPED
    assert result.error_code == "invalid_price"


# ------------------------------------------------------------------ regra SKU


def test_canonical_sku_hyphen_to_dot() -> None:
    # Magalu não aceita ponto; o canônico do sistema sempre usou ponto — a
    # conversão hífen→ponto é inequívoca.
    assert _canonical_sku("b001-20") == "b001.20"
    assert _canonical_sku("abc-1-2") == "abc.1.2"


def test_normalize_magalu_sku_splits_external_vs_canonical() -> None:
    norm = _normalize_magalu_sku(
        {"sku": "b001-20", "title": "Widget", "status": "PUBLISHED"}
    )
    assert norm is not None
    # external_id = SKU cru (hífen): id do recurso na Magalu, usado no PATCH e
    # gravado em product_links.external_id.
    assert norm["external_id"] == "b001-20"
    # sku = forma canônica (ponto): casa com products.sku nos dois caminhos de
    # vínculo (SQL exato do listings_import + _norm_sku do auto_link).
    assert norm["sku"] == "b001.20"
    assert norm["status"] == "active"
    assert norm["variation_id"] is None
