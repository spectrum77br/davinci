"""Bling API v3 client.

OAuth2 (authorization code), automatic refresh on 401.
Credentials stored in `integrations.credentials` shape:
    {
      "client_id": str,
      "client_secret": str,
      "access_token": str,
      "refresh_token": str,
      "token_type": "Bearer",
      "scope": str,
      "expires_at": int (epoch seconds),
    }
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from app.config import get_settings
from app.services.marketplaces.base import SyncResult, SyncStatus, TestResult

logger = structlog.get_logger()

BLING_AUTH_URL = "https://www.bling.com.br/Api/v3/oauth/authorize"
BLING_TOKEN_URL = "https://www.bling.com.br/Api/v3/oauth/token"
BLING_API_BASE = "https://api.bling.com.br/Api/v3"

# Default page size for `/produtos`. Bling caps at 100.
BLING_PRODUCTS_PAGE_SIZE = 100

# Bling V3 Cloudflare gate is 3 req/s; keep one slot of headroom and cap at
# 5 req/s globally. Limiter is shared across api/worker via Redis so parallel
# arq jobs and webhook handlers can't burst past the cap.
BLING_MAX_RPS = 5


async def _acquire_bling_rate_slot() -> None:
    """Token-bucket gate: consume one slot per second, sleep until one frees."""
    from app.redis_client import redis as _redis
    while True:
        bucket = int(time.time())
        key = f"bling:rate:{bucket}"
        n = await _redis.incr(key)
        if n == 1:
            await _redis.expire(key, 2)
        if n <= BLING_MAX_RPS:
            return
        # Over cap for this second — wait until the next one opens.
        wait = max(0.0, (bucket + 1) - time.time()) + 0.01
        await asyncio.sleep(wait)


class BlingCloudflareError(RuntimeError):
    """Raised when Bling returns the Cloudflare HTML challenge instead of JSON."""


def _looks_like_cf_html(resp: httpx.Response) -> bool:
    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype.lower():
        return False
    head = resp.text[:512].lower()
    return "<html" in head or "cloudflare" in head or "just a moment" in head


class BlingClient:
    def __init__(
        self,
        creds: dict,
        on_token_refresh=None,
        integration_id=None,
    ):
        self.creds = dict(creds)
        self._on_refresh = on_token_refresh
        self._integration_id = integration_id

    @property
    def access_token(self) -> str | None:
        return self.creds.get("access_token")

    @property
    def expires_at(self) -> int:
        return int(self.creds.get("expires_at") or 0)

    def _expired(self, skew: int = 30) -> bool:
        return self.expires_at - skew <= int(time.time())

    @staticmethod
    def authorize_url(state: str) -> str:
        s = get_settings()
        params = {
            "response_type": "code",
            "client_id": s.bling_client_id,
            "state": state,
            "redirect_uri": s.bling_redirect_uri,
        }
        return f"{BLING_AUTH_URL}?{urlencode(params)}"

    @staticmethod
    async def exchange_code(code: str) -> dict:
        s = get_settings()
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                BLING_TOKEN_URL,
                auth=(s.bling_client_id, s.bling_client_secret),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "enable-jwt": "1",
                },
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": s.bling_redirect_uri,
                },
            )
            r.raise_for_status()
            creds = _normalize_token(r.json())
            creds["client_id"] = s.bling_client_id
            creds["client_secret"] = s.bling_client_secret
            return creds

    def _client_creds(self) -> tuple[str, str]:
        """Per-integration client_id/secret when present, else env fallback."""
        s = get_settings()
        cid = str(self.creds.get("client_id") or s.bling_client_id or "")
        csec = str(self.creds.get("client_secret") or s.bling_client_secret or "")
        return cid, csec

    async def refresh(self) -> None:
        rt = self.creds.get("refresh_token")
        if not rt:
            raise RuntimeError("missing refresh_token")
        # Cloudflare 1015 ban renews every time we hit /oauth/token while
        # the IP is blocked, so once we see a 429 we stop calling the
        # endpoint for CF_COOLDOWN_S. Without this, the cron, on-demand
        # 401 retry, and webhook-driven sync jobs all keep refreshing the
        # ban and the cooldown never expires.
        from app.redis_client import redis as _redis
        blocked = await _redis.get("bling:cf_cooldown_until")
        if blocked is not None:
            try:
                ttl = int(blocked) - int(time.time())
            except (TypeError, ValueError):
                ttl = 0
            if ttl > 0:
                raise RuntimeError(f"bling_cf_cooldown_active ttl_s={ttl}")
        s = get_settings()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "enable-jwt": "1",
        }
        auth = None
        cid, csec = self._client_creds()
        if s.bling_basic_auth:
            headers["Authorization"] = s.bling_basic_auth
        else:
            auth = (cid, csec)
        await _acquire_bling_rate_slot()
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                BLING_TOKEN_URL,
                auth=auth,
                headers=headers,
                data={"grant_type": "refresh_token", "refresh_token": rt},
            )
            if r.status_code >= 400:
                body_preview = r.text[:500]
                logger.warning(
                    "bling_refresh_http_error",
                    status=r.status_code,
                    body=body_preview,
                    client_id_prefix=cid[:8],
                    rt_prefix=rt[:8],
                )
                if r.status_code == 429 and "cloudflare" in body_preview.lower():
                    CF_COOLDOWN_S = 1800
                    until = int(time.time()) + CF_COOLDOWN_S
                    await _redis.set(
                        "bling:cf_cooldown_until", str(until), ex=CF_COOLDOWN_S
                    )
                    logger.warning(
                        "bling_cf_cooldown_armed",
                        cooldown_s=CF_COOLDOWN_S,
                        until_epoch=until,
                    )
                r.raise_for_status()
            new_creds = _normalize_token(r.json(), prev=self.creds)
        # Persist FIRST via independent session — Bling already rotated RT,
        # losing it here means permanent lockout. After durable write, update
        # in-memory state and fire legacy callback (if any).
        if self._integration_id is not None:
            await _persist_bling_creds(self._integration_id, new_creds)
        self.creds = new_creds
        if self._on_refresh:
            await self._on_refresh(self.creds)

    async def _request(
        self, method: str, path: str, *, params: dict | None = None, json: Any = None
    ) -> httpx.Response:
        if self._expired():
            await self.refresh()
        url = f"{BLING_API_BASE}{path}"
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}

        # Bling occasionally serves a Cloudflare challenge (HTTP 403 + HTML) under
        # heavy load. Retry with exponential backoff up to 3 attempts (B12).
        last_exc: Exception | None = None
        delay = 1.0
        for attempt in range(3):
            await _acquire_bling_rate_slot()
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.request(method, url, headers=headers, params=params, json=json)
            if r.status_code == 401 and attempt == 0:
                await self.refresh()
                headers["Authorization"] = f"Bearer {self.access_token}"
                continue
            cf = _looks_like_cf_html(r)
            if r.status_code in (429, 503) or (r.status_code == 403 and cf):
                last_exc = BlingCloudflareError(
                    f"status={r.status_code} cf_html={cf}"
                )
                logger.warning(
                    "bling_retry",
                    attempt=attempt + 1,
                    status=r.status_code,
                    path=path,
                )
                await asyncio.sleep(delay)
                delay *= 2
                continue
            return r
        if last_exc:
            raise last_exc
        return r

    async def list_lojas(self) -> list[dict]:
        r = await self._request("GET", "/lojas")
        r.raise_for_status()
        body = r.json()
        return body.get("data", [])

    async def list_products_page(
        self,
        *,
        pagina: int = 1,
        limite: int = BLING_PRODUCTS_PAGE_SIZE,
    ) -> list[dict]:
        """Single page of `/produtos`. Bling pagination via `pagina` + `limite` query params."""
        r = await self._request(
            "GET",
            "/produtos",
            params={"pagina": pagina, "limite": limite},
        )
        r.raise_for_status()
        return r.json().get("data", []) or []

    async def list_products(self, *, start_page: int = 1) -> AsyncIterator[dict]:
        """Iterate every product, paging until Bling returns an empty page."""
        page = start_page
        while True:
            items = await self.list_products_page(pagina=page)
            if not items:
                return
            for it in items:
                yield it
            if len(items) < BLING_PRODUCTS_PAGE_SIZE:
                return
            page += 1

    async def get_product(self, bling_product_id: int) -> dict:
        r = await self._request("GET", f"/produtos/{bling_product_id}")
        r.raise_for_status()
        return r.json().get("data") or {}

    async def get_order(self, bling_order_id: int) -> dict:
        r = await self._request("GET", f"/pedidos/vendas/{bling_order_id}")
        r.raise_for_status()
        return r.json().get("data") or {}

    async def list_pedidos_vendas(
        self,
        *,
        data_inicial: str | None = None,
        data_final: str | None = None,
        id_situacao: int | None = None,
        id_loja: int | None = None,
        pagina: int = 1,
        limite: int = 100,
    ) -> list[dict]:
        """Single page of `/pedidos/vendas` with the marketing module's
        filter set. Bling v3 expects YYYY-MM-DD for dates and integer ids
        for situação/loja.

        situação 9 = "Atendido" (NF emitida) — the canonical "faturado"
        signal the aggregator uses as authoritative revenue. Other useful
        situações: 5 (em andamento), 6 (em digitação), 12 (cancelado).
        Pagination via `pagina` + `limite`; caller iterates pages."""
        params: dict[str, Any] = {"pagina": pagina, "limite": limite}
        if data_inicial:
            params["dataInicial"] = data_inicial
        if data_final:
            params["dataFinal"] = data_final
        if id_situacao is not None:
            params["idSituacao"] = id_situacao
        if id_loja is not None:
            params["idLoja"] = id_loja
        r = await self._request("GET", "/pedidos/vendas", params=params)
        r.raise_for_status()
        return r.json().get("data") or []

    async def iter_pedidos_vendas(
        self,
        *,
        data_inicial: str | None = None,
        data_final: str | None = None,
        id_situacao: int | None = None,
        id_loja: int | None = None,
        page_size: int = 100,
    ) -> AsyncIterator[dict]:
        """Iterate every pedido matching the filters, pulling pages until
        Bling returns a partial page (signal of end-of-list). Hard cap at
        50 pages to bound runaway loops; the marketing window is at most
        30 days so 50×100=5000 pedidos is generous."""
        page = 1
        while page <= 50:
            items = await self.list_pedidos_vendas(
                data_inicial=data_inicial, data_final=data_final,
                id_situacao=id_situacao, id_loja=id_loja,
                pagina=page, limite=page_size,
            )
            if not items:
                return
            for it in items:
                yield it
            if len(items) < page_size:
                return
            page += 1

    async def update_order_situacao(
        self, bling_order_id: int, situacao_id: int
    ) -> None:
        """Move a Bling pedido to a target situacao.

        Endpoint: PATCH /pedidos/vendas/{idPedido}/situacoes/{idSituacao}
        Bling returns 204 on success.
        """
        r = await self._request(
            "PATCH",
            f"/pedidos/vendas/{bling_order_id}/situacoes/{situacao_id}",
        )
        r.raise_for_status()

    async def update_stock_by_id(
        self,
        bling_product_id: int,
        qty: int,
        *,
        bling_store_id: int | None = None,
        operation: str = "B",
    ) -> dict:
        """Raw Bling stock write. POST /Api/v3/estoques.

        When `bling_store_id` is provided it is sent as `idLoja` so the change
        reflects on the correct channel inside Bling. Body shape per Bling docs:
            { "produto": {"id": <id>}, "operacao": "B"|"S"|"E", "quantidade": <qty>,
              "deposito": {"id": <id>}, "idLoja": <int?> }
        """
        body: dict[str, Any] = {
            "produto": {"id": bling_product_id},
            "operacao": operation,
            "quantidade": qty,
        }
        if bling_store_id is not None:
            body["idLoja"] = bling_store_id
        r = await self._request("POST", "/estoques", json=body)
        r.raise_for_status()
        return r.json().get("data") or {}

    async def update_stock(
        self,
        link: Any,
        qty: int,
        *,
        bling_store_id: int | None = None,
        force: bool = False,
    ) -> SyncResult:
        """ABC-conformant wrapper. `link` is a `ProductLink` whose `external_id`
        carries the Bling produto.id. See `services.marketplaces.base.MarketplaceClient`.

        `force` accepted for ABC parity; Bling has no zero-stock guard.
        """
        del force
        try:
            bling_product_id = int(link.external_id)
        except (TypeError, ValueError):
            return SyncResult(
                status=SyncStatus.FATAL,
                error_code="invalid_external_id",
                error_detail=f"link.external_id={link.external_id!r}",
            )
        qty_before = link.stock
        try:
            data = await self.update_stock_by_id(
                bling_product_id, qty, bling_store_id=bling_store_id
            )
        except BlingCloudflareError as e:
            return SyncResult(
                status=SyncStatus.RETRYABLE,
                qty_before=qty_before,
                error_code="bling_cloudflare",
                error_detail=str(e),
            )
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else None
            return SyncResult(
                status=SyncStatus.RETRYABLE if code in (429, 502, 503, 504) else SyncStatus.FATAL,
                qty_before=qty_before,
                error_code=f"http_{code}",
                error_detail=e.response.text[:500] if e.response is not None else str(e),
            )
        return SyncResult(
            status=SyncStatus.OK,
            qty_before=qty_before,
            qty_after=qty,
            payload=data if isinstance(data, dict) else {},
        )

    async def update_price(
        self,
        bling_product_id: int,
        price: float | int | str,
        *,
        bling_store_id: int | None = None,
    ) -> dict:
        """Stub for Fase 4. Bling endpoint: PATCH /Api/v3/produtos/{id}/precos
        with optional `idLoja` query param to scope to a channel.
        """
        params: dict[str, Any] = {}
        if bling_store_id is not None:
            params["idLoja"] = bling_store_id
        r = await self._request(
            "PATCH",
            f"/produtos/{bling_product_id}/precos",
            params=params or None,
            json={"preco": float(price)},
        )
        r.raise_for_status()
        return r.json().get("data") or {}

    async def get_product_stock_smart(
        self,
        bling_product_id: int,
        sku: str | None = None,
    ) -> dict:
        """Smart stock fetch: if the product is deleted/inactive in Bling,
        search by SKU to find the active replacement.

        Returns dict with keys: stock, bling_product_id, found_via, raw
        Raises RuntimeError if product cannot be resolved.
        """
        try:
            raw = await self.get_product(bling_product_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404 and sku:
                # Product deleted — try to find active one by SKU
                active = await self.find_active_product_by_sku(sku)
                if active:
                    return {
                        "stock": active.get("stock"),
                        "bling_product_id": active.get("id"),
                        "found_via": "sku_search",
                        "raw": active,
                    }
                raise RuntimeError(
                    f"Product {bling_product_id} deleted and no active product found for SKU {sku}"
                ) from e
            raise

        parsed = parse_bling_product(raw)
        situacao = (raw.get("situacao") or "").upper()

        # If product is inactive/excluded, search by SKU
        if situacao in ("I", "E", "INATIVO", "EXCLUIDO") and sku:
            active = await self.find_active_product_by_sku(sku)
            if active:
                return {
                    "stock": active.get("stock"),
                    "bling_product_id": active.get("id"),
                    "found_via": "sku_search_inactive",
                    "raw": active,
                }

        return {
            "stock": parsed.get("stock"),
            "bling_product_id": bling_product_id,
            "found_via": "direct",
            "raw": raw,
        }

    async def find_active_product_by_sku(self, sku: str) -> dict | None:
        """Search Bling for an active product matching the given SKU.

        Bling's /produtos endpoint supports filtering by codigo (SKU).
        Returns the first active product found, or None.
        """
        if not sku:
            return None
        try:
            r = await self._request(
                "GET",
                "/produtos",
                params={"codigo": sku, "pagina": 1, "limite": 5},
            )
            r.raise_for_status()
            items = r.json().get("data") or []
            for item in items:
                situacao = (item.get("situacao") or "").upper()
                if situacao in ("A", "ATIVO", ""):
                    # Get full product details including stock
                    item_id = item.get("id")
                    if item_id:
                        full = await self.get_product(int(item_id))
                        parsed = parse_bling_product(full)
                        return {
                            "id": item_id,
                            "sku": item.get("codigo") or sku,
                            "stock": parsed.get("stock"),
                            "name": item.get("nome"),
                            "raw": full,
                        }
            return None
        except Exception:  # noqa: BLE001
            return None

    async def product_exists_by_sku(self, sku: str) -> bool:
        """Return True if any product (active or inactive) exists with this SKU in Bling."""
        if not sku:
            return False
        try:
            r = await self._request(
                "GET", "/produtos", params={"codigo": sku, "pagina": 1, "limite": 1}
            )
            r.raise_for_status()
            return len(r.json().get("data") or []) > 0
        except Exception:  # noqa: BLE001
            return False

    async def find_next_z_sku(self) -> str:
        """Find the first available sequential z-SKU (z0001, z0002, …) not yet in Bling."""
        for n in range(1, 10000):
            candidate = f"z{n:04d}"
            if not await self.product_exists_by_sku(candidate):
                return candidate
        raise RuntimeError("z-SKU space exhausted (z0001–z9999 all taken)")

    async def get_category_id_by_name(self, name: str) -> int | None:
        """Return the Bling category ID matching `name` (case-insensitive), or None."""
        try:
            r = await self._request(
                "GET", "/categorias/produtos", params={"pagina": 1, "limite": 100}
            )
            r.raise_for_status()
            for item in r.json().get("data") or []:
                label = item.get("descricao") or item.get("nome") or ""
                if label.strip().lower() == name.strip().lower():
                    return item.get("id")
            return None
        except Exception:  # noqa: BLE001
            return None

    async def create_product(
        self,
        *,
        sku: str,
        name: str,
        price: float | None = None,
        category_id: int | None = None,
        formato: str = "S",
        estrutura: dict | None = None,
    ) -> dict:
        """Create a product in Bling via POST /Api/v3/produtos.

        Returns the `data` dict from Bling (contains at least `{"id": <int>}`).

        formato:
          * "S" = Simples (default)
          * "V" = Com variações
          * "E" = Com composição (kit/composto)

        O custo (precoCusto) NÃO entra aqui — Bling V3 descarta
        silenciosamente o bloco `fornecedor` no body do POST /produtos.
        O único caminho que faz o custo persistir é o endpoint separado
        `POST /produtos/fornecedores` (ver `link_supplier_to_product`),
        chamado pelo caller depois deste create retornar o id.

        Para composto, `estrutura` deve ter o shape (confirmado via
        SDK AlexandreBellas/bling-erp-api-js, src/entities/produtos):
          {
            "tipoEstoque": "F" | "V",            # F=Físico, V=Virtual
            "lancamentoEstoque": "A" | "M" | "P", # A=Produto+Componente, M=Componente, P=Produto
            "componentes": [
              {"produto": {"id": <int>}, "quantidade": <float>},
              ...
            ]
          }
        """
        body: dict[str, Any] = {
            "nome": name,
            "codigo": sku,
            "tipo": "P",
            "situacao": "A",
            "formato": formato,
        }
        if price is not None:
            body["preco"] = float(price)
        if category_id is not None:
            body["categoria"] = {"id": category_id}
        if formato == "E" and estrutura is not None:
            body["estrutura"] = estrutura
        r = await self._request("POST", "/produtos", json=body)
        r.raise_for_status()
        return r.json().get("data") or {}

    async def link_supplier_to_product(
        self, *, product_id: int, supplier_id: int, cost_price: float,
    ) -> dict:
        """Cria o relacionamento produto↔fornecedor no Bling V3 com custo.

        Endpoint: POST /produtos/fornecedores
        Body:     {idProduto, idContato, precoCusto}

        Esse é o único caminho que faz precoCusto persistir — o campo
        `fornecedor` no body do POST /produtos é silenciosamente
        descartado por Bling V3. Confirmado via teste em prod 2026-05-28
        (placeholder 000000111111111ll, contato 16980149177 → precoCusto
        apareceu corretamente).

        Returns the data dict from Bling (contém `id` do relacionamento).
        Custo <= 0 é no-op (retorna {}).
        """
        if cost_price <= 0:
            return {}
        body = {
            "idProduto": int(product_id),
            "idContato": int(supplier_id),
            "precoCusto": float(cost_price),
        }
        r = await self._request("POST", "/produtos/fornecedores", json=body)
        r.raise_for_status()
        return r.json().get("data") or {}

    async def find_contato_id_by_name(self, name: str) -> int | None:
        """Resolve o contato.id pelo nome via GET /contatos?pesquisa=<name>.
        Match exato case-insensitive. Retorna None se não encontrar.

        Bling V3 não tem unique constraint em nome — se houver homônimos,
        pega o primeiro. Pra o caso de uso atual (fornecedor padrão
        único anchor de precoCusto), isso é suficiente.
        """
        target = (name or "").strip().lower()
        if not target:
            return None
        try:
            r = await self._request("GET", "/contatos", params={"pesquisa": name})
            r.raise_for_status()
            items = r.json().get("data") or []
            for c in items:
                if (c.get("nome") or "").strip().lower() == target:
                    cid = c.get("id")
                    if cid is not None:
                        return int(cid)
            return None
        except Exception:  # noqa: BLE001
            return None

    async def find_or_create_category(self, name: str) -> int:
        """Resolve category by name (case-insensitive). Creates a new one
        via POST /categorias/produtos if missing. Returns the category id.

        Walks pages of GET /categorias/produtos until the name matches or
        the list ends — categorias é catálogo pequeno (~dezenas), 1-2
        páginas no pior caso.
        """
        target = name.strip().lower()
        pagina = 1
        while True:
            r = await self._request(
                "GET", "/categorias/produtos",
                params={"pagina": pagina, "limite": 100},
            )
            r.raise_for_status()
            items = r.json().get("data") or []
            for item in items:
                label = (item.get("descricao") or item.get("nome") or "").strip().lower()
                if label == target:
                    cid = item.get("id")
                    if cid is not None:
                        return int(cid)
            if len(items) < 100:
                break
            pagina += 1
        # Não achou → criar.
        rc = await self._request(
            "POST", "/categorias/produtos",
            json={"descricao": name.strip()},
        )
        rc.raise_for_status()
        data = rc.json().get("data") or {}
        new_id = data.get("id")
        if new_id is None:
            raise RuntimeError(f"Bling created category but returned no id: {data}")
        return int(new_id)

    async def test_connection(self) -> TestResult:
        try:
            r = await self._request("GET", "/produtos", params={"pagina": 1, "limite": 1})
            if r.status_code == 200:
                data = r.json().get("data") or []
                return TestResult(ok=True, info={"sample_count": len(data)})
            return TestResult(ok=False, detail=f"status={r.status_code} body={r.text[:200]}")
        except httpx.HTTPError as e:
            return TestResult(ok=False, detail=f"http_error: {e}")
        except Exception as e:  # noqa: BLE001
            return TestResult(ok=False, detail=f"error: {e}")


def parse_bling_product(raw: dict) -> dict:
    """Normalize Bling `/produtos` item to the shape used by `BlingPreviewItem`.

    Bling fields (v3): id, nome, codigo (sku), preco, estoque{saldoVirtualTotal,minimo},
    preco_custo (varies by endpoint), imagemURL, categoria{descricao}, observacoes.
    """
    estoque = raw.get("estoque") or {}
    if isinstance(estoque, dict):
        # Strictly virtual stock — physical excludes reserves/pending orders
        # and would over-state availability for marketplaces.
        stock = estoque.get("saldoVirtualTotal")
        min_stock = estoque.get("minimo")
    else:
        stock = None
        min_stock = None
    sku = (raw.get("codigo") or "").strip() or None
    cost = raw.get("precoCusto") or raw.get("preco_custo")
    if cost is None:
        # Bling v3 GET /produtos/{id}: precoCusto lives under fornecedor.precoCusto
        fornecedor = raw.get("fornecedor") or {}
        if isinstance(fornecedor, dict):
            cost = fornecedor.get("precoCusto") or fornecedor.get("precoCompra")
    image = raw.get("imagemURL") or raw.get("midia", {}).get("imagem", {}).get("url")
    categoria = raw.get("categoria") or {}
    if isinstance(categoria, dict):
        category = categoria.get("descricao") or categoria.get("nome")
    else:
        category = None
    observation = raw.get("observacoes")
    if observation is not None:
        observation = str(observation).strip() or None
    # situacao: A=Ativo, I=Inativo, E=Excluído (char(1) no DB).
    # formato:  S=Simples, E=Composto/kit.
    # Bling v3 GET /produtos/{id} já retorna esses dois campos com a letra
    # certa; o [:1] é só um cinto-e-suspensórios caso uma rota antiga (ou
    # uma resposta de erro fora do padrão) devolva "Ativo"/"Simples" por
    # extenso. Ambos viram None se a chave estiver ausente — quem cria/
    # atualiza decide qual fallback aplicar (a importação default é "A"/"S").
    situacao = (raw.get("situacao") or "").strip().upper()[:1] or None
    formato = (raw.get("formato") or "").strip().upper()[:1] or None
    return {
        "bling_product_id": int(raw["id"]),
        "sku": sku,
        "name": raw.get("nome") or raw.get("descricao") or "",
        "cost_price": cost,
        "bling_cost_price": cost,
        "price": raw.get("preco"),
        "stock": int(stock) if stock is not None else None,
        "min_stock": int(min_stock) if min_stock is not None else None,
        "image_url": image,
        "category": category,
        "observation": observation,
        "situacao": situacao,
        "formato": formato,
    }


def _normalize_token(payload: dict, prev: dict | None = None) -> dict:
    """Convert Bling token response to internal credential dict."""
    expires_in = int(payload.get("expires_in") or 21600)
    out: dict = dict(prev or {})
    out["access_token"] = payload["access_token"]
    if "refresh_token" in payload:
        out["refresh_token"] = payload["refresh_token"]
    out["token_type"] = payload.get("token_type", "Bearer")
    out["scope"] = payload.get("scope", out.get("scope", ""))
    out["expires_at"] = int(time.time()) + expires_in
    out["_obtained_at"] = datetime.now(UTC).isoformat()
    return out


async def _persist_bling_creds(integration_id, new_creds: dict) -> None:
    """Write fresh Bling creds to DB via an independent session.

    Bling rotates refresh_token on every refresh — if we don't durably store
    the new pair right after the HTTP 200, the integration locks out
    permanently. Independent session avoids being trapped by a caller's
    failing transaction."""
    from app.db import session_scope
    from app.models import Integration
    from app.security.cipher import encrypt_json
    async with session_scope() as s:
        it = await s.get(Integration, integration_id)
        if it is None:
            logger.error("bling_persist_integration_missing", integration_id=str(integration_id))
            return
        it.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            it.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await s.commit()
        logger.info(
            "bling_persist_ok",
            integration_id=str(integration_id),
            new_rt_prefix=str(new_creds.get("refresh_token", ""))[:8],
            expires_at=exp,
        )
