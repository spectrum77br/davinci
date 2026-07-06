"""Magalu (Magazine Luiza) Open API client — conector OAuth nativo.

Espelha `MercadoLivreClient` (OAuth2 authorization_code + refresh automático em
401), mas o portfólio Magalu é endereçado por **SKU**, não por item_id +
variation_id. Logo, para links Magalu:
  * `link.external_id` == o SKU do seller na Magalu (forma com hífen);
  * `link.variation_id` == None;
  * stock/price dão PATCH em `/seller/v1/portfolios/{stocks|prices}/{sku}`.

⚠️ Regra de SKU (Magalu NÃO aceita ponto no SKU, só hífen):
O SKU canônico do sistema (Bling + todos os outros marketplaces) usa PONTO como
separador — ex. `b001.20`. Na Magalu o mesmo produto vira `b001-20` (hífen). Como
NUNCA existiu hífen real nos SKUs canônicos (o separador sempre foi o ponto), a
conversão hífen→ponto é inequívoca. Por isso `list_listings` emite:
  * `external_id = "b001-20"`  (SKU cru da Magalu — usado no PATCH e gravado em
    product_links.external_id);
  * `sku        = "b001.20"`  (forma canônica — casa com `products.sku` nos DOIS
    caminhos de vínculo: o SQL exato de `listings_import._link_by_sku` E o
    `_norm_sku` de `auto_link`).
Assim, ao vincular, o estoque do produto `b001.20` flui pro anúncio `b001-20`.

Credenciais (em `integrations.credentials`, cifradas):
    {
      "access_token":  str,
      "refresh_token": str  (⚠️ SINGLE-USE — rotaciona a cada refresh),
      "expires_at":    int  (epoch seconds),
      "scope":         str,
    }
O app OAuth é ÚNICO/compartilhado (nível env): client_id/secret vêm de
`settings.magalu_*`, não das creds da integração.

Semântica confirmada na doc oficial (developers.magalu.com, API "Produtos"):
  * List SKUs : GET  /seller/v1/portfolios/skus?_limit=<=100&_offset=N&_sort=...
                → {"results": [ {sku,title,status,...} ], "meta":..., "links":...}
                (o portfólio NÃO traz stock/price inline — são sub-recursos.)
  * PATCH stock: PATCH /seller/v1/portfolios/stocks/{sku}
                body {"quantity": <int>, "type": "AVAILABLE"} → 202 (async = OK)
  * PATCH price: PATCH /seller/v1/portfolios/prices/{sku}
                body {"price": <cent>, "list_price": <cent>, "currency": "BRL",
                "normalizer": 100} → 202. Preços são INTEIROS em centavos
                (normalizer=100 é o divisor: 9980 == R$ 99,80).
O token já é escopado ao tenant escolhido no login (choose_tenants=true), então
as chamadas em api.magalu.com NÃO exigem header de tenant.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

import httpx
import structlog

from app.config import get_settings
from app.services.marketplaces.base import SyncResult, SyncStatus, TestResult

if TYPE_CHECKING:
    from app.models import ProductLink

logger = structlog.get_logger()

MAGALU_AUTH_URL = "https://id.magalu.com/login"
MAGALU_TOKEN_URL = "https://id.magalu.com/oauth/token"
MAGALU_API_BASE = "https://api.magalu.com"

# Os 11 scopes concedidos ao app "DavinciERP" no ID Magalu (portfólio r/w,
# estoque r/w, preço r/w, categorias r, pedidos/nf/entrega). Separados por
# espaço no parâmetro `scope` do authorize.
MAGALU_SCOPES = " ".join(
    [
        "open:portfolio-skus-seller:read",
        "open:portfolio-skus-seller:write",
        "open:portfolio-stocks-seller:read",
        "open:portfolio-stocks-seller:write",
        "open:portfolio-prices-seller:read",
        "open:portfolio-prices-seller:write",
        "open:portfolio-categories-seller:read",
        "open:order-order-seller:read",
        "open:order-invoice-seller:read",
        "open:order-delivery-seller:read",
        "open:order-delivery-seller:write",
    ]
)

# Máximo de itens por página aceito pelo list de SKUs (schema: _limit max=100).
_MAX_PAGE_SIZE = 100


class MagaluClient:
    def __init__(self, creds: dict, on_token_refresh=None):
        self.creds = dict(creds)
        self._on_refresh = on_token_refresh

    @property
    def access_token(self) -> str | None:
        return self.creds.get("access_token")

    @property
    def expires_at(self) -> int:
        return int(self.creds.get("expires_at") or 0)

    def _expired(self, skew: int = 30) -> bool:
        # expires_at == 0 means unknown — trust access_token until a 401.
        if self.expires_at == 0:
            return False
        return self.expires_at - skew <= int(time.time())

    @staticmethod
    def authorize_url(
        state: str,
        *,
        client_id: str | None = None,
        redirect_uri: str | None = None,
        scope: str | None = None,
    ) -> str:
        """Monta a URL de login/authorize do ID Magalu.

        `choose_tenants=true` é OBRIGATÓRIO para seller — o vendedor escolhe o
        tenant/loja no login e o token sai escopado a ele. `client_id`/
        `redirect_uri` default = app único do env (`settings.magalu_*`).
        """
        s = get_settings()
        params = {
            "response_type": "code",
            "client_id": client_id or s.magalu_client_id,
            "redirect_uri": redirect_uri or s.magalu_redirect_uri,
            "scope": scope or MAGALU_SCOPES,
            "state": state,
            "choose_tenants": "true",
        }
        return f"{MAGALU_AUTH_URL}?{urlencode(params)}"

    @staticmethod
    async def exchange_code(
        code: str,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
    ) -> dict:
        """Troca o authorization_code por tokens. ID Magalu espera **JSON** aqui
        (o refresh, ao contrário, é form-urlencoded)."""
        s = get_settings()
        cid = client_id or s.magalu_client_id
        csec = client_secret or s.magalu_client_secret
        ruri = redirect_uri or s.magalu_redirect_uri
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                MAGALU_TOKEN_URL,
                headers={"Accept": "application/json"},
                json={
                    "grant_type": "authorization_code",
                    "client_id": cid,
                    "client_secret": csec,
                    "code": code,
                    "redirect_uri": ruri,
                },
            )
            r.raise_for_status()
            return _normalize_token(r.json())

    async def refresh(self) -> None:
        """Renova o access_token. ⚠️ O refresh_token é SINGLE-USE: cada refresh
        devolve um NOVO refresh_token e invalida o anterior — por isso persistimos
        via `on_token_refresh` sempre. Sem isso, a conta cai no refresh seguinte.
        O endpoint de refresh espera **form-urlencoded** (não JSON)."""
        rt = self.creds.get("refresh_token")
        if not rt:
            raise RuntimeError("missing refresh_token")
        s = get_settings()
        cid = str(self.creds.get("client_id") or s.magalu_client_id or "")
        csec = str(self.creds.get("client_secret") or s.magalu_client_secret or "")
        if not cid or not csec:
            raise RuntimeError("missing client_id or client_secret")
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                MAGALU_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "grant_type": "refresh_token",
                    "client_id": cid,
                    "client_secret": csec,
                    "refresh_token": rt,
                },
            )
            if r.status_code >= 400:
                raise RuntimeError(
                    f"magalu_refresh_failed status={r.status_code} body={r.text[:300]}"
                )
            self.creds.update(_normalize_token(r.json(), prev=self.creds))
        if self._on_refresh:
            await self._on_refresh(self.creds)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
    ) -> httpx.Response:
        if self._expired():
            await self.refresh()
        url = f"{MAGALU_API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        delay = 1.0
        r: httpx.Response | None = None
        for attempt in range(3):
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.request(
                    method, url, headers=headers, params=params, json=json
                )
            if r.status_code == 401 and attempt == 0:
                await self.refresh()
                headers["Authorization"] = f"Bearer {self.access_token}"
                continue
            if r.status_code in (429, 502, 503, 504):
                logger.warning(
                    "magalu_retry", attempt=attempt + 1, status=r.status_code, path=path
                )
                await asyncio.sleep(delay)
                delay *= 2
                continue
            return r
        assert r is not None
        return r

    async def test_connection(self) -> TestResult:
        try:
            r = await self._request(
                "GET", "/seller/v1/portfolios/skus", params={"_limit": 1}
            )
            if r.status_code == 200:
                data = r.json() or {}
                total = None
                meta = data.get("meta") or {}
                if isinstance(meta, dict):
                    page = meta.get("page") or {}
                    if isinstance(page, dict):
                        total = page.get("total") or page.get("count")
                return TestResult(ok=True, info={"portfolio_total": total})
            return TestResult(
                ok=False, detail=f"status={r.status_code} body={r.text[:200]}"
            )
        except httpx.HTTPError as e:
            return TestResult(ok=False, detail=f"http_error: {e}")
        except Exception as e:  # noqa: BLE001
            return TestResult(ok=False, detail=f"error: {e}")

    async def get_sku(self, sku: str) -> dict:
        """Consulta 1 SKU do portfólio. `sku` é a forma crua (hífen) da Magalu."""
        r = await self._request(
            "GET", f"/seller/v1/portfolios/skus/{quote(sku, safe='')}"
        )
        r.raise_for_status()
        return r.json() or {}

    async def update_stock(
        self,
        link: ProductLink,
        qty: int,
        *,
        bling_store_id: int | None = None,  # ignored on Magalu side
        force: bool = False,
    ) -> SyncResult:
        """PATCH do estoque no SKU da Magalu (`link.external_id`).

        Mantém o **guard B1** do ML: nunca empurra qty=0 quando a origem tinha
        estoque positivo, salvo `force=True` (sync manual/individual). O endpoint
        responde 202 (processamento assíncrono) — tratamos como sucesso.
        """
        del bling_store_id  # not used; signature kept for ABC parity
        qty_before = link.stock

        # B1 guard ------------------------------------------------------------
        if not force and qty == 0 and (qty_before or 0) > 0:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                qty_before=qty_before,
                error_code="b1_guard_zero_block",
                error_detail=(
                    "refused to push qty=0 when source stock was positive; "
                    "verify origin before unblocking"
                ),
            )

        sku = (link.external_id or "").strip()
        if not sku:
            return SyncResult(
                status=SyncStatus.FATAL,
                qty_before=qty_before,
                error_code="magalu_missing_sku",
                error_detail="link.external_id vazio",
            )

        try:
            r = await self._request(
                "PATCH",
                f"/seller/v1/portfolios/stocks/{quote(sku, safe='')}",
                json={"quantity": int(qty), "type": "AVAILABLE"},
            )
        except httpx.HTTPError as e:
            return _map_http_error(e, qty_before, "magalu_patch_stock_failed")
        # 202 Accepted = update aceito (processamento assíncrono).
        if r.status_code in (200, 202):
            return SyncResult(
                status=SyncStatus.OK,
                qty_before=qty_before,
                qty_after=qty,
                payload={"sku": sku, "http_status": r.status_code},
            )
        if r.status_code == 404:
            return SyncResult(
                status=SyncStatus.FATAL,
                qty_before=qty_before,
                error_code="magalu_sku_not_found",
                error_detail=f"sku={sku}",
            )
        return _map_status_error(r, qty_before, "magalu_patch_stock_status")

    async def update_price(
        self,
        item_id: str,
        price: float,
        *,
        variation_id: str | None = None,  # ignored on Magalu side
    ) -> SyncResult:
        """PATCH do preço no SKU da Magalu (`item_id` == SKU cru).

        `price` chega em REAIS (float), como no ML. A Magalu espera INTEIROS em
        centavos (normalizer=100). Enviamos `price` e `list_price` iguais ao alvo
        pra não deixar um "de/por" defasado mostrando desconto fantasma. 202 = OK.
        """
        del variation_id  # Magalu addresses by SKU only
        cents = int(round(float(price) * 100))
        if cents <= 0:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                error_code="invalid_price",
                error_detail=f"price={price}",
            )
        sku = (item_id or "").strip()
        if not sku:
            return SyncResult(
                status=SyncStatus.FATAL,
                error_code="magalu_missing_sku",
                error_detail="item_id vazio",
            )
        try:
            r = await self._request(
                "PATCH",
                f"/seller/v1/portfolios/prices/{quote(sku, safe='')}",
                json={
                    "price": cents,
                    "list_price": cents,
                    "currency": "BRL",
                    "normalizer": 100,
                },
            )
        except httpx.HTTPError as e:
            return _map_http_error(e, None, "magalu_patch_price_failed")
        if r.status_code in (200, 202):
            return SyncResult(
                status=SyncStatus.OK,
                payload={"sku": sku, "price_cents": cents, "http_status": r.status_code},
            )
        if r.status_code == 404:
            return SyncResult(
                status=SyncStatus.FATAL,
                error_code="magalu_sku_not_found",
                error_detail=f"sku={sku}",
            )
        return _map_status_error(r, None, "magalu_patch_price_status")

    async def list_listings(
        self,
        *,
        page_size: int = 50,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict]:
        """Itera o portfólio de SKUs, um dict normalizado por SKU.

        Pagina `GET /seller/v1/portfolios/skus` com `_limit`/`_offset`. O
        portfólio é catálogo puro (sku, título, status) — NÃO traz stock/price,
        então esses campos saem None (o estoque flui do Bling PRA Magalu via
        `update_stock`, nunca ao contrário; o vínculo casa por SKU).
        """
        limit = min(max(int(page_size), 1), _MAX_PAGE_SIZE)
        offset = 0
        page_idx = 0
        while True:
            if max_pages is not None and page_idx >= max_pages:
                break
            r = await self._request(
                "GET",
                "/seller/v1/portfolios/skus",
                params={
                    "_limit": limit,
                    "_offset": offset,
                    "_sort": "created_at:asc",
                },
            )
            if r.status_code != 200:
                raise RuntimeError(
                    f"magalu_list_skus_failed status={r.status_code} body={r.text[:200]}"
                )
            data = r.json() or {}
            results = data.get("results") or []
            if not results:
                break
            for item in results:
                normalized = _normalize_magalu_sku(item)
                if normalized is not None:
                    yield normalized
            # Última página: menos itens que o limite pedido.
            if len(results) < limit:
                break
            offset += limit
            page_idx += 1
            await asyncio.sleep(0.3)


# ---------------------------------------------------------------- helpers


def _canonical_sku(raw: str) -> str:
    """Converte o SKU cru da Magalu (hífen) na forma canônica do sistema (ponto).

    A Magalu não aceita ponto no SKU, só hífen; e como o separador canônico
    SEMPRE foi o ponto (nunca houve hífen real), trocar todo hífen por ponto é
    inequívoco — é o que faz `b001-20` (Magalu) casar com `b001.20` (products).
    """
    return (raw or "").replace("-", ".")


# Magalu status → ListingStatus (o `listings_import._coerce_status` faz o parse
# final; strings desconhecidas viram INACTIVE lá).
_MAGALU_STATUS_MAP = {
    "PUBLISHED": "active",
    "UNPUBLISHED": "paused",
    "UNDER_REVIEW": "under_review",
    "BLOCKED": "inactive",
    "DELETING": "inactive",
    "DELETED": "closed",
}


def _map_magalu_status(raw: str | None) -> str:
    if not raw:
        return "active"
    return _MAGALU_STATUS_MAP.get(str(raw).strip().upper(), "inactive")


def _first_image_url(item: dict) -> str | None:
    images = item.get("images")
    if isinstance(images, list):
        for img in images:
            if isinstance(img, dict):
                url = img.get("url") or img.get("reference")
                if url:
                    return str(url)
    return None


def _normalize_magalu_sku(item: dict) -> dict | None:
    """Um SKU do portfólio → dict normalizado no formato do `_upsert_listing`.

    `external_id` fica com o SKU CRU (hífen — id do recurso na Magalu, usado no
    PATCH); `sku` recebe a forma canônica (ponto) pra casar com `products.sku`.
    """
    if not isinstance(item, dict):
        return None
    raw_sku = (item.get("sku") or "").strip()
    if not raw_sku:
        return None
    return {
        "external_id": raw_sku,
        "variation_id": None,
        "sku": _canonical_sku(raw_sku),
        "title": (item.get("title") or "").strip(),
        "description": item.get("description"),
        "price": None,  # portfólio não traz preço inline (sub-recurso separado)
        "stock": None,  # idem estoque
        "status": _map_magalu_status(item.get("status")),
        "category": None,
        "thumbnail_url": _first_image_url(item),
        "listing_type": None,
        "raw": item,
    }


def _map_http_error(
    e: httpx.HTTPError, qty_before: int | None, code: str
) -> SyncResult:
    status: SyncStatus = SyncStatus.RETRYABLE
    detail = str(e)[:500]
    response = getattr(e, "response", None)
    http_code = response.status_code if response is not None else None
    if http_code in {400, 401, 403, 404, 422}:
        status = SyncStatus.FATAL
    return SyncResult(
        status=status,
        qty_before=qty_before,
        error_code=f"{code}_{http_code}" if http_code else code,
        error_detail=detail,
    )


def _map_status_error(
    r: httpx.Response, qty_before: int | None, code: str
) -> SyncResult:
    if r.status_code in {429, 502, 503, 504}:
        status = SyncStatus.RETRYABLE
    elif r.status_code in {401, 403, 400, 422}:
        status = SyncStatus.FATAL
    else:
        status = SyncStatus.RETRYABLE
    try:
        body = getattr(r, "text", None)
        if not isinstance(body, str):
            content = getattr(r, "content", None)
            body = (
                content.decode("utf-8", "replace")
                if isinstance(content, bytes)
                else str(r)
            )
    except Exception:  # noqa: BLE001
        body = str(r)
    return SyncResult(
        status=status,
        qty_before=qty_before,
        error_code=f"{code}_{r.status_code}",
        error_detail=body[:500],
    )


def _normalize_token(payload: dict, prev: dict | None = None) -> dict:
    """Normaliza a resposta do token endpoint em creds persistíveis.

    ⚠️ Preserva/rotaciona o refresh_token: a Magalu devolve um novo a cada
    refresh (single-use). `prev` mantém client_id/secret e demais campos.
    """
    expires_in = int(payload.get("expires_in") or 7200)
    out: dict = dict(prev or {})
    out["access_token"] = payload["access_token"]
    if payload.get("refresh_token"):
        out["refresh_token"] = payload["refresh_token"]
    out["scope"] = payload.get("scope", out.get("scope", ""))
    out["token_type"] = payload.get("token_type", out.get("token_type", "Bearer"))
    out["expires_at"] = int(time.time()) + expires_in
    out["_obtained_at"] = datetime.now(UTC).isoformat()
    return out
