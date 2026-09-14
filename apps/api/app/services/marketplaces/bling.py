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


# Cache do depósito padrão por integração (id estável; raramente muda). O Bling
# exige idDeposito no POST /estoques, então resolvemos uma vez e reusamos.
_DEFAULT_DEPOSIT_CACHE: dict[Any, int] = {}


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
                    # 1h (não 30min): empiricamente o ban CF do Bling dura
                    # mais que 30min, então 1800s causava loop "cooldown
                    # expira → 1º request 429 → cooldown re-armado".
                    CF_COOLDOWN_S = 3600
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
        """Lista os canais de venda do Bling (id, nome, tipo, situacao).

        Bling v3 aposentou o GET /lojas (passou a responder 404). O
        substituto e /canais-venda, que pagina por `pagina`/`limite` e
        retorna `descricao` (nao `nome`). Normalizamos `nome` <- `descricao`
        para preservar o shape esperado por quem consome (registro de loja),
        e percorremos as paginas ate vir vazio.

        So retornamos canais ativos (`situacao == 1`): o /canais-venda
        devolve tambem dezenas de canais desativados/legados (situacao 2,
        ex.: "Shopee - 85.") que poluiriam o seletor de cadastro de loja.
        """
        out: list[dict] = []
        page = 1
        while True:
            r = await self._request(
                "GET", "/canais-venda", params={"pagina": page, "limite": 100}
            )
            r.raise_for_status()
            rows = r.json().get("data", []) or []
            if not rows:
                break
            for row in rows:
                if row.get("situacao") != 1:
                    continue
                row.setdefault("nome", row.get("descricao"))
                out.append(row)
            page += 1
        return out

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
        data_alteracao_inicial: str | None = None,
        data_alteracao_final: str | None = None,
        id_situacao: int | None = None,
        id_loja: int | None = None,
        pagina: int = 1,
        limite: int = 100,
    ) -> list[dict]:
        """Single page of `/pedidos/vendas` with the marketing module's
        filter set. Bling v3 expects YYYY-MM-DD for dates and integer ids
        for situação/loja.

        `data_alteracao_inicial`/`data_alteracao_final` filtram pela data
        de ÚLTIMA ALTERAÇÃO do pedido e exigem timestamp completo
        (`YYYY-MM-DD HH:MM:SS`, horário de Brasília) — usado pela varredura
        horária que recupera webhooks perdidos.

        situação 9 = "Atendido" (NF emitida) — the canonical "faturado"
        signal the aggregator uses as authoritative revenue. Other useful
        situações: 6 (Em aberto), 15 (Em andamento), 21 (Em digitação),
        12 (Cancelado).
        Pagination via `pagina` + `limite`; caller iterates pages."""
        params: dict[str, Any] = {"pagina": pagina, "limite": limite}
        if data_inicial:
            params["dataInicial"] = data_inicial
        if data_final:
            params["dataFinal"] = data_final
        if data_alteracao_inicial:
            params["dataAlteracaoInicial"] = data_alteracao_inicial
        if data_alteracao_final:
            params["dataAlteracaoFinal"] = data_alteracao_final
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
        data_alteracao_inicial: str | None = None,
        data_alteracao_final: str | None = None,
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
                data_alteracao_inicial=data_alteracao_inicial,
                data_alteracao_final=data_alteracao_final,
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

    async def create_conta_pagar(
        self,
        *,
        contato_id: int,
        valor: float,
        vencimento: str,
        data_emissao: str | None = None,
        historico: str | None = None,
        categoria_id: int | None = None,
        numero_documento: str | None = None,
    ) -> dict:
        """Cria uma conta a pagar (POST /contas/pagar).

        Usado pelo fluxo da DI (Importação): ao anexar a DI o operador
        pode lançar o pagamento parcelado em nome do contato isatrading.
        Datas em YYYY-MM-DD. Retorna o `data` do Bling ({"id": ...}).

        `competencia` é obrigatória no Bling e só aceita YYYY-MM-DD; sem
        ela vem 400 "A competência está inválida".
        """
        body: dict = {
            "contato": {"id": contato_id},
            "vencimento": vencimento,
            "valor": valor,
            "competencia": data_emissao or vencimento,
        }
        if data_emissao:
            body["dataEmissao"] = data_emissao
        if historico:
            body["historico"] = historico
        if categoria_id is not None:
            body["categoria"] = {"id": categoria_id}
        if numero_documento:
            body["numeroDocumento"] = numero_documento
        r = await self._request("POST", "/contas/pagar", json=body)
        r.raise_for_status()
        return r.json().get("data") or {}

    async def update_order(self, bling_order_id: int, body: dict) -> dict:
        """Substitui o pedido de venda (PUT /pedidos/vendas/{id}).

        O Bling v3 não tem PATCH parcial de pedido: pra mexer em um campo
        (ex.: Observações) é preciso reenviar o pedido INTEIRO. O `body` já
        deve vir sanitizado (referências por id, sem campos calculados) —
        ver `logistica_bling.build_observacoes_put_body`. Retorna o pedido
        atualizado.
        """
        r = await self._request("PUT", f"/pedidos/vendas/{bling_order_id}", json=body)
        r.raise_for_status()
        return r.json().get("data") or {}

    async def get_default_deposit_id(self) -> int | None:
        """idDeposito do depósito padrão (padrao=True, senão o primeiro ativo).
        Cacheado por integração — o Bling exige idDeposito no POST /estoques."""
        key = self._integration_id
        if key in _DEFAULT_DEPOSIT_CACHE:
            return _DEFAULT_DEPOSIT_CACHE[key]
        try:
            r = await self._request("GET", "/depositos", params={"pagina": 1, "limite": 100})
            r.raise_for_status()
            deps = r.json().get("data") or []
        except Exception:  # noqa: BLE001
            return None
        chosen = next((d.get("id") for d in deps if d.get("padrao")), None)
        if chosen is None and deps:
            chosen = deps[0].get("id")
        if chosen is None:
            return None
        _DEFAULT_DEPOSIT_CACHE[key] = int(chosen)
        return int(chosen)

    async def update_stock_by_id(
        self,
        bling_product_id: int,
        qty: int,
        *,
        bling_store_id: int | None = None,
        operation: str = "B",
        deposit_id: int | None = None,
        observacao: str | None = None,
        custo: float | None = None,
    ) -> dict:
        """Raw Bling stock write. POST /Api/v3/estoques.

        `idDeposito` é obrigatório no Bling V3 — quando `deposit_id` não é
        passado, resolvemos o depósito padrão automaticamente. `bling_store_id`
        vira `idLoja` para refletir no canal certo. `observacao` vira
        `observacoes` (aparece na coluna Observação do extrato de estoque).
        `custo` (>0) vira `custo` no body — o preço de custo do LANÇAMENTO,
        que alimenta o custo médio do Bling e a coluna "Preço de Custo" do
        extrato; sem ele a entrada fica com custo 0. Body:
            { "produto": {"id": <id>}, "operacao": "B"|"S"|"E", "quantidade": <qty>,
              "deposito": {"id": <id>}, "idLoja": <int?>, "observacoes": <str?>,
              "custo": <float?> }
        """
        body: dict[str, Any] = {
            "produto": {"id": bling_product_id},
            "operacao": operation,
            "quantidade": qty,
        }
        if deposit_id is None:
            deposit_id = await self.get_default_deposit_id()
        if deposit_id is not None:
            body["deposito"] = {"id": deposit_id}
        if bling_store_id is not None:
            body["idLoja"] = bling_store_id
        if observacao:
            body["observacoes"] = observacao
        if custo is not None and custo > 0:
            body["custo"] = float(custo)
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

        # 200 with an empty `data` payload (get_product coalesces to {}):
        # nothing to parse. Without this guard `parse_bling_product({})`
        # KeyErrors on the missing `id`, which the orchestrator's blanket
        # except turned into RETRYABLE — the pre-smart path in _refresh_bling
        # (`parse_bling_product(raw) if raw else {}`) treated it as
        # stock-missing → SKIPPED. Keep that semantic.
        if not raw:
            return {
                "stock": None,
                "bling_product_id": bling_product_id,
                "found_via": "direct",
                "raw": raw,
            }

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

    async def cost_by_skus(self, skus: set[str]) -> dict[str, float]:
        """`precoCusto` atual no Bling para cada SKU, via listagem `/produtos`.

        O `precoCusto` só vem na LISTA (`/produtos?codigo=`), não no detalhe
        `/produtos/{id}`. Usado no refresh on-ingest: busca só os SKUs do
        pedido cujo custo local está velho. Falha por SKU é silenciosa (o
        chamador cai no `bling_cost_price` já gravado)."""
        out: dict[str, float] = {}
        for sku in skus:
            if not sku:
                continue
            try:
                r = await self._request(
                    "GET",
                    "/produtos",
                    params={"codigo": sku, "pagina": 1, "limite": 5},
                )
                r.raise_for_status()
                for item in r.json().get("data") or []:
                    if (item.get("codigo") or "").strip() != sku:
                        continue
                    raw_cost = item.get("precoCusto")
                    if raw_cost in (None, ""):
                        continue
                    try:
                        out[sku] = float(raw_cost)
                    except (TypeError, ValueError):
                        pass
                    break
            except Exception:  # noqa: BLE001
                continue
        return out

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
          * "E" = Com composição (kit/composto). Bling V3 (rev. 2026-06)
            agora EXIGE `estrutura` completa no POST quando formato="E".
            Antes descartava silenciosamente (fix 261bda0 separou em PUT);
            comportamento mudou — voltamos a enviar tudo no POST.
            `update_product_estrutura` permanece como reforço pra retry.

        `estrutura` shape:
          {
            "tipoEstoque": "F" | "V",            # F=Físico, V=Virtual
            "lancamentoEstoque": "A" | "M" | "P", # A=Produto+Componente, M=Componente, P=Produto
            "componentes": [
              {"produto": {"id": <int>}, "quantidade": <float>},
              ...
            ]
          }

        O custo (precoCusto) NÃO entra aqui — Bling V3 descarta
        silenciosamente o bloco `fornecedor` no body do POST /produtos.
        O único caminho que faz o custo persistir é o endpoint separado
        `POST /produtos/fornecedores` (ver `link_supplier_to_product`),
        chamado pelo caller depois deste create retornar o id.
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

    async def update_product_estrutura(
        self, *, product_id: int, estrutura: dict,
    ) -> dict:
        """Set/replace a composed product's estrutura via PUT
        /Api/v3/produtos/estruturas/{id}.

        Mesmo padrão de `link_supplier_to_product`: o bloco `estrutura`
        no body do POST /produtos é silenciosamente descartado por
        Bling V3 (testado 2026-06-01 com kit b057.8.18 — formato="E" e
        tipoEstoque foram preservados, mas `lancamentoEstoque` e
        `componentes` voltaram zerados). Único caminho que faz os
        componentes persistirem é este endpoint dedicado, mapeado pelo
        SDK AlexandreBellas/bling-erp-api-js como `produtosEstruturas`.

        Body shape (mesmo do create_product):
          {
            "tipoEstoque": "F" | "V",
            "lancamentoEstoque": "A" | "M" | "P",
            "componentes": [
              {"produto": {"id": <int>}, "quantidade": <float>},
              ...
            ]
          }
        """
        r = await self._request(
            "PUT", f"/produtos/estruturas/{int(product_id)}", json=estrutura,
        )
        r.raise_for_status()
        # Bling responde sem body (SDK tipa como Promise<null>); só
        # tentar parsear se houver content-type JSON.
        if r.content and "json" in (r.headers.get("content-type") or ""):
            try:
                return r.json().get("data") or {}
            except ValueError:
                return {}
        return {}

    async def link_supplier_to_product(
        self, *, product_id: int, supplier_id: int, cost_price: float,
    ) -> dict:
        """Upsert do relacionamento produto↔fornecedor no Bling V3 com custo.

        Tenta POST /produtos/fornecedores (cria); se já existir (Bling
        retorna 400 + code=279 "Registro duplicado"), busca o link
        existente via GET e atualiza com PUT. Comportamento upsert
        efetivo — único caminho que faz precoCusto persistir, e que
        funciona tanto na 1ª chamada quanto em re-runs (companion ao
        restore de componentes do bling_kit_create, que pode tentar
        re-aplicar custo em link já existente).

        Custo <= 0 é no-op (retorna {}).
        """
        if cost_price <= 0:
            return {}
        body = {
            "idProduto": int(product_id),
            "idContato": int(supplier_id),
            "precoCusto": float(cost_price),
        }
        try:
            r = await self._request("POST", "/produtos/fornecedores", json=body)
            r.raise_for_status()
            return r.json().get("data") or {}
        except httpx.HTTPStatusError as e:
            # Bling V3: 400 + code=279 "Registro duplicado" = link já
            # existe pra esse (produto, fornecedor). Cai pro update.
            if e.response.status_code != 400:
                raise
            try:
                err_fields = (e.response.json().get("error") or {}).get("fields") or []
            except ValueError:
                raise e from None
            is_duplicate = any(f.get("code") == 279 for f in err_fields)
            if not is_duplicate:
                raise
            return await self._update_existing_supplier_link(
                product_id=product_id,
                supplier_id=supplier_id,
                cost_price=cost_price,
            )

    async def _update_existing_supplier_link(
        self, *, product_id: int, supplier_id: int, cost_price: float,
    ) -> dict:
        """Quando POST retorna duplicado: lista os links do produto
        pelo fornecedor, pega o id da relação e atualiza via PUT.

        Endpoints (Bling V3, SDK AlexandreBellas/bling-erp-api-js):
          GET  /produtos/fornecedores?idProduto=X&idFornecedor=Y
          PUT  /produtos/fornecedores/{idProdutoFornecedor}
            body: {produto:{id}, fornecedor:{id}, precoCusto}
              (shape nested — NÃO é {idProduto, idContato, precoCusto}
              como no POST; PUT segue o padrão `produto:{id}` do V3.)
        """
        r = await self._request(
            "GET", "/produtos/fornecedores",
            params={"idProduto": int(product_id), "idFornecedor": int(supplier_id)},
        )
        r.raise_for_status()
        items = r.json().get("data") or []
        if not items:
            # Inconsistência rara: Bling disse duplicado no POST mas
            # GET veio vazio. Sem id pra atualizar — desiste sem raise
            # pra não derrubar o caller (fluxo de restore de custos).
            return {}
        link_id = items[0].get("id")
        if link_id is None:
            return {}
        put_body = {
            "produto": {"id": int(product_id)},
            "fornecedor": {"id": int(supplier_id)},
            "precoCusto": float(cost_price),
        }
        r = await self._request(
            "PUT", f"/produtos/fornecedores/{int(link_id)}", json=put_body,
        )
        r.raise_for_status()
        # PUT pode responder sem body (vimos isso em update_product_estrutura).
        if r.content and "json" in (r.headers.get("content-type") or ""):
            try:
                return r.json().get("data") or {}
            except ValueError:
                return {}
        return {}

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
        # Bling v3 GET /produtos/{id} devolve categoria só com `id` (sem
        # descricao/nome). Guardamos o id (mesmo formato dos produtos legados);
        # o resolvedor de categoria (bling_orders / product_categories) casa
        # tanto por id quanto por nome. Sem o id (fallback), usa o nome.
        category = categoria.get("descricao") or categoria.get("nome")
        if not category and categoria.get("id") is not None:
            category = str(categoria["id"])
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
