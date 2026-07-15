"""Mercado Livre API client (Fase 4b.ML).

Implements `MarketplaceClient` for ML; covers PRD §11 Fase 4b.ML and resolves
bugs B1 (auto-link zeroing stock), B2 (links with stock=0/last_sync_at=NULL),
B3 (variation_not_found intermittent).

OAuth2 (authorization code), automatic refresh on 401.

Credential shape stored in `integrations.credentials`:
    {
      "client_id":     str,
      "client_secret": str,
      "access_token":  str,
      "refresh_token": str,
      "user_id":       int (ML seller id, populated after first /users/me),
      "expires_at":    int (epoch seconds),
    }

ML stock writes:
- Listings without variations  : PUT /items/{item_id}        body={"available_quantity": qty}
- Listings *with* variations   : PUT /items/{item_id}        body={"variations": [{"id": var_id, "available_quantity": qty}]}

If the variation_id stored locally is no longer present on ML (variations were
edited in the seller dashboard), `update_stock` walks the variations on the
listing matching by `seller_custom_field` (== local SKU). When found, the link
is repointed (B3); when not, the link is marked `requires_review`.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx
import structlog

from app.config import get_settings
from app.services.marketplaces.base import SyncResult, SyncStatus, TestResult

if TYPE_CHECKING:
    from app.models import ProductLink

logger = structlog.get_logger()

ML_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
ML_API_BASE = "https://api.mercadolibre.com"


class MercadoLivreClient:
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
        # expires_at == 0 means unknown — trust access_token until ML returns 401.
        if self.expires_at == 0:
            return False
        return self.expires_at - skew <= int(time.time())

    @staticmethod
    def authorize_url(
        state: str,
        *,
        client_id: str | None = None,
        redirect_uri: str | None = None,
    ) -> str:
        """Build the ML authorize URL.

        `client_id`/`redirect_uri` default to the global env app when omitted
        (generic `/api/oauth/ml/*` flow). The integration-bound flow passes the
        integration's own credentials so each seller uses their own ML app.
        """
        s = get_settings()
        params = {
            "response_type": "code",
            "client_id": client_id or s.ml_client_id,
            "redirect_uri": redirect_uri or s.ml_redirect_uri,
            "state": state,
        }
        return f"{ML_AUTH_URL}?{urlencode(params)}"

    @staticmethod
    async def exchange_code(
        code: str,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
    ) -> dict:
        """Exchange an auth code for tokens.

        `client_id`/`client_secret`/`redirect_uri` default to the env app when
        omitted. The one actually used is written back into the returned creds
        so `refresh()` can reuse it.
        """
        s = get_settings()
        cid = client_id or s.ml_client_id
        csec = client_secret or s.ml_client_secret
        ruri = redirect_uri or s.ml_redirect_uri
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                ML_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "grant_type": "authorization_code",
                    "client_id": cid,
                    "client_secret": csec,
                    "code": code,
                    "redirect_uri": ruri,
                },
            )
            r.raise_for_status()
            creds = _normalize_token(r.json())
            creds["client_id"] = cid
            creds["client_secret"] = csec
            return creds

    def _client_creds(self) -> tuple[str, str]:
        """Per-integration client_id/secret when present, else env fallback."""
        s = get_settings()
        cid = str(self.creds.get("client_id") or s.ml_client_id or "")
        csec = str(self.creds.get("client_secret") or s.ml_client_secret or "")
        return cid, csec

    async def refresh(self) -> None:
        rt = self.creds.get("refresh_token")
        if not rt:
            raise RuntimeError("missing refresh_token")
        cid, csec = self._client_creds()
        if not cid or not csec:
            raise RuntimeError("missing client_id or client_secret")
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                ML_TOKEN_URL,
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
                    f"ml_refresh_failed status={r.status_code} body={r.text[:300]}"
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
        url = f"{ML_API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        delay = 1.0
        for attempt in range(3):
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.request(method, url, headers=headers, params=params, json=json)
            if r.status_code == 401 and attempt == 0:
                await self.refresh()
                headers["Authorization"] = f"Bearer {self.access_token}"
                continue
            if r.status_code in (429, 502, 503, 504):
                logger.warning(
                    "ml_retry", attempt=attempt + 1, status=r.status_code, path=path
                )
                await asyncio.sleep(delay)
                delay *= 2
                continue
            return r
        return r

    async def test_connection(self) -> TestResult:
        try:
            r = await self._request("GET", "/users/me")
            if r.status_code == 200:
                data = r.json() or {}
                if data.get("id") and self.creds.get("user_id") != data["id"]:
                    self.creds["user_id"] = data["id"]
                    if self._on_refresh:
                        await self._on_refresh(self.creds)
                return TestResult(ok=True, info={"id": data.get("id"), "nick": data.get("nickname")})
            return TestResult(ok=False, detail=f"status={r.status_code} body={r.text[:200]}")
        except httpx.HTTPError as e:
            return TestResult(ok=False, detail=f"http_error: {e}")
        except Exception as e:  # noqa: BLE001
            return TestResult(ok=False, detail=f"error: {e}")

    async def get_item(self, item_id: str) -> dict:
        r = await self._request("GET", f"/items/{item_id}")
        r.raise_for_status()
        return r.json() or {}

    async def get_order(self, order_id: str) -> dict:
        r = await self._request("GET", f"/orders/{order_id}")
        r.raise_for_status()
        return r.json() or {}

    async def get_pack(self, pack_id: str) -> dict:
        """Fetch a pack (cart) and its sibling orders. ML groups items bought
        together into a pack; each item can be a separate order_id sharing one
        shipment. Returns `{id, orders: [{id, ...}], ...}`."""
        r = await self._request("GET", f"/packs/{pack_id}")
        r.raise_for_status()
        return r.json() or {}

    async def get_shipment(self, shipment_id: str) -> dict:
        """Fetch shipment details. Used by the shipment-check sweep to read
        `substatus=dropped_off`, which fires when the seller hands the
        package to the agency — the order itself still reports
        `status=paid` at that point but the ML UI already shows "A caminho"."""
        r = await self._request("GET", f"/shipments/{shipment_id}")
        r.raise_for_status()
        return r.json() or {}

    async def get_claim(self, claim_id: str | int) -> dict:
        """Fetch a post-purchase claim/mediation detail.

        Returns `{id, type, stage, status, resolution: {benefited, reason, ...},
        players: [{role, type}], ...}`. `stage` ∈ claim/dispute/recontact/none;
        `status` ∈ opened/closed; `resolution.benefited` names the winning side
        (complainant/respondent). Claim ids come off the order's `mediations`
        array (`order.mediations[].id`) — ML only lists them there when a
        post-sale claim/mediation actually opened. Raises on non-2xx (caller
        soft-fails)."""
        r = await self._request("GET", f"/post-purchase/v1/claims/{claim_id}")
        r.raise_for_status()
        return r.json() or {}

    async def get_claim_returns(self, claim_id: str | int) -> dict | list:
        """Fetch the return(s) tied to a claim. The return carries its own
        shipment status (`shipments[].status` ∈ ready_to_ship/shipped/delivered/
        cancelled) which is what the planilha calls `return_status`. Uses the
        v2 endpoint (v1 responds 400 for this resource); shape is
        `{id, shipments: [{shipment_id, status, ...}]}`. The caller normalizes.
        Raises on non-2xx (soft-failed by caller when a claim has no return)."""
        r = await self._request("GET", f"/post-purchase/v2/claims/{claim_id}/returns")
        r.raise_for_status()
        return r.json() or {}

    async def open_claim_dispute(self, claim_id: str | int) -> dict:
        """Escala a reclamação pra mediação do Mercado Livre (o "chamado"): o ML
        passa a atuar como mediador e o canal de mensagem com o mediador é
        liberado. Só funciona sobre uma reclamação já ABERTA pelo comprador (o
        vendedor NÃO abre reclamação/mediação do zero por API — a doc do ML é
        explícita). Ação irreversível. Levanta com o corpo do erro do ML em 4xx
        (pra ser mostrado ao operador)."""
        r = await self._request(
            "POST", f"/post-purchase/v1/claims/{claim_id}/actions/open-dispute"
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"ml_open_dispute status={r.status_code} body={r.text[:500]}"
            )
        return r.json() or {}

    async def send_claim_message(
        self,
        claim_id: str | int,
        message: str,
        *,
        receiver_role: str = "mediator",
        attachments: list[str] | None = None,
    ) -> dict:
        """Manda uma mensagem numa reclamação existente. `receiver_role`:
        `mediator` (o Mercado Livre, disponível só após open-dispute),
        `complainant` (o comprador) ou `respondent`. `attachments` são os
        `filename` retornados pelo upload em /claims/{id}/attachments. Levanta
        com o corpo do erro do ML em 4xx."""
        r = await self._request(
            "POST",
            f"/post-purchase/v1/claims/{claim_id}/actions/send-message",
            json={
                "receiver_role": receiver_role,
                "message": message,
                "attachments": attachments or [],
            },
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"ml_send_claim_message status={r.status_code} body={r.text[:500]}"
            )
        return r.json() or {}

    async def get_billing_order_details(self, order_id: str) -> dict:
        r = await self._request(
            "GET",
            "/billing/integration/group/ML/order/details",
            params={"order_ids": order_id},
        )
        r.raise_for_status()
        return r.json() or {}

    async def get_order_discounts(self, order_id: str) -> dict:
        """Per-discount funding breakdown for an order.

        Returns `{details: [{type, items: [{amounts: {total, seller}}],
        supplier: {funding_mode, campaign_id, ...}}]}`. `amounts.seller` is the
        portion the SELLER funds; ML-funded promo coupons report `seller: 0`.
        This is the only place ML exposes who paid each discount — the order's
        `payment.coupon_amount` lumps both together. Only meaningful when the
        order carries the `order_has_discount` tag."""
        r = await self._request("GET", f"/orders/{order_id}/discounts")
        r.raise_for_status()
        return r.json() or {}

    async def get_shipment_costs(self, shipping_id: str) -> dict:
        r = await self._request("GET", f"/shipments/{shipping_id}/costs")
        r.raise_for_status()
        return r.json() or {}

    async def get_shipment_items(self, shipping_id: str) -> dict | list:
        r = await self._request("GET", f"/shipments/{shipping_id}/items")
        r.raise_for_status()
        return r.json() or {}

    async def get_free_shipping_options(self, seller_id: str | int, item_id: str) -> dict:
        r = await self._request(
            "GET",
            f"/users/{seller_id}/shipping_options/free",
            params={"item_id": item_id, "verbose": "true"},
        )
        r.raise_for_status()
        return r.json() or {}

    async def search_orders(
        self,
        *,
        seller_id: str | int,
        date_from: str,
        date_to: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        r = await self._request(
            "GET",
            "/orders/search",
            params={
                "seller": seller_id,
                "order.date_created.from": date_from,
                "order.date_created.to": date_to,
                "limit": limit,
                "offset": offset,
            },
        )
        r.raise_for_status()
        return r.json() or {}

    async def get_listing_price(self, link: "ProductLink") -> float | None:
        """Read the current price from /items/{id}. ML quotes the item-level
        price even for multi-variation listings, so variation_id is ignored
        here — matches how update_price works."""
        try:
            item = await self.get_item(link.external_id)
        except Exception:  # noqa: BLE001
            return None
        price = item.get("price")
        try:
            return float(price) if price is not None else None
        except (TypeError, ValueError):
            return None

    async def get_listing_snapshot(self, link: "ProductLink") -> dict | None:
        """Current seller_sku + title for the item/variation this link points
        to. Returns ``{"sku", "title"}`` or None when it can't be read.

        For a variation link we read the SELLER_SKU off the matching variation
        (by stored variation_id); if that variation is gone we return None
        rather than guess. Used by the on-demand reconcile."""
        try:
            item = await self.get_item(link.external_id)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(item, dict) or not item:
            return None
        title = item.get("title")
        variations = item.get("variations") or []
        if link.variation_id:
            for v in variations:
                if str(v.get("id")) == str(link.variation_id):
                    return {"sku": _ml_sku_of(v), "title": title}
            return None
        return {"sku": _ml_sku_of(item), "title": title}

    async def update_stock(
        self,
        link: ProductLink,
        qty: int,
        *,
        bling_store_id: int | None = None,  # ignored on ML side
        force: bool = False,
    ) -> SyncResult:
        """ABC entrypoint. Resolves variation, applies B1 guard, dispatches to
        the correct ML endpoint, and classifies the outcome.

        B1: never write `available_quantity=0` when caller has positive stock —
        unless `force=True` (manual/individual sync where the user explicitly
        wants the marketplace to reflect a Bling zero).
        B3: re-resolve variation_id by `seller_sku` when the stored id is gone.
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

        item_id = link.external_id
        try:
            item = await self.get_item(item_id)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else None
            if code == 404:
                return SyncResult(
                    status=SyncStatus.FATAL,
                    qty_before=qty_before,
                    error_code="ml_item_not_found",
                    error_detail=f"item_id={item_id}",
                )
            return _map_http_error(e, qty_before, "ml_get_item_failed")

        listing_status = (item.get("status") or "").lower()
        # Manual sync (force=True) overrides the closed/paused short-circuit:
        # the user explicitly asked to push, so let ML reject if it must,
        # but don't preemptively skip on this side.
        if not force and listing_status in {"closed", "paused"}:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                qty_before=qty_before,
                error_code=f"ml_listing_{listing_status}",
            )

        variations = item.get("variations") or []
        seller_sku = (link.external_sku or "").strip() or None

        if variations:
            target_var, repointed = _resolve_variation(
                variations,
                stored_var_id=link.variation_id,
                seller_sku=seller_sku,
            )
            if target_var is None:
                return SyncResult(
                    status=SyncStatus.REQUIRES_REVIEW,
                    qty_before=qty_before,
                    error_code="ml_variation_not_found",
                    error_detail=(
                        f"variation_id={link.variation_id!r} gone and no "
                        f"variation matches seller_sku={seller_sku!r}"
                    ),
                    payload={"item_id": item_id, "variations_seen": len(variations)},
                )

            new_var_id = str(target_var["id"])
            payload_extra: dict[str, Any] = {}
            if repointed:
                link.variation_id = new_var_id
                payload_extra["variation_repointed_from"] = link.variation_id

            try:
                r = await self._request(
                    "PUT",
                    f"/items/{item_id}",
                    json={
                        "variations": [
                            {"id": int(new_var_id), "available_quantity": qty}
                        ]
                    },
                )
            except httpx.HTTPError as e:
                return _map_http_error(e, qty_before, "ml_put_variation_failed")
            if r.status_code >= 400:
                return _map_status_error(r, qty_before, "ml_put_variation_status")
            return SyncResult(
                status=SyncStatus.OK,
                qty_before=qty_before,
                qty_after=qty,
                payload={"item_id": item_id, "variation_id": new_var_id, **payload_extra},
            )

        # No variations on the listing -- single-item update.
        try:
            r = await self._request(
                "PUT",
                f"/items/{item_id}",
                json={"available_quantity": qty},
            )
        except httpx.HTTPError as e:
            return _map_http_error(e, qty_before, "ml_put_item_failed")
        if r.status_code >= 400:
            return _map_status_error(r, qty_before, "ml_put_item_status")
        return SyncResult(
            status=SyncStatus.OK,
            qty_before=qty_before,
            qty_after=qty,
            payload={"item_id": item_id},
        )

    async def update_price(
        self,
        item_id: str,
        price: float,
        *,
        variation_id: str | None = None,
    ) -> SyncResult:
        """Push price to a single ML listing — SSH semantics.

        SSH rules we mirror here:
          1. Round to integer reais (ML rejects decimals on most categories).
          2. GET /items/{id} first so we know if the listing has variations
             AND the current item.status (skip cleanly for closed/forbidden).
          3. For items WITH variations: PUT the same price on EVERY variation
             in one request. ML's docs are explicit: "you should make a PUT
             sending the same price in all the IDs for the variations".
             Sending only one variation is silently ignored or rejected.
          4. For items WITHOUT variations: PUT `{price: N}` directly.
          5. If ML returns `item.price.not_modifiable` (paused listing):
             activate → wait 2s → retry → re-pause regardless of retry result.
        """
        rounded_price = int(round(price))
        if rounded_price <= 0:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                error_code="invalid_price",
                error_detail=f"price={price}",
            )

        # 1. Fetch item info so we have variations + status.
        try:
            item_r = await self._request("GET", f"/items/{item_id}")
        except httpx.HTTPError as e:
            return SyncResult(
                status=SyncStatus.RETRYABLE,
                error_code="ml_get_item_failed",
                error_detail=str(e)[:500],
            )
        if item_r.status_code != 200:
            return SyncResult(
                status=SyncStatus.RETRYABLE,
                error_code=f"ml_get_item_{item_r.status_code}",
                error_detail=(item_r.text or "")[:500],
            )
        try:
            item_info = item_r.json() or {}
        except Exception:  # noqa: BLE001
            item_info = {}

        item_status = (item_info.get("status") or "").lower()
        sub_status = item_info.get("sub_status") or []
        variations = item_info.get("variations") or []

        # 2. Bail cleanly on terminal states — don't fight ML's moderation/closure.
        if item_status == "closed":
            detail = ",".join(sub_status) if sub_status else "closed"
            return SyncResult(
                status=SyncStatus.SKIPPED,
                error_code="ml_item_closed",
                error_detail=f"Anúncio {item_id} encerrado ({detail})",
            )
        if item_status == "under_review" and "forbidden" in sub_status:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                error_code="ml_item_forbidden",
                error_detail=f"Anúncio {item_id} removido por moderação",
            )

        # 3. Build the PUT body. SSH parity: when variations exist, send ALL.
        if variations:
            body: dict[str, Any] = {
                "variations": [
                    {"id": v["id"], "price": rounded_price}
                    for v in variations
                ]
            }
        else:
            body = {"price": rounded_price}

        async def _put_price() -> tuple[int, dict]:
            try:
                r = await self._request("PUT", f"/items/{item_id}", json=body)
            except httpx.HTTPError as e:
                return -1, {"_http_error": str(e)}
            try:
                payload = r.json() if r.content else {}
            except Exception:  # noqa: BLE001
                payload = {}
            return r.status_code, payload

        status_code, payload = await _put_price()

        if status_code == -1:
            return SyncResult(
                status=SyncStatus.RETRYABLE,
                error_code="ml_put_price_failed",
                error_detail=str(payload.get("_http_error", "unknown"))[:500],
            )
        if status_code < 400:
            return SyncResult(
                status=SyncStatus.OK,
                payload={
                    "item_id": item_id,
                    "variation_id": variation_id,
                    "price": rounded_price,
                    "variations_count": len(variations),
                },
            )

        # 4. not_modifiable → activate → wait → push → re-pause.
        message = (payload.get("message") or "").lower() if isinstance(payload, dict) else ""
        cause_list = payload.get("cause") or [] if isinstance(payload, dict) else []
        cause_codes = " ".join(str(c.get("code", "")) for c in cause_list).lower()

        if "not_modifiable" in message or "not_modifiable" in cause_codes:
            try:
                ar = await self._request(
                    "PUT", f"/items/{item_id}", json={"status": "active"}
                )
                if ar.status_code >= 400:
                    return SyncResult(
                        status=SyncStatus.FATAL,
                        error_code="ml_unpause_failed",
                        error_detail=(ar.text or "")[:500],
                    )
                await asyncio.sleep(2)
                status_code2, payload2 = await _put_price()
                # Re-pause regardless of retry outcome.
                try:
                    await self._request(
                        "PUT", f"/items/{item_id}", json={"status": "paused"}
                    )
                except Exception:  # noqa: BLE001
                    pass
                if status_code2 == -1:
                    return SyncResult(
                        status=SyncStatus.RETRYABLE,
                        error_code="ml_put_price_failed_after_unpause",
                        error_detail=str(payload2.get("_http_error", "unknown"))[:500],
                    )
                if status_code2 < 400:
                    return SyncResult(
                        status=SyncStatus.OK,
                        payload={
                            "item_id": item_id,
                            "variation_id": variation_id,
                            "price": rounded_price,
                            "via": "unpause_repause",
                        },
                    )
                # Fall through to the generic error mapping below using the
                # second-attempt status/payload.
                status_code = status_code2
                payload = payload2
            except Exception as e:  # noqa: BLE001
                return SyncResult(
                    status=SyncStatus.FATAL,
                    error_code="ml_unpause_retry_failed",
                    error_detail=str(e)[:500],
                )

        # 5. Generic error mapping — inline, no _R shim (the previous version
        # built a fake response object that failed at `r.text` later on).
        if status_code in {429, 502, 503, 504}:
            sync_status = SyncStatus.RETRYABLE
        else:
            sync_status = SyncStatus.FATAL

        if isinstance(payload, dict):
            cause = payload.get("cause") or []
            if cause:
                error_detail = "; ".join(
                    f"{c.get('code', '?')}: {c.get('message', '')}" for c in cause
                )[:500]
            else:
                error_detail = (payload.get("message") or json.dumps(payload))[:500]
        else:
            error_detail = str(payload)[:500]

        return SyncResult(
            status=sync_status,
            error_code=f"ml_put_price_status_{status_code}",
            error_detail=error_detail,
        )

    async def list_listings(
        self,
        *,
        page_size: int = 50,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict]:
        """Yields one normalized listing dict per ML item.

        Walks /users/{seller}/items/search to enumerate ids, then
        /items?ids=...&attributes=... in batches of 20 (ML's multi-get cap)
        to fetch listing details.
        """
        seller_id = self.creds.get("user_id")
        if not seller_id:
            r = await self._request("GET", "/users/me")
            r.raise_for_status()
            seller_id = (r.json() or {}).get("id")
            if seller_id and self.creds.get("user_id") != seller_id:
                self.creds["user_id"] = seller_id
                if self._on_refresh:
                    await self._on_refresh(self.creds)
        if not seller_id:
            return

        offset = 0
        page_idx = 0
        while True:
            if max_pages is not None and page_idx >= max_pages:
                break
            r = await self._request(
                "GET",
                f"/users/{seller_id}/items/search",
                params={"limit": page_size, "offset": offset},
            )
            if r.status_code != 200:
                raise RuntimeError(
                    f"ml_search_failed status={r.status_code} body={r.text[:200]}"
                )
            data = r.json() or {}
            ids = data.get("results") or []
            if not ids:
                break
            n_chunks = (len(ids) + 19) // 20
            for chunk_idx, chunk_start in enumerate(range(0, len(ids), 20)):
                chunk = ids[chunk_start : chunk_start + 20]
                rr = await self._request(
                    "GET",
                    "/items",
                    params={"ids": ",".join(chunk), "include_attributes": "all"},
                )
                if rr.status_code != 200:
                    logger.warning(
                        "ml_multiget_failed", status=rr.status_code, body=rr.text[:200]
                    )
                    continue
                for entry in rr.json() or []:
                    if entry.get("code") != 200:
                        continue
                    body = entry.get("body") or {}
                    for normalized in _iter_ml_variants(body):
                        yield normalized
                if chunk_idx < n_chunks - 1:
                    await asyncio.sleep(1.0)
            paging = data.get("paging") or {}
            total = int(paging.get("total") or 0)
            offset += page_size
            page_idx += 1
            if offset >= total:
                break
            await asyncio.sleep(0.3)


# ---------------------------------------------------------------- helpers
#
# Keep ML's raw listing_type_id ("gold_special" / "gold_pro") in product_links
# and listings so the push resolver (sku_match.ml_listing_type_for_account)
# can filter against the SAME value the API returns. The translation to the
# user-facing "ml classico" / "ml premium" lives in the pricing UI / sku_match
# layer — *not* at the ingestion edge. Earlier the mapping happened here, so
# the auto-link path wrote display strings into product_links.listing_type
# and push filtered by API values → 0 matches.


def _map_ml_listing_type(listing_type_id: str | None) -> str | None:
    if not listing_type_id:
        return None
    val = listing_type_id.strip().lower()
    # gold_premium is the legacy alias for gold_pro — collapse it.
    if val == "gold_premium":
        return "gold_pro"
    return val


def _normalize_ml_item(body: dict) -> dict:
    sku = None
    for attr in body.get("attributes") or []:
        if (attr.get("id") or "").upper() == "SELLER_SKU":
            sku = (attr.get("value_name") or attr.get("value") or "").strip() or None
            break
    if not sku:
        sku = (body.get("seller_custom_field") or "").strip() or None
    raw_price = body.get("price")
    price_cents: int | None = None
    if raw_price is not None:
        try:
            price_cents = int(round(float(raw_price) * 100))
        except (TypeError, ValueError):
            price_cents = None
    status = (body.get("status") or "").lower() or "active"
    return {
        "external_id": str(body.get("id") or ""),
        "variation_id": None,
        "sku": sku,
        "title": body.get("title") or "",
        "description": None,
        "price": price_cents,
        "stock": body.get("available_quantity"),
        "status": status if status in {
            "active", "paused", "closed", "under_review", "inactive"
        } else "inactive",
        "category": body.get("category_id"),
        "thumbnail_url": body.get("thumbnail") or body.get("secure_thumbnail"),
        "listing_type": _map_ml_listing_type(body.get("listing_type_id")),
        "raw": body,
    }


def _iter_ml_variants(body: dict):
    """Yield one normalized listing dict per ML variation, or one for the item
    if it has no variations. Mirrors SSH's getProducts fan-out logic — same
    SKU resolution priority (SELLER_SKU attr value_name → value →
    seller_custom_field → sku field) and one product_link per variation_id.
    """
    variations = body.get("variations") or []
    if not variations:
        yield _normalize_ml_item(body)
        return

    base_title = body.get("title") or ""
    status = (body.get("status") or "").lower() or "active"
    norm_status = status if status in {
        "active", "paused", "closed", "under_review", "inactive"
    } else "inactive"
    listing_type = _map_ml_listing_type(body.get("listing_type_id"))
    body_price = body.get("price")

    for variation in variations:
        sku = None
        for attr in variation.get("attributes") or []:
            if (attr.get("id") or "").upper() == "SELLER_SKU":
                sku = (attr.get("value_name") or attr.get("value") or "").strip() or None
                break
        if not sku:
            sku = (variation.get("seller_custom_field") or "").strip() or None
        if not sku:
            sku = (variation.get("sku") or "").strip() or None
        if not sku:
            continue

        raw_price = variation.get("price")
        if raw_price is None:
            raw_price = body_price
        price_cents: int | None = None
        if raw_price is not None:
            try:
                price_cents = int(round(float(raw_price) * 100))
            except (TypeError, ValueError):
                price_cents = None

        combos = variation.get("attribute_combinations") or []
        combo_label = " / ".join(
            (a.get("value_name") or "").strip()
            for a in combos
            if (a.get("value_name") or "").strip()
        )
        title = f"{base_title} - {combo_label}" if combo_label else base_title

        yield {
            "external_id": str(body.get("id") or ""),
            "variation_id": str(variation.get("id") or "") or None,
            "sku": sku,
            "title": title,
            "description": None,
            "price": price_cents,
            "stock": variation.get("available_quantity") or 0,
            "status": norm_status,
            "category": body.get("category_id"),
            "thumbnail_url": body.get("thumbnail") or body.get("secure_thumbnail"),
            "listing_type": listing_type,
            "raw": body,
        }


def _ml_sku_of(obj: dict) -> str | None:
    """Seller SKU of an ML item OR variation: SELLER_SKU attribute
    (value_name → value) → seller_custom_field. Same priority the auto-link
    ingestion uses, so reconcile compares like with like."""
    for attr in obj.get("attributes") or []:
        if (attr.get("id") or "").upper() == "SELLER_SKU":
            v = (attr.get("value_name") or attr.get("value") or "").strip()
            if v:
                return v
    return (obj.get("seller_custom_field") or "").strip() or None


def _resolve_variation(
    variations: list[dict],
    *,
    stored_var_id: str | None,
    seller_sku: str | None,
) -> tuple[dict | None, bool]:
    """Return (variation_dict, repointed?). `repointed=True` means the stored
    id no longer matches but we found a fresh one via `seller_sku`."""
    if stored_var_id:
        for v in variations:
            if str(v.get("id")) == str(stored_var_id):
                return v, False
    if not seller_sku:
        return None, False
    sku_norm = seller_sku.strip().lower()
    for v in variations:
        for attr in v.get("attributes") or []:
            if (attr.get("id") or "").upper() == "SELLER_SKU":
                val = (attr.get("value_name") or "").strip().lower()
                if val and val == sku_norm:
                    return v, True
        scf = (v.get("seller_custom_field") or "").strip().lower()
        if scf and scf == sku_norm:
            return v, True
    return None, False


def _map_http_error(e: httpx.HTTPError, qty_before: int | None, code: str) -> SyncResult:
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


def _map_status_error(r: httpx.Response, qty_before: int | None, code: str) -> SyncResult:
    if r.status_code in {429, 502, 503, 504}:
        status = SyncStatus.RETRYABLE
    elif r.status_code in {401, 403}:
        status = SyncStatus.FATAL
    elif r.status_code in {400, 422}:
        status = SyncStatus.FATAL
    else:
        status = SyncStatus.RETRYABLE
    # Some callers pass non-httpx objects (mocks, wrapped errors). Read `.text`
    # defensively so a misshaped response can never raise AttributeError out
    # of the error-classification layer itself.
    try:
        body = getattr(r, "text", None)
        if not isinstance(body, str):
            content = getattr(r, "content", None)
            body = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(r)
    except Exception:  # noqa: BLE001
        body = str(r)
    return SyncResult(
        status=status,
        qty_before=qty_before,
        error_code=f"{code}_{r.status_code}",
        error_detail=body[:500],
    )


def _normalize_token(payload: dict, prev: dict | None = None) -> dict:
    expires_in = int(payload.get("expires_in") or 21600)
    out: dict = dict(prev or {})
    out["access_token"] = payload["access_token"]
    if "refresh_token" in payload:
        out["refresh_token"] = payload["refresh_token"]
    if "user_id" in payload:
        out["user_id"] = payload["user_id"]
    out["scope"] = payload.get("scope", out.get("scope", ""))
    out["expires_at"] = int(time.time()) + expires_in
    out["_obtained_at"] = datetime.now(UTC).isoformat()
    return out
