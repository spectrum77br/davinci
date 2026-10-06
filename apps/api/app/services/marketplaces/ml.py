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
- Listings *with* variations   : PUT /items/{item_id}, retaining every variation ID
  and setting available_quantity only on the target variation.

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
from app.services.marketplaces import flex_api
from app.services.marketplaces.base import SyncResult, SyncStatus, TestResult
from app.services.pricing import anuncios as _anuncios
from app.services.pricing.anuncios import CANAL_CATALOGO, CANAL_KIT, motivo_por_status
from app.services.vinculo_saude import norm_sku

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

    async def get_item(self, item_id: str, *, include_attributes: bool = False) -> dict:
        # include_attributes=all: sem ele o ML NÃO manda os `attributes` das
        # variações (onde mora o SELLER_SKU de cada variação).
        params = {"include_attributes": "all"} if include_attributes else None
        r = await self._request("GET", f"/items/{item_id}", params=params)
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

    async def get_shipment_sla(self, shipment_id: str) -> dict:
        """`/shipments/{id}/sla` → `{expected_date, last_updated, ...}`.

        `expected_date` é o "despachar até" que o Seller Central mostra —
        com hora real de coleta no cross-docking (ex.: 13:00) e 23:59:59
        quando é agência (conta o dia). Usado pelo sweep de envio pra
        carimbar o horário de corte do pedido na aba Pedidos."""
        r = await self._request("GET", f"/shipments/{shipment_id}/sla")
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

    async def get_claim_messages(self, claim_id: str | int) -> list[dict]:
        """Mensagens da reclamação (GET /post-purchase/v1/claims/{id}/messages):
        `[{sender_role, receiver_role, message, date_created, attachments…}]`.
        [] quando a API não devolve lista (o acompanhamento é best-effort)."""
        r = await self._request("GET", f"/post-purchase/v1/claims/{claim_id}/messages")
        if r.status_code >= 400:
            return []
        try:
            body = r.json()
        except ValueError:
            return []
        if isinstance(body, dict):
            body = body.get("messages") or body.get("results") or []
        return [m for m in body if isinstance(m, dict)] if isinstance(body, list) else []

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

    async def upload_return_attachment(
        self,
        claim_id: str | int,
        filename: str,
        content: bytes,
        content_type: str = "image/jpeg",
    ) -> str:
        """Sobe uma evidência (foto) pra devolução de um claim e devolve o
        `file_name` que entra em `return_review_fail`. Multipart `file`
        (POST /post-purchase/v1/claims/{id}/returns/attachments) — a API aceita
        JPG/PNG/PDF/TXT até 5 MB; vídeo não. Levanta com o corpo do erro em 4xx."""
        if self._expired():
            await self.refresh()
        url = f"{ML_API_BASE}/post-purchase/v1/claims/{claim_id}/returns/attachments"
        for attempt in range(2):
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=120.0) as c:
                r = await c.post(
                    url, headers=headers, files={"file": (filename, content, content_type)}
                )
            if r.status_code == 401 and attempt == 0:
                await self.refresh()
                continue
            break
        if r.status_code >= 400:
            raise RuntimeError(
                f"ml_return_attachment status={r.status_code} body={r.text[:500]}"
            )
        data = r.json() or {}
        name = str(data.get("file_name") or "").strip()
        if not name:
            raise RuntimeError(f"ml_return_attachment sem file_name body={r.text[:300]}")
        return name

    async def return_review_fail(
        self,
        return_id: str | int,
        reason: str,
        message: str,
        *,
        attachments: list[str] | None = None,
    ) -> dict:
        """Revisão da devolução COM PROBLEMA (o "chamado" de devolução do
        vendedor): POST /post-purchase/v1/returns/{return_id}/return-review com
        `[{reason, message, attachments}]`. `reason` ∈ SRF2 danificado | SRF3
        incompleta | SRF4 produto diferente | SRF5 sem produto | SRF6 outro |
        SRF7 não chegou (esse é do pacote, sem anexo). SRF2/SRF4 exigem
        `attachments` (nomes do `upload_return_attachment`). Só funciona quando
        o player seller do claim tem `return_review_fail` em available_actions.
        Levanta com o corpo do erro em 4xx."""
        item: dict = {"reason": reason, "message": message}
        if attachments:
            item["attachments"] = list(attachments)
        r = await self._request(
            "POST", f"/post-purchase/v1/returns/{return_id}/return-review", json=[item]
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"ml_return_review status={r.status_code} body={r.text[:500]}"
            )
        try:
            return r.json() or {}
        except ValueError:
            return {}

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

    async def search_orders_updated(
        self,
        *,
        seller_id: str | int,
        date_from: str,
        date_to: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Como `search_orders`, mas filtrando por `order.date_last_updated`:
        pega quem MUDOU na janela (entrega tardia, cancelamento), não quem foi
        criado nela. Cada resultado traz `status` e `tags` (incl. "delivered"/
        "not_delivered") — base do sweep de pós-venda da Logística."""
        r = await self._request(
            "GET",
            "/orders/search",
            params={
                "seller": seller_id,
                "order.date_last_updated.from": date_from,
                "order.date_last_updated.to": date_to,
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
            item = await self.get_item(link.external_id, include_attributes=True)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(item, dict) or not item:
            return None
        title = item.get("title")
        variations = item.get("variations") or []
        if link.variation_id:
            for v in variations:
                if str(v.get("id")) == str(link.variation_id):
                    return {"sku": _ml_sku_oficial(v), "title": title}
            return None
        return {"sku": _ml_sku_oficial(item), "title": title}

    async def update_stock(
        self,
        link: ProductLink,
        qty: int,
        *,
        bling_store_id: int | None = None,  # ignored on ML side
        force: bool = False,
        sku_esperado: str | None = None,
    ) -> SyncResult:
        """ABC entrypoint. Resolves variation, applies B1 guard, dispatches to
        the correct ML endpoint, and classifies the outcome.

        `sku_esperado` (SKU do produto do vínculo): o GET do anúncio já vem de
        graça antes do PUT, então conferimos o SELLER_SKU atual. Se o anúncio
        agora tem OUTRO SKU (dg053.ci → dg053.sp trocado lá dentro), NÃO
        envia o estoque do produto velho: devolve `sku_trocado` com o SKU
        atual e o orquestrador move o vínculo e reenvia o estoque certo.
        SKU vazio no anúncio não conta como troca.

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
            item = await self.get_item(item_id, include_attributes=sku_esperado is not None)
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
        # States ML locks against stock writes: a closed/ended listing, a
        # seller-inactivated one, or one under review. Pushing here ALWAYS 400s
        # (`field_not_updatable` / `variations.not_updatable`) and only floods
        # the sync log — ML won't revive them via a stock PUT (they'd need a
        # relist). Skip cleanly even under force. `paused` is different: an
        # out-of-stock pause reactivates when we push positive stock, so a
        # forced sync IS allowed to push through it.
        if listing_status in {"closed", "inactive", "under_review"}:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                qty_before=qty_before,
                error_code=f"ml_listing_{listing_status}",
            )
        if not force and listing_status == "paused":
            # Pausado POR FALTA DE ESTOQUE volta sozinho quando recebe estoque
            # positivo — é justamente o anúncio que zerou (ex. vínculo preso no
            # lote velho) e precisa voltar. Pausado pelo vendedor continua
            # intocado.
            sub = {str(x).lower() for x in (item.get("sub_status") or [])}
            if not (qty > 0 and "out_of_stock" in sub):
                return SyncResult(
                    status=SyncStatus.SKIPPED,
                    qty_before=qty_before,
                    error_code="ml_listing_paused",
                )

        variations = item.get("variations")
        if variations is None:
            variations = []
        variation_ids = _stock_variation_ids(variations)
        if variation_ids is None:
            return SyncResult(
                status=SyncStatus.REQUIRES_REVIEW,
                qty_before=qty_before,
                error_code="ml_variations_invalid",
                error_detail="Cannot preserve every variation: missing, invalid or duplicate ID",
                payload={"item_id": item_id},
            )
        if link.variation_id and not variations:
            # A removed variation must not become the remaining simple item.
            # Manual sync includes dead links; falling through could repoint
            # their SKU, write to a sibling and revive the obsolete link.
            return SyncResult(
                status=SyncStatus.REQUIRES_REVIEW,
                qty_before=qty_before,
                error_code="ml_variation_not_found",
                error_detail=(
                    f"variation_id={link.variation_id!r} no longer exists: "
                    "the listing has no variations"
                ),
                payload={
                    "item_id": item_id, "variation_id": link.variation_id,
                    "variations_seen": 0,
                },
            )
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
            sku_atual = _ml_sku_oficial(target_var)
            trocado = _sku_trocado(sku_atual, sku_esperado, qty_before, item_id, new_var_id)
            if trocado is not None:
                return trocado
            if sku_atual:
                payload_extra["sku_atual"] = sku_atual
            if repointed:
                payload_extra["variation_repointed_from"] = link.variation_id
                link.variation_id = new_var_id

            try:
                r = await self._request(
                    "PUT",
                    f"/items/{item_id}",
                    json={
                        "variations": [
                            {"id": variation_id, "available_quantity": qty}
                            if variation_id == int(new_var_id)
                            else {"id": variation_id}
                            for variation_id in variation_ids
                        ]
                    },
                )
            except httpx.HTTPError as e:
                return _map_http_error(e, qty_before, "ml_put_variation_failed")
            if r.status_code >= 400:
                return _map_status_error(r, qty_before, "ml_put_variation_status")
            try:
                updated_item = r.json()
            except ValueError:
                updated_item = {}
            if isinstance(updated_item, dict) and "variations" in updated_item:
                returned = updated_item["variations"]
                returned_ids = _stock_variation_ids(returned)
                returned_qty = None
                if returned_ids is not None:
                    returned_qty = next(
                        (v.get("available_quantity")
                         for v, vid in zip(returned, returned_ids, strict=True)
                         if vid == int(new_var_id)),
                        None,
                    )
                if (
                    returned_ids is None
                    or set(returned_ids) != set(variation_ids)
                    or isinstance(returned_qty, bool)
                    or returned_qty != qty
                ):
                    return SyncResult(
                        status=SyncStatus.REQUIRES_REVIEW,
                        qty_before=qty_before,
                        error_code="ml_variations_changed",
                        error_detail="ML response did not preserve variation IDs and target stock",
                        payload={
                            "item_id": item_id, "variation_id": new_var_id,
                            "variations_expected": variation_ids,
                            "variations_returned": returned_ids,
                            "stock_returned": returned_qty,
                        },
                    )
            return SyncResult(
                status=SyncStatus.OK,
                qty_before=qty_before,
                qty_after=qty,
                payload={"item_id": item_id, "variation_id": new_var_id, **payload_extra},
            )

        # No variations on the listing -- single-item update.
        sku_atual = _ml_sku_oficial(item)
        trocado = _sku_trocado(sku_atual, sku_esperado, qty_before, item_id, None)
        if trocado is not None:
            return trocado
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
            payload={"item_id": item_id, **({"sku_atual": sku_atual} if sku_atual else {})},
        )

    async def update_price(
        self,
        item_id: str,
        price: float,
        *,
        variation_id: str | None = None,
        canal_esperado: str | None = None,
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
          5. If ML returns `item.price.not_modifiable` on a PAUSED listing:
             activate → wait 2s → retry → pause again, conferindo a resposta
             da pausa (06/10/2026: antes reativava qualquer status e pausava
             sem conferir; agora só mexe no que estava pausado).

        `canal_esperado` (Catálogo ML, 06/10/2026) confere no item VIVO, antes
        do PUT, se o anúncio é do canal da coluna: 'catalogo' só vai para
        anúncio com catalog_listing=true, não sincronizado (item_relations
        vazio), nem pausado/em revisão/encerrado; 'kit' nunca vai para anúncio
        de catálogo. Fora do canal → SKIPPED sem chamar o PUT. None = sem
        conferência (comportamento antigo).
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

        # 1b. O canal da coluna bate com o anúncio vivo? (Catálogo ML)
        fora_do_canal = _conferir_canal_ml(item_id, item_info, canal_esperado)
        if fora_do_canal is not None:
            return fora_do_canal

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

        # 3b. Automação de preço do ML ligada (desde 18/03/2026 o ML recusa
        # troca de preço nesses anúncios) — erro claro, sem reativar nada.
        if 400 <= status_code < 500 and _recusa_por_automacao(payload):
            return _erro_automacao(item_id)

        # 4. not_modifiable num anúncio PAUSADO → activate → wait → push →
        # pausa de novo (e confere). Anúncio que não estava pausado não é
        # reativado: a recusa dele cai no erro genérico abaixo.
        message = (payload.get("message") or "").lower() if isinstance(payload, dict) else ""
        cause_list = payload.get("cause") or [] if isinstance(payload, dict) else []
        cause_codes = " ".join(str(c.get("code", "")) for c in cause_list).lower()

        if item_status == "paused" and (
            "not_modifiable" in message or "not_modifiable" in cause_codes
        ):
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
                # Pausa de novo qualquer que seja o resultado — e confere.
                repausa_falhou = not await self._pausar_de_novo(item_id)
                if status_code2 == -1:
                    return SyncResult(
                        status=SyncStatus.RETRYABLE,
                        error_code="ml_put_price_failed_after_unpause",
                        error_detail=str(payload2.get("_http_error", "unknown"))[:500],
                        payload={"repausa_falhou": repausa_falhou},
                    )
                if status_code2 < 400:
                    return SyncResult(
                        status=SyncStatus.OK,
                        payload={
                            "item_id": item_id,
                            "variation_id": variation_id,
                            "price": rounded_price,
                            "via": "unpause_repause",
                            "repausa_falhou": repausa_falhou,
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
        if 400 <= status_code < 500 and _recusa_por_automacao(payload):
            return _erro_automacao(item_id)
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

    async def _pausar_de_novo(self, item_id: str) -> bool:
        """Volta o anúncio para pausado depois da troca de preço e CONFERE a
        resposta. False = a pausa não voltou (o anúncio ficou ATIVO) — vai
        para o log como erro e para o resultado do envio."""
        try:
            r = await self._request("PUT", f"/items/{item_id}", json={"status": "paused"})
        except Exception as e:  # noqa: BLE001
            logger.error("ml_repausa_falhou", item_id=item_id, erro=str(e)[:300])
            return False
        if r.status_code >= 400:
            logger.error(
                "ml_repausa_falhou",
                item_id=item_id,
                status=r.status_code,
                body=(r.text or "")[:300],
            )
            return False
        return True

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

        # Paginação por SCROLL (search_type=scan): a por `offset` o ML recusa
        # passar de 1.000 anúncios ("Invalid limit and offset values") — as
        # contas kfa, forpaper e marquezini nunca eram lidas até o fim, e o que
        # passava do 1.000º nunca era vinculado nem religado.
        # `listagem_completa` diz se a leitura foi até o fim sem buraco (fica
        # no relatório da varredura; sumir da lista NÃO marca morto — só o
        # status "closed" ou o 404 no envio).
        self.listagem_completa = False
        scroll_id: str | None = None
        page_idx = 0
        algum_buraco = False
        while True:
            if max_pages is not None and page_idx >= max_pages:
                algum_buraco = True
                break
            params: dict[str, Any] = {"search_type": "scan", "limit": 100}
            if scroll_id:
                params["scroll_id"] = scroll_id
            r = await self._request("GET", f"/users/{seller_id}/items/search", params=params)
            if r.status_code != 200:
                raise RuntimeError(
                    f"ml_search_failed status={r.status_code} body={r.text[:200]}"
                )
            data = r.json() or {}
            scroll_id = data.get("scroll_id") or scroll_id
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
                    algum_buraco = True
                    logger.warning(
                        "ml_multiget_failed", status=rr.status_code, body=rr.text[:200]
                    )
                    continue
                for entry in rr.json() or []:
                    if entry.get("code") != 200:
                        algum_buraco = True
                        continue
                    body = entry.get("body") or {}
                    for normalized in _iter_ml_variants(body):
                        yield normalized
                if chunk_idx < n_chunks - 1:
                    await asyncio.sleep(1.0)
            page_idx += 1
            await asyncio.sleep(0.3)
        self.listagem_completa = not algum_buraco

    # ── Atendimento: perguntas pré-venda e mensagens pós-venda (25/09/2026) ──
    #
    # Usados pela caixa de atendimento (services/atendimento/ml.py). Enquanto
    # o Duoke estiver ligado, NADA aqui pode marcar mensagem como lida: a
    # equipe se guia pelo "não lido" de lá.

    async def _request_uma_vez(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
    ) -> httpx.Response:
        """Como `_request`, mas SEM repetir em 429/5xx — para o que ESCREVE ao comprador.

        O `_request` repete em 502/503/504, o que é ótimo para leitura e
        péssimo para envio: um 502 do gateway pode chegar depois que o ML já
        gravou a mensagem, e a repetição mandaria a mesma resposta duas vezes
        (ou, na pergunta, devolveria "já respondida" para uma resposta que
        saiu). Aqui só o 401 repete — token recusado é pedido que o ML não
        processou. O 5xx volta para quem chamou tratar como AMBÍGUO.
        """
        if self._expired():
            await self.refresh()
        url = f"{ML_API_BASE}{path}"
        for attempt in range(2):
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.request(method, url, headers=headers, params=params, json=json)
            if r.status_code == 401 and attempt == 0:
                await self.refresh()
                continue
            return r
        return r

    async def perguntas_recebidas(
        self,
        *,
        status: str = "UNANSWERED",
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        """Perguntas dos anúncios da conta (GET /my/received_questions/search).

        `api_version=4` é o formato com `from: {id}` e `answer` embutido
        (`{text, status, date_created}`), medido em produção em 25/09.
        Mais novas primeiro. `status`: UNANSWERED | ANSWERED | CLOSED_UNANSWERED
        | UNDER_REVIEW | BANNED | DELETED. Devolve `{total, limit, questions}`;
        levanta em não-2xx (quem chama traduz o erro)."""
        r = await self._request(
            "GET",
            "/my/received_questions/search",
            params={
                "status": status,
                "api_version": 4,
                "sort_fields": "date_created",
                "sort_types": "DESC",
                "offset": offset,
                "limit": limit,
            },
        )
        r.raise_for_status()
        return r.json() or {}

    async def detalhe_pergunta(self, question_id: str | int) -> dict:
        """Uma pergunta pelo id (GET /questions/{id}, `api_version=4`).

        A busca por status só mostra quem ESTÁ naquele status: a pergunta que
        saiu de UNANSWERED sem aparecer em ANSWERED (apagada pelo comprador,
        fechada com o anúncio, banida, em revisão) só se descobre assim.
        Levanta em não-2xx (404 = apagada)."""
        r = await self._request(
            "GET", f"/questions/{question_id}", params={"api_version": 4}
        )
        r.raise_for_status()
        return r.json() or {}

    async def responder_pergunta(self, question_id: str | int, texto: str) -> httpx.Response:
        """Responde uma pergunta pré-venda (POST /answers). Até 2.000 caracteres.

        Devolve a resposta HTTP crua, sem levantar por status: quem chama
        decide o que é recusa (4xx) e o que é ambíguo (5xx). Uma tentativa
        só (`_request_uma_vez`) — resposta pública não se desfaz."""
        return await self._request_uma_vez(
            "POST",
            "/answers",
            json={"question_id": int(question_id), "text": texto},
        )

    async def mensagens_nao_lidas(self, tag: str = "post_sale") -> dict:
        """Conversas pós-venda com mensagem não lida pelo vendedor.

        GET /messages/unread?role=seller&tag=post_sale →
        `{user_id, total, results: [{resource: "/packs/<pack>/sellers/<seller>",
        count}]}`. Só CONTA; não marca nada como lido. Levanta em não-2xx
        (403 `PA_UNAUTHORIZED_RESULT_FROM_POLICIES` = conta sem permissão)."""
        r = await self._request(
            "GET", "/messages/unread", params={"role": "seller", "tag": tag}
        )
        r.raise_for_status()
        return r.json() or {}

    async def mensagens_do_pack(
        self,
        pack_id: str | int,
        seller_id: str | int,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> dict:
        """As mensagens pós-venda de um pack (GET /messages/packs/{pack}/sellers/{seller}).

        `mark_as_read=false` é FIXO e não é parâmetro de propósito: sem ele o
        ML marca a conversa como lida ao ler, e a equipe perde o "não lido"
        no Duoke — que é por onde ela sabe o que falta responder. Devolve
        `{paging, conversation_status, messages, seller_max_message_length,
        buyer_max_message_length}`; levanta em não-2xx.

        Pedido sem pack usa o próprio order_id no lugar do pack_id (regra do ML).
        """
        r = await self._request(
            "GET",
            f"/messages/packs/{pack_id}/sellers/{seller_id}",
            params={
                "tag": "post_sale",
                "mark_as_read": "false",
                "offset": offset,
                "limit": limit,
            },
        )
        r.raise_for_status()
        return r.json() or {}

    async def enviar_mensagem_pack(
        self,
        pack_id: str | int,
        seller_id: str | int,
        buyer_id: str | int,
        texto: str,
    ) -> httpx.Response:
        """Manda uma mensagem pós-venda no pack. Até 350 caracteres, só ISO-8859-1.

        POST /messages/packs/{pack}/sellers/{seller}?tag=post_sale. Devolve a
        resposta HTTP crua, sem levantar por status (4xx = recusa, com a
        conversa bloqueada dizendo o motivo; 5xx = ambíguo). Uma tentativa só
        (`_request_uma_vez`): mensagem não se desenvia.

        `user_id` vai como número, o mesmo tipo que o GET devolve em
        `from`/`to` (medido em produção)."""

        def _uid(v: str | int) -> str | int:
            return int(v) if str(v).isdigit() else str(v)

        return await self._request_uma_vez(
            "POST",
            f"/messages/packs/{pack_id}/sellers/{seller_id}",
            params={"tag": "post_sale"},
            json={
                "from": {"user_id": _uid(seller_id)},
                "to": {"user_id": _uid(buyer_id)},
                "text": texto,
            },
        )

    # ── Atendimento: pedido, envio e anúncio no painel da caixa (28/09/2026) ──
    #
    # O painel "Pedido" (services/atendimento/enriquecer.py) mostra o que o
    # Duoke mostra: status, itens com foto, valores, pagamento e envio. SÓ
    # LEITURA (GET). O comprador e o endereço vêm nas respostas do ML, mas o
    # enriquecimento não os guarda. Formato medido em produção em 28/09/2026.

    _CAMPOS_ITEM_ATENDIMENTO = (
        "id,title,price,original_price,thumbnail,secure_thumbnail,permalink,variations"
    )

    async def _get_com_cabecalhos(
        self, path: str, *, params: dict | None = None, headers: dict | None = None
    ) -> httpx.Response:
        """GET como o `_request` (renova no 401, repete em 429/5xx), com
        cabeçalhos a mais — o `/shipments` só devolve o formato novo com
        `x-format-new: true`."""
        if self._expired():
            await self.refresh()
        url = f"{ML_API_BASE}{path}"
        delay = 1.0
        for attempt in range(3):
            cabecalhos = {
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
                **(headers or {}),
            }
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.get(url, headers=cabecalhos, params=params)
            if r.status_code == 401 and attempt == 0:
                await self.refresh()
                continue
            if r.status_code in (429, 502, 503, 504):
                logger.warning("ml_retry", attempt=attempt + 1, status=r.status_code, path=path)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            return r
        return r

    async def pedido(self, order_id: str | int) -> dict:
        """UM pedido (GET /orders/{id}): itens, valores, pagamentos e o id do
        envio (`shipping.id`). Levanta em não-2xx (404 = não é pedido desta conta)."""
        return await self.get_order(str(order_id))

    async def envio(self, shipment_id: str | int) -> dict:
        """UM envio no formato novo (GET /shipments/{id}, `x-format-new: true`):
        `status`, `substatus`, `tracking_number`, `tracking_method`,
        `last_updated`. Levanta em não-2xx."""
        r = await self._get_com_cabecalhos(
            f"/shipments/{shipment_id}", headers={"x-format-new": "true"}
        )
        r.raise_for_status()
        return r.json() or {}

    async def itens(self, ids: list[str]) -> list[dict]:
        """Os anúncios pedidos (GET /items?ids=a,b&attributes=...), de 20 em 20:
        título, preço, preço original, miniatura (`secure_thumbnail`) e link.

        Devolve o `body` de cada anúncio que o ML achou (`code` 200); os outros
        ficam de fora. Levanta em não-2xx da chamada inteira."""
        unicos = [str(i).strip() for i in dict.fromkeys(ids) if str(i or "").strip()]
        saida: list[dict] = []
        for inicio in range(0, len(unicos), 20):
            lote = unicos[inicio : inicio + 20]
            r = await self._request(
                "GET",
                "/items",
                params={"ids": ",".join(lote), "attributes": self._CAMPOS_ITEM_ATENDIMENTO},
            )
            r.raise_for_status()
            corpo = r.json()
            for entrada in corpo if isinstance(corpo, list) else []:
                if not isinstance(entrada, dict) or entrada.get("code") != 200:
                    continue
                body = entrada.get("body")
                if isinstance(body, dict):
                    saida.append(body)
        return saida

    # ── Atendimento: o cartão "Cliente" da caixa (28/09/2026) ──
    #
    # Quem escreve já comprou? Quanto? Avaliou mal? O ML filtra pedido por
    # comprador (medido em 28/09: `buyer=` devolve só os dele), então o cartão
    # (services/atendimento/cliente.py) pergunta AO VIVO, com cache de 2 h.
    # SÓ LEITURA. A resposta traz nome e apelido do comprador: quem chama
    # guarda só número, data, valor, status e itens.

    async def pedidos_do_comprador(
        self, buyer_id: str | int, *, limit: int = 50, offset: int = 0
    ) -> dict:
        """Os pedidos da conta feitos por UM comprador, mais novos primeiro
        (GET /orders/search?seller=<uid>&buyer=<buyer_id>&sort=date_desc).

        Devolve `{results: [pedido...], paging: {total, offset, limit}}` como
        veio. O vendedor é o `creds["user_id"]` (gravado no primeiro /users/me);
        sem ele, `ValueError`. Levanta em não-2xx."""
        seller = str((self.creds or {}).get("user_id") or "").strip()
        if not seller:
            raise ValueError("pedidos_do_comprador: conta sem user_id")
        r = await self._request(
            "GET",
            "/orders/search",
            params={
                "seller": seller,
                "buyer": str(buyer_id),
                "sort": "date_desc",
                "limit": max(1, min(int(limit), 50)),
                "offset": max(0, int(offset)),
            },
        )
        r.raise_for_status()
        return r.json() or {}

    async def feedback_do_pedido(self, order_id: str | int) -> dict | None:
        """A avaliação da venda (GET /orders/{id}/feedback), ou None quando não
        houve — o ML responde 404 para venda sem avaliação (medido em 28/09).
        Devolve o corpo como veio (`{sale, purchase}`: cada lado com `rating`
        positive|neutral|negative, `message`, `reply`, `date_created`, `from`,
        `to`). Levanta nos outros não-2xx."""
        r = await self._request("GET", f"/orders/{order_id}/feedback")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        corpo = r.json()
        return corpo if isinstance(corpo, dict) else {}

    # ── Atendimento: opiniões do produto (avaliações, 02/10/2026) ──
    #
    # A avaliação da VENDA (`/orders/{id}/feedback`) está morta (0 de 60
    # pedidos de 7 a 30 dias atrás, 404 em todos): o que liga a avaliação ao
    # pedido hoje é a OPINIÃO do produto, que traz o `order_id`. SÓ LEITURA:
    # o ML não tem resposta do vendedor para a opinião pela API.

    async def opinioes_do_anuncio(
        self, item_id: str, *, limit: int = 50, offset: int = 0
    ) -> dict:
        """As opiniões do PRODUTO do anúncio (GET /reviews/item/{item_id}).

        Formato medido em produção em 02/10/2026 (contas aguiar, jlas2,
        marquezini, aguiar2; só GET): `{paging: {total, limit, offset,
        total_pageable}, reviews: [...], rating_average, stars,
        rating_levels: {one_star..five_star}, helpful_reviews,
        quali_attributes, cross_site_enabled, user_product_id}`. Cada opinião:
        `id`, `reviewable_object: {id (o anúncio), type: product}`,
        `date_created` (ISO com Z), `status` (published), `title`, `content`,
        `rate` (1–5), `media` [{id, status, type, url, thumbnail,
        preview_url, alt, duration_ms}], `order_id` (100%), `buying_date`,
        `variation_id`... — e NENHUM id do comprador nem resposta.

        Cuidados medidos: a lista é do PRODUTO (`user_product_id`), não do
        anúncio — anúncios irmãos (inclusive de outra conta nossa) devolvem
        as mesmas opiniões; a ordem NÃO é por data; `total_pageable` <
        `total` (as que não paginam não vêm). Levanta em não-2xx."""
        r = await self._request(
            "GET",
            f"/reviews/item/{item_id}",
            params={"limit": max(1, min(int(limit), 50)), "offset": max(0, int(offset))},
        )
        r.raise_for_status()
        corpo = r.json()
        return corpo if isinstance(corpo, dict) else {}

    # ── Atendimento: reclamações, mediações e devoluções (01/10/2026) ──
    #
    # A leitura das reclamações do pós-venda (services/atendimento/
    # reclamacoes.py). SÓ LEITURA (GET): nenhuma ação na reclamação sai daqui
    # — as ações (`open_claim_dispute`, `send_claim_message`...) moram acima
    # e o atendimento não as usa. Formato medido em produção em 01/10/2026
    # (conta aguiar): a busca é pelo dono do token, aceita `limit` até 100 e
    # EXIGE `status` (ou `resource`+`resource_id`); `sort` só com `status`;
    # `range` e `resource_id` sozinho dão 400.

    async def buscar_reclamacoes(
        self,
        *,
        status: str,
        offset: int = 0,
        limit: int = 50,
        sort: str | None = None,
    ) -> dict:
        """Reclamações da conta (GET /post-purchase/v1/claims/search).

        `status` = opened | closed (obrigatório); `sort` ex.
        `last_updated:desc` (as encerradas mais recentes primeiro). Devolve
        `{paging: {total, offset, limit}, data: [claim...]}` — cada claim já
        traz `type`, `stage`, `status`, `reason_id`, `resource`/`resource_id`
        (o pedido), `players[].available_actions` (com `due_date`) e
        `resolution`. Levanta em não-2xx."""
        params: dict[str, Any] = {
            "status": status,
            "offset": max(0, int(offset)),
            "limit": max(1, min(int(limit), 100)),
        }
        if sort:
            params["sort"] = sort
        r = await self._request("GET", "/post-purchase/v1/claims/search", params=params)
        r.raise_for_status()
        corpo = r.json()
        return corpo if isinstance(corpo, dict) else {}

    async def mensagens_da_reclamacao(self, claim_id: str | int) -> list[dict]:
        """As mensagens da reclamação (GET /post-purchase/v1/claims/{id}/messages).

        Diferente de `get_claim_messages` (que devolve [] em erro, para o
        acompanhamento dos chamados), esta LEVANTA em não-2xx: quem lê para
        gravar não pode confundir "a API falhou" com "não há mensagem" e dar
        a reclamação por lida. Cada mensagem: `sender_role`/`receiver_role`
        (complainant | respondent | mediator), `message`, `date_created`,
        `attachments`, `stage`, `status`, `message_moderation` e `hash` — o
        ML não manda id de mensagem (medido em 01/10/2026)."""
        r = await self._request("GET", f"/post-purchase/v1/claims/{claim_id}/messages")
        r.raise_for_status()
        corpo = r.json()
        if isinstance(corpo, dict):
            corpo = corpo.get("messages") or corpo.get("data") or []
        return [m for m in corpo if isinstance(m, dict)] if isinstance(corpo, list) else []

    async def motivo_de_reclamacao(self, reason_id: str) -> dict:
        """O motivo da reclamação (GET /post-purchase/v1/claims/reasons/{id}):
        `{id, flow, name, detail, parent_id, ...}` — texto do PRÓPRIO ML ("O
        produto chegou com defeito"), nunca do comprador. Levanta em não-2xx."""
        r = await self._request("GET", f"/post-purchase/v1/claims/reasons/{reason_id}")
        r.raise_for_status()
        corpo = r.json()
        return corpo if isinstance(corpo, dict) else {}

    async def reclamacao_afeta_reputacao(self, claim_id: str | int) -> dict | None:
        """A reclamação afeta a reputação? (GET /post-purchase/v1/claims/{id}/affects-reputation)
        → `{affects_reputation: 'affected' | 'not_affected' | ..., has_incentive,
        due_date}`; None em 404. Levanta nos outros não-2xx."""
        r = await self._request(
            "GET", f"/post-purchase/v1/claims/{claim_id}/affects-reputation"
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        corpo = r.json()
        return corpo if isinstance(corpo, dict) else None

    async def devolucao_da_reclamacao(self, claim_id: str | int) -> dict | None:
        """A devolução ligada à reclamação (GET /post-purchase/v2/claims/{id}/returns),
        ou None quando não há (404). Mesmo recurso do `get_claim_returns`,
        sem levantar no "não tem devolução". Traz `status`, `status_money`,
        `refund_at`, `shipments[].status` — e o endereço do comprador, que
        quem chama NÃO guarda. Levanta nos outros não-2xx."""
        r = await self._request("GET", f"/post-purchase/v2/claims/{claim_id}/returns")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        corpo = r.json()
        return corpo if isinstance(corpo, dict) else None

    # ── Flex por anúncio (projeto Flex, etapa 3 — 02/10/2026) ──
    #
    # GET/POST/DELETE /flex/sites/MLB/items/{id}/v2 (página oficial do Flex,
    # 22/09/2026). Os três NUNCA levantam: devolvem um `ResultadoFlex` já
    # classificado (flex_api.classificar_ml) — o motor (services/flex_motor)
    # decide o que fazer com cada caso e grava em flex_anuncio_estado, nunca
    # no `product_links.last_sync_status`.
    #
    # Leitura usa o `_request` (repete 429/5xx com espera: ler duas vezes não
    # muda nada). Escrita usa o `_request_uma_vez` (só repete o 401 depois do
    # refresh): um 5xx/429 volta para o motor marcar "tentar depois" na
    # próxima rodada, em vez de bater de novo no mesmo segundo — o ML responde
    # 409 a pedidos simultâneos no mesmo anúncio.

    def _caminho_flex(self, item_id: str | int) -> str:
        return f"/flex/sites/{flex_api.ML_SITE}/items/{str(item_id).strip()}/v2"

    async def ler_flex(self, item_id: str | int) -> flex_api.ResultadoFlex:
        """O anúncio está com Flex? (`has_flex`)."""
        try:
            r = await self._request("GET", self._caminho_flex(item_id))
        except Exception as exc:  # noqa: BLE001 — rede/refresh: classificado
            return flex_api.erro_de_rede(exc)
        return flex_api.classificar_ml("ler", r)

    async def ligar_flex(self, item_id: str | int) -> flex_api.ResultadoFlex:
        """Liga o Flex no anúncio (POST, sem corpo). 400 "already in flex" é
        sucesso. Só o motor chama, depois da aprovação de uma pessoa."""
        try:
            r = await self._request_uma_vez("POST", self._caminho_flex(item_id))
        except Exception as exc:  # noqa: BLE001
            return flex_api.erro_de_rede(exc)
        return flex_api.classificar_ml("ligar", r)

    async def desligar_flex(self, item_id: str | int) -> flex_api.ResultadoFlex:
        """Desliga o Flex no anúncio (DELETE).

        O retorno do DELETE num anúncio JÁ desligado não está documentado:
        num 400 a confirmação é uma leitura — se o GET diz `has_flex=false`, o
        que se queria já está feito (sucesso); senão fica o erro do DELETE."""
        try:
            r = await self._request_uma_vez("DELETE", self._caminho_flex(item_id))
        except Exception as exc:  # noqa: BLE001
            return flex_api.erro_de_rede(exc)
        res = flex_api.classificar_ml("desligar", r)
        if r.status_code == 400:
            lido = await self.ler_flex(item_id)
            if lido.ok and lido.has_flex is False:
                return flex_api.ResultadoFlex(
                    flex_api.OK,
                    has_flex=False,
                    status_http=400,
                    detalhe=f"já estava desligado ({res.detalhe})".strip(),
                )
        return res

    async def _id_vendedor(self) -> str | None:
        """`user_id` da conta (gravado no primeiro /users/me); sem ele, pergunta
        ao ML e guarda — o mesmo caminho do `list_listings`."""
        uid = self.creds.get("user_id")
        if uid:
            return str(uid)
        r = await self._request("GET", "/users/me")
        if r.status_code != 200:
            return None
        uid = (r.json() or {}).get("id")
        if uid and self.creds.get("user_id") != uid:
            self.creds["user_id"] = uid
            if self._on_refresh:
                await self._on_refresh(self.creds)
        return str(uid) if uid else None

    async def ler_assinatura_flex(self) -> flex_api.AssinaturaFlex:
        """A conta tem o Flex? (`/flex/sites/MLB/users/{id}/subscriptions/v1`).
        Nunca levanta: `AssinaturaFlex.ativo` None = não deu para saber."""
        try:
            uid = await self._id_vendedor()
            if not uid:
                return flex_api.AssinaturaFlex(None, None, "conta sem user_id")
            r = await self._request(
                "GET", f"/flex/sites/{flex_api.ML_SITE}/users/{uid}/subscriptions/v1"
            )
        except Exception as exc:  # noqa: BLE001 — rede/refresh: classificado
            return flex_api.assinatura_erro(exc)
        return flex_api.classificar_assinatura_ml(r)

    async def ids_da_conta(
        self, status: str, *, max_paginas: int = 100, pausa: float = 0.3
    ) -> flex_api.ListagemConta:
        """Ids de TODOS os anúncios da conta com esse `status` ("active" ou
        "paused") — a descoberta do Flex (projeto Flex, revisão de 02/10/2026):
        o anúncio criado depois da última importação, ou sem vínculo, também
        pode estar com o Flex ligado (nas contas "in" quase todos estão).

        `GET /users/{id}/items/search?search_type=scan&status=…`, 100 por
        página pelo `scroll_id` (a paginação por offset para nos 1.000), com
        `pausa` entre as páginas e no máximo `max_paginas` (o resto fica para a
        próxima, `completo=False`). Nunca levanta."""
        ids: list[str] = []
        try:
            uid = await self._id_vendedor()
            if not uid:
                return flex_api.ListagemConta(erro="conta sem user_id")
            scroll_id: str | None = None
            for pagina in range(max_paginas):
                params: dict[str, Any] = {"search_type": "scan", "limit": 100, "status": status}
                if scroll_id:
                    params["scroll_id"] = scroll_id
                r = await self._request("GET", f"/users/{uid}/items/search", params=params)
                if r.status_code != 200:
                    return flex_api.ListagemConta(
                        ids=tuple(ids), erro=f"busca {r.status_code} {r.text[:200]}".strip()
                    )
                data = r.json() or {}
                scroll_id = data.get("scroll_id") or scroll_id
                lote = [str(x).strip() for x in (data.get("results") or []) if str(x).strip()]
                if not lote:
                    return flex_api.ListagemConta(ids=tuple(ids), completo=True)
                ids.extend(lote)
                if pausa and pagina < max_paginas - 1:
                    await asyncio.sleep(pausa)
        except Exception as exc:  # noqa: BLE001 — rede/refresh: o que veio vale
            return flex_api.ListagemConta(ids=tuple(ids), erro=str(exc)[:300])
        return flex_api.ListagemConta(
            ids=tuple(ids), erro=f"parou no limite de {max_paginas} página(s)"
        )


# ---------------------------------------------------------------- helpers
#
# Keep ML's raw listing_type_id ("gold_special" / "gold_pro") in product_links
# and listings so the push resolver (sku_match.ml_listing_type_for_account)
# can filter against the SAME value the API returns. The translation to the
# user-facing "ml classico" / "ml premium" lives in the pricing UI / sku_match
# layer — *not* at the ingestion edge. Earlier the mapping happened here, so
# the auto-link path wrote display strings into product_links.listing_type
# and push filtered by API values → 0 matches.


def _conferir_canal_ml(
    item_id: str, item_info: dict, canal_esperado: str | None
) -> SyncResult | None:
    """SKIPPED quando o item vivo não é do canal da coluna (Catálogo ML,
    06/10/2026); None = pode enviar. Ver `update_price`."""
    if canal_esperado not in (CANAL_KIT, CANAL_CATALOGO):
        return None
    eh_catalogo = item_info.get("catalog_listing") is True
    if canal_esperado == CANAL_KIT:
        if eh_catalogo:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                error_code="canal_errado",
                error_detail=(
                    f"Anúncio {item_id} é de catálogo no ML: a coluna de Kit não "
                    "manda preço para anúncio de catálogo"
                ),
            )
        return None
    if not eh_catalogo:
        return SyncResult(
            status=SyncStatus.SKIPPED,
            error_code="canal_errado",
            error_detail=(
                f"Anúncio {item_id} não é de catálogo no ML: a coluna Catálogo só "
                "manda preço para anúncio de catálogo"
            ),
        )
    status = (item_info.get("status") or "").lower()
    motivo = motivo_por_status(status)
    if motivo:
        textos = {
            "pausado": "pausado",
            "em_revisao": "em revisão no Mercado Livre",
            "encerrado": "encerrado",
        }
        return SyncResult(
            status=SyncStatus.SKIPPED,
            error_code=motivo,
            error_detail=f"Anúncio de catálogo {item_id} {textos[motivo]} — não envia",
        )
    relacionados = [
        str(r.get("id"))
        for r in item_info.get("item_relations") or []
        if isinstance(r, dict) and r.get("id")
    ]
    # Lida na hora: a flag D2 mora num lugar só (services/pricing/anuncios).
    if _anuncios.CATALOGO_SINCRONIZADO_BLOQUEIA and relacionados:
        return SyncResult(
            status=SyncStatus.SKIPPED,
            error_code="sincronizado",
            error_detail=(
                f"Anúncio de catálogo {item_id} sincronizado com o anúncio comum "
                f"{', '.join(relacionados)} — o preço vem da coluna de Kit"
            ),
            payload={"sincronizado_com": relacionados},
        )
    return None


def _erro_automacao(item_id: str) -> SyncResult:
    return SyncResult(
        status=SyncStatus.FATAL,
        error_code="automacao_ml",
        error_detail=(
            f"O Mercado Livre recusou o preço do anúncio {item_id}: a automação "
            "de preços do ML está ligada nele. Desligue a automação no anúncio "
            "(Mercado Livre) para o DaVinci poder trocar o preço."
        ),
    )


def _recusa_por_automacao(payload: Any) -> bool:
    """A recusa do PUT de preço é a da automação de preços do ML? Procura
    "automat" (automation/automatic/automática…) na mensagem, no erro e nas
    causas da resposta."""
    if not isinstance(payload, dict):
        return False
    partes = [str(payload.get("message") or ""), str(payload.get("error") or "")]
    for c in payload.get("cause") or []:
        if isinstance(c, dict):
            partes.append(str(c.get("code") or ""))
            partes.append(str(c.get("message") or ""))
    return "automat" in " ".join(partes).lower()


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


def _ml_sku_oficial(obj: dict) -> str | None:
    """Só o atributo SELLER_SKU (o SKU que o vendedor edita hoje). Para
    decidir MOVER vínculo não usamos `seller_custom_field`: é o campo antigo e
    pode estar desatualizado — com ele um anúncio seria movido para o produto
    errado. Sem o atributo, None (= não conferir)."""
    for attr in obj.get("attributes") or []:
        if (attr.get("id") or "").upper() == "SELLER_SKU":
            v = (attr.get("value_name") or attr.get("value") or "").strip()
            return v or None
    return None


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


def _sku_trocado(
    sku_atual: str | None,
    sku_esperado: str | None,
    qty_before: int | None,
    item_id: str,
    variation_id: str | None,
) -> SyncResult | None:
    """Resultado `sku_trocado` quando o anúncio tem um SKU (não vazio)
    diferente do produto do vínculo; None quando está tudo certo."""
    if not sku_esperado or not sku_atual:
        return None
    if norm_sku(sku_atual) == norm_sku(sku_esperado):
        return None
    return SyncResult(
        status=SyncStatus.REQUIRES_REVIEW,
        qty_before=qty_before,
        error_code="sku_trocado",
        error_detail=f"anúncio agora com SKU {sku_atual} (vínculo em {sku_esperado})",
        payload={"item_id": item_id, "variation_id": variation_id, "sku_atual": sku_atual},
    )


def _stock_variation_ids(variations: Any) -> list[int] | None:
    """A PUT variations array replaces membership: never omit a sibling ID.

    Retain only IDs for siblings, without resending potentially stale stock.
    If the complete set cannot be identified, refuse the stock update.
    """
    if not isinstance(variations, list):
        return None
    ids = []
    for variation in variations:
        raw = variation.get("id") if isinstance(variation, dict) else None
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            return None
        value = str(raw)
        if not value.isascii() or not value.isdigit() or int(value) <= 0:
            return None
        variation_id = int(value)
        if variation_id in ids:
            return None
        ids.append(variation_id)
    return ids


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
