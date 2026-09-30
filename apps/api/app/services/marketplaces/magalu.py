"""Magalu (Magazine Luiza) Open API client — conector OAuth nativo.

Espelha `MercadoLivreClient` (OAuth2 authorization_code + refresh automático em
401), mas o portfólio Magalu é endereçado por **SKU**, não por item_id +
variation_id. Logo, para links Magalu:
  * `link.external_id` == o SKU do seller na Magalu (forma com hífen);
  * `link.variation_id` == None;
  * stock/price dão PATCH em `/seller/v1/portfolios/{stocks|prices}/{sku}`.

Regra de SKU desta integração: pontos e '+' do catálogo viram hífens na
Magalu. A conversão inversa é ambígua para kits e não recupera o SKU canônico.
`list_listings` preserva:
  * `external_id = "b001-20"`  (SKU cru da Magalu — usado no PATCH e gravado em
    product_links.external_id);
  * `sku = "b001.20"` (normalização legada de hífens para pontos).
O vínculo em `auto_link` e `listings_import` compara o external_id cru com um
índice do catálogo codificado para Magalu. Só uma correspondência única permite
vincular, preservando o SKU real do produto e os '+' dos kits.

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
                body {"quantity": <int>, "type": "AVAILABLE",
                "channel": {"id": <uuid>}} → 202 (async = OK)
                409 só é OK após GET confirmar a quantidade no mesmo estoque.
  * PATCH price: PATCH /seller/v1/portfolios/prices/{sku}
                body {"price": <cent>, "list_price": <cent>, "currency": "BRL",
                "normalizer": 100, "channel": {"id": <uuid>}} → 202. Preços são
                INTEIROS em centavos (normalizer=100 é o divisor: 9980 == R$ 99,80).
  ⚠️ `channel.id` é o canal de venda do seller (nível conta) e é OBRIGATÓRIO nos
     PATCH de estoque/preço (sem ele a Magalu devolve 422 "channel: Field
     required"). Não há endpoint de listagem de canais — descobrimos o id lendo o
     sub-recurso `stocks`/`prices` de qualquer SKU do portfólio
     (`results[0].channel.id`) e cacheamos no cliente (mesmo canal p/ todos os
     SKUs da integração).
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
MAGALU_TOKEN_URL = "https://id.magalu.com/oauth/token"  # noqa: S105 - endpoint, not a secret
MAGALU_API_BASE = "https://api.magalu.com"
# Perguntas & Respostas e Chat com Cliente moram em OUTRO servidor (o SAC fica
# no `api.magalu.com`). Mesmo token, mesmo proxy, mesma renovação: só muda a
# base da URL (`_request(..., base=MAGALU_SERVICES_BASE)`).
MAGALU_SERVICES_BASE = "https://services.magalu.com"

# Limites de texto das APIs de atendimento (developers.magalu.com, 30/09/2026):
# chat `content` maxLength 2200; SAC `message` maxLength 3000. A resposta de
# pergunta não tem limite documentado — quem decide é a caixa
# (`atendimento.constantes.limite_caracteres`).
LIMITE_CHAT = 2200
LIMITE_SAC = 3000

# Escopos solicitados pelo DaVinci, separados por espaço no authorize.
# Além de produtos e pedidos, inclui perguntas, conversas e SAC. Novos
# escopos precisam estar liberados no app ID Magalu e receber consentimento
# do seller; renovar o token existente não concede permissões adicionais.
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
        "services:questions-seller:read",
        "services:questions-seller:write",
        "services:conversations-seller:read",
        "services:conversations-seller:write",
        "open:tickets-seller:read",
        "open:tickets-seller:write",
        "open:ticket-messages-seller:read",
        "open:ticket-messages-seller:write",
    ]
)

# Máximo de itens por página aceito pelo list de SKUs (schema: _limit max=100).
_MAX_PAGE_SIZE = 100
# SAC (get_tickets e get_ticket_messages): a OpenAPI limita também o
# `_offset` a 100 — acima disso a Magalu responde 422. Quem precisa de mais
# anda pela DATA (`updated_at_gte`) ou pela ordem (`_sort`), não pelo offset.
_MAX_OFFSET_SAC = 100


def _http_client(timeout: float) -> httpx.AsyncClient:
    """AsyncClient com proxy opcional — usado por TODA saída HTTP da Magalu.

    `MAGALU_PROXY_URL` direciona exchange, refresh e chamadas de API pela saída
    configurada. Vazia mantém conexão direta. As demais integrações não passam
    por aqui; o proxy CONNECT preserva o TLS entre este cliente e a Magalu.
    """
    proxy = get_settings().magalu_proxy_url or None
    return httpx.AsyncClient(timeout=timeout, proxy=proxy)


class MagaluClient:
    def __init__(self, creds: dict, on_token_refresh=None, integration_id=None):
        self.creds = dict(creds)
        self._on_refresh = on_token_refresh
        self._integration_id = integration_id
        # channel.id do seller (obrigatório nos PATCH de estoque/preço). Fica
        # None até a 1ª descoberta e é cacheado aqui pelo resto do ciclo de vida
        # do cliente (o orchestrator reusa 1 cliente por integração no run).
        self._channel_id: str | None = None

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
        async with _http_client(20.0) as c:
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

    async def refresh(
        self, *, expired_only: bool = False, rejected_access_token: str | None = None,
    ) -> None:
        """Serialize refreshes for a saved integration across workers/processes.

        The row lock covers rereading, token rotation and its durable commit.
        Caller callbacks may own other sessions, so they are used only for
        clients without an integration ID. No token is adopted before commit.
        """
        if self._integration_id is None:
            new_creds = await self._fetch_refreshed_credentials(self.creds)
            if self._on_refresh:
                await self._on_refresh(new_creds)
            self.creds = new_creds
            return

        from sqlalchemy import select

        from app import db
        from app.models import Integration, IntegrationPlatform
        from app.security.cipher import decrypt_json, encrypt_json

        observed_tokens = (self.access_token, self.creds.get("refresh_token"))
        async with db.SessionLocal() as session:
            async with session.begin():
                integration = await session.scalar(
                    select(Integration)
                    .where(
                        Integration.id == self._integration_id,
                        Integration.platform == IntegrationPlatform.MAGALU,
                    )
                    # FK inserts in the caller can hold KEY SHARE on this row.
                    # NO KEY UPDATE serializes rotations without blocking those
                    # callers; refresh never changes the integration's key.
                    .with_for_update(key_share=True)
                )
                if integration is None:
                    raise RuntimeError("magalu_refresh_integration_missing")
                current = decrypt_json(integration.credentials)
                current_tokens = (current.get("access_token"), current.get("refresh_token"))
                expiry = int(current.get("expires_at") or 0)
                usable = bool(current_tokens[0]) and (
                    not expiry or expiry - 30 > int(time.time())
                )
                if rejected_access_token is not None:
                    reuse = usable and current_tokens[0] != rejected_access_token
                else:
                    reuse = usable and (expired_only or current_tokens != observed_tokens)
                if reuse:
                    new_creds = current
                else:
                    new_creds = await self._fetch_refreshed_credentials(current)
                    integration.credentials = encrypt_json(new_creds)
                    integration.token_expires_at = datetime.fromtimestamp(
                        int(new_creds["expires_at"]), tz=UTC,
                    )
            # Commit must succeed before any API request uses the new token.
            self.creds = new_creds

    async def _fetch_refreshed_credentials(self, creds: dict) -> dict:
        """Refresh uses form-urlencoded; keep rotation separate from persistence."""
        rt = creds.get("refresh_token")
        if not rt:
            raise RuntimeError("missing refresh_token")
        s = get_settings()
        cid = str(creds.get("client_id") or s.magalu_client_id or "")
        csec = str(creds.get("client_secret") or s.magalu_client_secret or "")
        if not cid or not csec:
            raise RuntimeError("missing client_id or client_secret")
        async with _http_client(20.0) as c:
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
            return _normalize_token(r.json(), prev=creds)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        base: str = MAGALU_API_BASE,
        repetir_5xx: bool = True,
    ) -> httpx.Response:
        """Uma chamada à Magalu: token renovado (401 → refresh), proxy e 429 com espera.

        `base`: `MAGALU_API_BASE` (portfólio, pedidos, SAC) ou
        `MAGALU_SERVICES_BASE` (perguntas e chat).
        `repetir_5xx=False` é para o POST de mensagem ao comprador: 502/503/504
        pode ter sido processado, e repetir mandaria a mesma frase duas vezes.
        O 429 continua repetido (a Magalu recusou antes de processar).

        O `read_by` (marca a conversa como lida) é recusado AQUI, antes de
        sair: enquanto a equipe ler pelo portal, o "não lido" é dela.
        """
        if "read_by" in path:
            raise RuntimeError("magalu_read_by_proibido")
        if self._expired():
            await self.refresh(expired_only=True)
        url = f"{base}{path}"
        request_token = self.access_token
        headers = {
            "Authorization": f"Bearer {request_token}",
            "Accept": "application/json",
        }
        delay = 1.0
        r: httpx.Response | None = None
        for attempt in range(3):
            async with _http_client(30.0) as c:
                r = await c.request(
                    method, url, headers=headers, params=params, json=json
                )
            if r.status_code == 401 and attempt == 0:
                await self.refresh(rejected_access_token=request_token or "")
                request_token = self.access_token
                headers["Authorization"] = f"Bearer {request_token}"
                continue
            if r.status_code == 429 or (repetir_5xx and r.status_code in (502, 503, 504)):
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
            return TestResult(ok=False, detail=f"http_error: {_http_error_detail(e)}")
        except Exception as e:  # noqa: BLE001
            return TestResult(ok=False, detail=f"error: {e}")

    async def get_sku(self, sku: str) -> dict:
        """Consulta 1 SKU do portfólio. `sku` é a forma crua (hífen) da Magalu."""
        r = await self._request(
            "GET", f"/seller/v1/portfolios/skus/{quote(sku, safe='')}"
        )
        r.raise_for_status()
        return r.json() or {}

    async def _discover_channel_id(self, sku: str) -> str | None:
        """Descobre (e cacheia) o `channel.id` do seller a partir de um SKU.

        Os PATCH de estoque/preço exigem `channel: {"id": <uuid>}` — o canal de
        venda do seller, nível conta. Como não há endpoint de listagem de canais,
        lemos o sub-recurso de estoque (e, em fallback, o de preço) de um SKU
        qualquer do portfólio e extraímos `results[0].channel.id`. É o mesmo canal
        pra todos os SKUs da integração, então cacheamos no cliente e as chamadas
        seguintes saem de graça.
        """
        if self._channel_id:
            return self._channel_id
        for sub in ("stocks", "prices"):
            try:
                r = await self._request(
                    "GET", f"/seller/v1/portfolios/{sub}/{quote(sku, safe='')}"
                )
            except httpx.HTTPError:
                continue
            if r.status_code != 200:
                continue
            results = (r.json() or {}).get("results") or []
            for row in results:
                if not isinstance(row, dict):
                    continue
                ch = row.get("channel")
                cid = ch.get("id") if isinstance(ch, dict) else None
                if cid:
                    self._channel_id = str(cid)
                    logger.info(
                        "magalu_channel_discovered",
                        channel_id=self._channel_id,
                        via=sub,
                    )
                    return self._channel_id
        return None

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

        # channel.id é obrigatório no corpo (sem ele → 422). Descoberto 1x por
        # integração e cacheado no cliente.
        channel_id = await self._discover_channel_id(sku)
        if not channel_id:
            return SyncResult(
                status=SyncStatus.RETRYABLE,
                qty_before=qty_before,
                error_code="magalu_channel_unknown",
                error_detail=f"não descobriu channel.id (sku={sku})",
            )

        try:
            r = await self._request(
                "PATCH",
                f"/seller/v1/portfolios/stocks/{quote(sku, safe='')}",
                json={
                    "quantity": int(qty),
                    "type": "AVAILABLE",
                    "channel": {"id": channel_id},
                },
            )
        except httpx.HTTPError as e:
            return _map_http_error(e, qty_before, "magalu_patch_stock_failed")
        # 202 Accepted = update aceito (processamento assíncrono).
        if r.status_code in (200, 202):
            payload = {"sku": sku, "http_status": r.status_code}
            # Preserve the tracking ID without storing the provider's full body.
            try:
                body = r.json()
            except ValueError:
                body = None
            trace_id = body.get("trace_id") if isinstance(body, dict) else None
            if isinstance(trace_id, str) and 0 < len(trace_id) <= 100 and trace_id.strip():
                payload["trace_id"] = trace_id
            return SyncResult(
                status=SyncStatus.OK,
                qty_before=qty_before,
                qty_after=qty,
                payload=payload,
            )
        if r.status_code == 409:
            verification = await self._verify_stock_conflict(sku, channel_id, int(qty))
            payload = {"sku": sku, "http_status": 409, "stock_verification": verification}
            if verification.get("confirmed"):
                return SyncResult(
                    status=SyncStatus.OK,
                    qty_before=qty_before,
                    qty_after=qty,
                    payload=payload,
                )
            result = _map_status_error(r, qty_before, "magalu_patch_stock_status")
            result.payload = payload
            return result
        if r.status_code == 404:
            return SyncResult(
                status=SyncStatus.FATAL,
                qty_before=qty_before,
                error_code="magalu_sku_not_found",
                error_detail=f"sku={sku}",
            )
        return _map_status_error(r, qty_before, "magalu_patch_stock_status")

    async def _verify_stock_conflict(self, sku: str, channel_id: str, qty: int) -> dict:
        """A conflict is successful only when a fresh read proves the target stock.

        This client's PATCH omits branch, so only an unassigned/default stock
        (branch absent or null) can confirm it. Never combine CDs or RESERVED
        stock, and reject duplicate matching rows as ambiguous.
        """
        verification: dict[str, Any] = {"confirmed": False, "target_quantity": qty}
        try:
            response = await self._request(
                "GET", f"/seller/v1/portfolios/stocks/{quote(sku, safe='')}"
            )
            verification["http_status"] = response.status_code
            if response.status_code != 200:
                verification["reason"] = "read_failed"
                return verification
            data = response.json()
        except Exception as exc:  # noqa: BLE001 - preserve the original PATCH failure
            # Exception text may contain proxy credentials or token refresh bodies.
            verification.update(reason="read_failed", error_type=type(exc).__name__)
            return verification
        rows = data.get("results") if isinstance(data, dict) else None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            verification["reason"] = "invalid_response"
            return verification
        matches = [
            row for row in rows
            if row.get("type") == "AVAILABLE"
            and isinstance(row.get("channel"), dict)
            and row["channel"].get("id") == channel_id
            and row.get("branch") is None
        ]
        if len(matches) != 1:
            verification.update(reason="stock_not_unique", matching_rows=len(matches))
            return verification
        observed = matches[0].get("quantity")
        if type(observed) is not int:
            verification["reason"] = "invalid_quantity"
            return verification
        verification.update(
            confirmed=observed == qty,
            reason="target_confirmed" if observed == qty else "quantity_mismatch",
            observed_quantity=observed,
        )
        return verification

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
        # channel.id é obrigatório no corpo do PATCH de preço, igual ao estoque.
        channel_id = await self._discover_channel_id(sku)
        if not channel_id:
            return SyncResult(
                status=SyncStatus.RETRYABLE,
                error_code="magalu_channel_unknown",
                error_detail=f"não descobriu channel.id (sku={sku})",
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
                    "channel": {"id": channel_id},
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

    # ------------------------------------------------------- atendimento
    # Leitura e resposta das três caixas do vendedor (caixa /atendimento,
    # `services/atendimento/magalu.py`). As LEITURAS devolvem o JSON e levantam
    # `httpx.HTTPStatusError` fora do 2xx (quem chama traduz 401/403/429); os
    # ENVIOS devolvem a resposta crua, para quem chama decidir entre "saiu",
    # "recusou" e "pode ter saído" — e nunca repetem 5xx.
    #
    # NÃO existe (e não pode existir) chamada ao `PATCH .../read_by`: marcar
    # como lido tiraria o aviso de quem lê pelo portal. O `_request` recusa.

    async def _ler_json(
        self, path: str, *, base: str, params: dict | None = None
    ) -> dict:
        r = await self._request("GET", path, params=params, base=base)
        r.raise_for_status()
        if not r.content:
            return {}
        corpo = r.json()
        return corpo if isinstance(corpo, dict) else {}

    # Perguntas & Respostas (pré-venda) — services:questions-seller:read/write.

    async def perguntas(
        self, *, status: str, offset: int = 0, limit: int = _MAX_PAGE_SIZE
    ) -> dict:
        """GET /v0/questions (WAITING_RESPONSE | APPROVED | REJECTED_RESPONSE)."""
        return await self._ler_json(
            "/v0/questions",
            base=MAGALU_SERVICES_BASE,
            params={
                "status": status,
                "_offset": max(0, int(offset)),
                "_limit": min(max(int(limit), 1), _MAX_PAGE_SIZE),
            },
        )

    async def pergunta(self, question_id: str) -> dict:
        """GET /v0/questions/{id}."""
        return await self._ler_json(
            f"/v0/questions/{quote(str(question_id), safe='')}", base=MAGALU_SERVICES_BASE
        )

    async def responder_pergunta(
        self,
        question_id: str,
        mensagem: str,
        *,
        autor_nome: str,
        autor_id: str,
        ref: str | None = None,
    ) -> httpx.Response:
        """POST /v0/questions/{id}/answer → 202 (a resposta vai para a moderação).

        `ref` vai em `external_id` e volta na leitura (`answer.external_id`):
        é por ele que o sync reconhece a resposta que saiu daqui.
        """
        corpo: dict[str, Any] = {
            "message": mensagem,
            "owner": {"name": autor_nome, "external_id": autor_id},
        }
        if ref:
            corpo["external_id"] = ref
        return await self._request(
            "POST",
            f"/v0/questions/{quote(str(question_id), safe='')}/answer",
            json=corpo,
            base=MAGALU_SERVICES_BASE,
            repetir_5xx=False,
        )

    # Chat com Cliente — services:conversations-seller:read/write.

    async def conversas(
        self,
        *,
        status: str = "OPENED",
        desde: str | None = None,
        ate: str | None = None,
        offset: int = 0,
        limit: int = _MAX_PAGE_SIZE,
    ) -> dict:
        """GET /v0/conversations, filtrando pela última interação (`desde`/`ate`, ISO)."""
        params: dict[str, Any] = {
            "status": status,
            "_offset": max(0, int(offset)),
            "_limit": min(max(int(limit), 1), _MAX_PAGE_SIZE),
        }
        if desde:
            params["last_interaction_at_start"] = desde
        if ate:
            params["last_interaction_at_end"] = ate
        return await self._ler_json("/v0/conversations", base=MAGALU_SERVICES_BASE, params=params)

    async def conversa(self, conversation_id: str) -> dict:
        """GET /v0/conversations/{id} (status, não lidas, última interação)."""
        return await self._ler_json(
            f"/v0/conversations/{quote(str(conversation_id), safe='')}",
            base=MAGALU_SERVICES_BASE,
        )

    async def mensagens_da_conversa(
        self, conversation_id: str, *, offset: int = 0, limit: int = _MAX_PAGE_SIZE
    ) -> dict:
        """GET /v0/conversations/{id}/messages. Só lê: não registra leitura."""
        return await self._ler_json(
            f"/v0/conversations/{quote(str(conversation_id), safe='')}/messages",
            base=MAGALU_SERVICES_BASE,
            params={
                "_offset": max(0, int(offset)),
                "_limit": min(max(int(limit), 1), _MAX_PAGE_SIZE),
            },
        )

    async def enviar_mensagem_conversa(
        self,
        conversation_id: str,
        conteudo: str,
        *,
        autor_nome: str,
        autor_id: str,
        ref: str | None = None,
    ) -> httpx.Response:
        """POST /v0/conversations/{id}/messages (até 2200 caracteres) → 201 com o `id`."""
        if len(conteudo) > LIMITE_CHAT:
            raise ValueError(f"magalu_chat_acima_do_limite ({len(conteudo)} > {LIMITE_CHAT})")
        corpo: dict[str, Any] = {
            "content": conteudo,
            "owner": {"name": autor_nome, "external_id": autor_id},
        }
        if ref:
            corpo["external_id"] = ref
        return await self._request(
            "POST",
            f"/v0/conversations/{quote(str(conversation_id), safe='')}/messages",
            json=corpo,
            base=MAGALU_SERVICES_BASE,
            repetir_5xx=False,
        )

    # SAC (protocolos de pós-venda) — open:tickets-seller:read e
    # open:ticket-messages-seller:read/write. Servidor api.magalu.com.

    async def tickets(
        self,
        *,
        status: str | None = None,
        atualizado_desde: str | None = None,
        ordem: str | None = None,
        offset: int = 0,
        limit: int = _MAX_PAGE_SIZE,
    ) -> dict:
        """GET /seller/v0/tickets (status=waiting_seller é a fila; `due_date` é o prazo).

        `_offset` vai no máximo a 100 (a OpenAPI do SAC recusa acima disso).
        """
        params: dict[str, Any] = {
            "_offset": min(max(0, int(offset)), _MAX_OFFSET_SAC),
            "_limit": min(max(int(limit), 1), _MAX_PAGE_SIZE),
        }
        if status:
            params["status"] = status
        if atualizado_desde:
            params["updated_at_gte"] = atualizado_desde
        if ordem:
            params["_sort"] = ordem
        return await self._ler_json("/seller/v0/tickets", base=MAGALU_API_BASE, params=params)

    async def ticket(self, ticket_id: str) -> dict:
        """GET /seller/v0/tickets/{id}."""
        return await self._ler_json(
            f"/seller/v0/tickets/{quote(str(ticket_id), safe='')}", base=MAGALU_API_BASE
        )

    async def mensagens_do_ticket(
        self,
        ticket_id: str,
        *,
        offset: int = 0,
        limit: int = _MAX_PAGE_SIZE,
        ordem: str = "created_at:desc",
    ) -> dict:
        """GET /seller/v0/tickets/{id}/messages, da mais NOVA para a mais antiga.

        Com o `_offset` no máximo em 100, duas páginas trazem as 200 mais
        recentes — as que importam para a fila.
        """
        return await self._ler_json(
            f"/seller/v0/tickets/{quote(str(ticket_id), safe='')}/messages",
            base=MAGALU_API_BASE,
            params={
                "_offset": min(max(0, int(offset)), _MAX_OFFSET_SAC),
                "_limit": min(max(int(limit), 1), _MAX_PAGE_SIZE),
                "_sort": ordem,
            },
        )

    async def enviar_mensagem_ticket(
        self,
        ticket_id: str,
        mensagem: str,
        *,
        autor_nome: str,
        autor_codigo: str,
        destino: str = "customer",
        ref: str | None = None,
    ) -> httpx.Response:
        """POST /seller/v0/tickets/{id}/messages (até 3000) → 202 + `transaction_id`.

        O 202 é assíncrono e não traz o id da mensagem: `ref` vai em `code`
        (campo livre do seller) e volta na leitura — é por ele que o sync
        reconhece a nossa.
        """
        if len(mensagem) > LIMITE_SAC:
            raise ValueError(f"magalu_sac_acima_do_limite ({len(mensagem)} > {LIMITE_SAC})")
        corpo: dict[str, Any] = {
            "message": mensagem,
            "destination": destino,
            "owner": {"code": autor_codigo, "name": autor_nome},
        }
        if ref:
            corpo["code"] = ref
        return await self._request(
            "POST",
            f"/seller/v0/tickets/{quote(str(ticket_id), safe='')}/messages",
            json=corpo,
            base=MAGALU_API_BASE,
            repetir_5xx=False,
        )


# ---------------------------------------------------------------- helpers


def _canonical_sku(raw: str) -> str:
    """Normalização legada do SKU cru: hífens viram pontos.

    Não recupera os '+' dos kits. Os vínculos usam o external_id cru e o
    catálogo codificado para Magalu, recusando correspondências ambíguas.
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

    `external_id` fica com o SKU CRU (id do recurso usado no PATCH); `sku`
    recebe a normalização legada. O vínculo resolve o catálogo pelo ID cru.
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


def _http_error_detail(e: httpx.HTTPError) -> str:
    """Diagnóstico útil mesmo quando httpx não traz mensagem.

    Não reproduzimos `str(e)`: erros de transporte podem conter a URL do proxy
    (incluindo usuário/senha), headers ou tokens. O tipo e uma descrição fixa
    identificam a etapa que falhou sem expor esses dados.
    """
    descriptions = (
        (httpx.ProxyError, "falha ao conectar pelo proxy configurado"),
        (httpx.ConnectTimeout, "tempo esgotado ao estabelecer conexão"),
        (httpx.ReadTimeout, "tempo esgotado aguardando a resposta"),
        (httpx.WriteTimeout, "tempo esgotado ao enviar a requisição"),
        (httpx.PoolTimeout, "tempo esgotado aguardando uma conexão disponível"),
        (httpx.ConnectError, "não foi possível estabelecer conexão"),
        (httpx.ReadError, "falha ao receber a resposta"),
        (httpx.WriteError, "falha ao enviar a requisição"),
        (httpx.RemoteProtocolError, "resposta inválida ou conexão encerrada pelo destino"),
        (httpx.LocalProtocolError, "falha ao preparar a requisição HTTP"),
        (httpx.UnsupportedProtocol, "protocolo de conexão não suportado"),
        (httpx.TooManyRedirects, "limite de redirecionamentos excedido"),
    )
    description = next(
        (text for kind, text in descriptions if isinstance(e, kind)),
        "falha na comunicação HTTP",
    )
    response = getattr(e, "response", None)
    status = f" status={response.status_code}" if response is not None else ""
    return f"{type(e).__name__}: {description}{status}"


def _map_http_error(
    e: httpx.HTTPError, qty_before: int | None, code: str
) -> SyncResult:
    status: SyncStatus = SyncStatus.RETRYABLE
    detail = _http_error_detail(e)
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
