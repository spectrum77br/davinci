"""Carrefour Brasil / Mirakl: authenticated reads; the only remote write is M12.

Credentials: api_key, shop_id. The server-owned _carrefour_account_check caches
A01 for 24 hours; on_token_refresh persists this metadata, not a rotated key.
Never accept that cache from user input. See docs/carrefour-integracao.md.

Messages (30/10/2026, the atendimento chat): M11 lists inbox threads and M12
replies on an existing thread. Offers, stock, prices, invoices and shipping
stay read-only (`update_stock` never calls HTTP).
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import re
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import httpx

from app.services.marketplaces.base import SyncResult, SyncStatus, TestResult

if TYPE_CHECKING:
    from app.models import ProductLink

CARREFOUR_API_BASE = "https://marketplace.carrefour.com.br/api"
ACCOUNT_CACHE_KEY = "_carrefour_account_check"
ACCOUNT_CACHE_SECONDS = 86400
_ACCOUNT_CACHE: dict[str, dict] = {}
_ACCOUNT_LOCK = asyncio.Lock()
INBOX_THREADS_PATH = "/inbox/threads"
_READ_PATHS = frozenset({"/account", "/offers", "/orders", INBOX_THREADS_PATH})
# M11 seek pagination: `limit` up to 100; with `page_token` Mirakl ignores the
# other filters. The token is opaque, but it only ever goes back to the fixed host.
INBOX_PAGE_MAX = 100
# Thread ids are UUIDs; anything else never becomes part of a request path.
_RE_THREAD_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_RE_PAGE_TOKEN = re.compile(r"^[A-Za-z0-9._~+/=-]{1,2048}$")
REPLY_RECIPIENT_TYPES = frozenset({"CUSTOMER", "OPERATOR"})
# M12 `message_input.body`: minLength 3, maxLength 50000.
REPLY_BODY_MIN = 3
REPLY_BODY_MAX = 50000


class CarrefourError(RuntimeError):
    """Safe diagnostics: no remote response body, headers, URL or exception text."""

    def __init__(
        self, code: str, detail: str, *, http_status: int | None = None,
        retry_after: int | None = None,
    ):
        super().__init__(detail)
        self.code = code
        self.http_status = http_status
        self.retry_after = retry_after


def _positive_id(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    text = str(value).strip()
    if len(text) > 19 or not text.isascii() or not text.isdecimal():
        return None
    number = int(text)
    return number if 0 < number <= 9223372036854775807 else None


def _retry_after(value: str | None) -> int:
    """Retry-After accepts seconds or an HTTP date; never retry before it."""
    if value:
        try:
            if value.strip().isdigit():
                return max(0, int(value))
            moment = parsedate_to_datetime(value)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            return max(0, math.ceil(moment.timestamp() - time.time()))
        except (ValueError, TypeError, OverflowError):
            pass
    return 60


def _number(value: object, *, integer: bool = False) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0 or (integer and not number.is_integer()):
        return None
    return int(number) if integer else number


def _text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


def _price_cents(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        price = Decimal(str(value))
        if not price.is_finite() or price < 0:
            return None
        cents = int((price * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return cents if cents <= 9223372036854775807 else None
    except (DecimalException, ValueError, OverflowError):
        return None


def _normalize_offer(offer: dict) -> dict:
    offer_id = _positive_id(offer.get("offer_id"))
    if offer_id is None:
        raise CarrefourError("invalid_offer", "O Carrefour retornou uma oferta sem ID válido.")
    pricing = offer.get("applicable_pricing")
    price = pricing.get("price") if isinstance(pricing, dict) else None
    if price is None:
        price = offer.get("price")
    return {
        "external_id": str(offer_id),
        "variation_id": None,
        "sku": _text(offer.get("shop_sku")),
        "title": _text(offer.get("product_title")) or str(offer_id),
        "description": _text(offer.get("product_description")) or _text(offer.get("description")),
        "price": _price_cents(price),
        "stock": _number(offer.get("quantity"), integer=True),
        "status": "active" if offer.get("active") is True else "inactive",
        "category": _text(offer.get("category_label")) or _text(offer.get("category_code")),
        # OF21 does not document a product image field; do not guess one.
        "thumbnail_url": None,
        "listing_type": None,
        "raw": offer,
    }


class CarrefourClient:
    def __init__(self, creds: dict, on_token_refresh=None, integration_id=None):
        self.creds = copy.deepcopy(creds)
        self._on_refresh = on_token_refresh
        self._integration_id = integration_id

    def _credentials(self) -> tuple[str, int]:
        api_key = self.creds.get("api_key")
        shop_id = _positive_id(self.creds.get("shop_id"))
        if not isinstance(api_key, str) or not api_key.strip() or shop_id is None:
            raise CarrefourError("invalid_credentials", "Informe a chave de API e o ID da loja.")
        api_key = api_key.strip()
        if not api_key.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in api_key):
            raise CarrefourError("invalid_credentials", "A chave de API possui formato inválido.")
        return api_key, shop_id

    async def _read_json(self, path: str, params: dict | None = None) -> dict:
        if path not in _READ_PATHS:
            raise CarrefourError("unsupported_read", "Consulta Carrefour não habilitada.")
        api_key, shop_id = self._credentials()
        query = {**(params or {}), "shop_id": shop_id}
        # No caller-supplied host, URL from API response or auth redirect is followed.
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            attempts = 1 if path == "/account" else 2
            for attempt in range(attempts):
                try:
                    response = await client.get(
                        f"{CARREFOUR_API_BASE}{path}", params=query,
                        headers={"Authorization": api_key, "Accept": "application/json"},
                    )
                except httpx.TimeoutException:
                    raise CarrefourError("timeout", "O Carrefour demorou a responder.") from None
                except httpx.HTTPError:
                    raise CarrefourError(
                        "transport_error", "Não foi possível conectar ao Carrefour."
                    ) from None
                if response.status_code == 429:
                    delay = _retry_after(response.headers.get("Retry-After"))
                    if attempt + 1 < attempts and delay <= 5:
                        await asyncio.sleep(delay)
                        continue
                    raise CarrefourError(
                        "rate_limited",
                        f"Limite de consultas do Carrefour. Aguarde {delay} segundos.",
                        http_status=429, retry_after=delay,
                    )
                status = response.status_code
                if status != 200:
                    descriptions = {
                        401: "A chave de API do Carrefour foi recusada.",
                        403: "O usuário não possui permissão para esta consulta no Carrefour.",
                    }
                    detail = descriptions.get(
                        status, f"Falha na consulta ao Carrefour (HTTP {status})."
                    )
                    if 300 <= status < 400:
                        detail = "O Carrefour redirecionou a consulta; confirme a chave de API."
                    raise CarrefourError("http_error", detail, http_status=status)
                try:
                    data = response.json()
                except ValueError:
                    raise CarrefourError(
                        "invalid_response", "O Carrefour retornou uma resposta inválida."
                    ) from None
                if not isinstance(data, dict):
                    raise CarrefourError(
                        "invalid_response", "O Carrefour retornou formato inesperado."
                    )
                return data
        raise CarrefourError("request_failed", "Não foi possível concluir a consulta ao Carrefour.")

    @staticmethod
    def _cached(check: object, fingerprint: str, now: int) -> bool:
        if not isinstance(check, dict) or check.get("fingerprint") != fingerprint:
            return False
        checked_at = check.get("checked_at")
        return (
            isinstance(checked_at, int) and not isinstance(checked_at, bool)
            and 0 <= now - checked_at < ACCOUNT_CACHE_SECONDS
            and isinstance(check.get("ok"), bool)
            and isinstance(check.get("info"), dict)
            and (check.get("detail") is None or isinstance(check.get("detail"), str))
            and "detail" in check
        )

    async def _save_check(self, check: dict) -> None:
        updated = {**self.creds, ACCOUNT_CACHE_KEY: copy.deepcopy(check)}
        if self._on_refresh:
            try:
                await self._on_refresh(copy.deepcopy(updated))
            except Exception:
                # The check stays in memory to avoid reissuing A01 after a save failure.
                raise CarrefourError(
                    "cache_persistence_failed",
                    "A consulta foi realizada, mas não foi possível salvar sua verificação.",
                ) from None
        self.creds = updated

    async def _check_account(self, shop_id: int, fingerprint: str, now: int) -> dict:
        try:
            account = await self._read_json("/account")
            state = account.get("shop_state")
            name = _text(account.get("shop_name"))
            if _positive_id(account.get("shop_id")) != shop_id:
                raise CarrefourError(
                    "shop_mismatch", "A resposta do Carrefour pertence a outra loja."
                )
            if (
                not name or not isinstance(state, str)
                or state not in {"OPEN", "CLOSE", "SUSPENDED", "TERMINATED"}
            ):
                raise CarrefourError(
                    "invalid_account", "O Carrefour não confirmou nome e estado da loja."
                )
            details = {
                "CLOSE": "A loja Carrefour está fechada.",
                "SUSPENDED": "A loja Carrefour está suspensa.",
                "TERMINATED": "A loja Carrefour foi encerrada.",
            }
            check = {
                "ok": state == "OPEN", "detail": details.get(state),
                "info": {"shop_id": shop_id, "shop_name": name, "shop_state": state},
            }
        except CarrefourError as error:
            check = {
                "ok": False, "detail": str(error), "info": {"error_code": error.code},
            }
            if error.retry_after is not None:
                check["info"]["retry_after"] = error.retry_after
        check.update({"fingerprint": fingerprint, "checked_at": now})
        return check

    @staticmethod
    def _result(check: dict, *, cached: bool) -> TestResult:
        return TestResult(
            ok=check["ok"], detail=check["detail"],
            info={**check["info"], "mode": "read_only", "cached": cached,
                  "checked_at": check["checked_at"]},
        )

    async def _test_saved_integration(self) -> TestResult:
        """One A01 per integration across API/worker processes; no stale callback writes.

        The independent transaction locks the row before rereading credentials.
        Credential edits wait for it, so metadata cannot restore an obsolete key.
        NO KEY UPDATE is compatible with callers holding FK KEY SHARE locks.
        """
        from sqlalchemy import select

        from app import db
        from app.models import Integration, IntegrationPlatform
        from app.security.cipher import decrypt_json, encrypt_json

        try:
            async with db.SessionLocal() as session:
                async with session.begin():
                    integration = await session.scalar(
                        select(Integration).where(
                            Integration.id == self._integration_id,
                            Integration.platform == IntegrationPlatform.CARREFOUR,
                        ).with_for_update(key_share=True)
                    )
                    if integration is None:
                        raise CarrefourError(
                            "integration_missing", "A integração Carrefour não foi encontrada."
                        )
                    current = decrypt_json(integration.credentials)
                    self.creds = copy.deepcopy(current)
                    api_key, shop_id = self._credentials()
                    fingerprint = hashlib.sha256(f"{shop_id}\0{api_key}".encode()).hexdigest()
                    now = int(time.time())
                    check = current.get(ACCOUNT_CACHE_KEY)
                    cached = self._cached(check, fingerprint, now)
                    if not cached:
                        check = await self._check_account(shop_id, fingerprint, now)
                        current = {**current, ACCOUNT_CACHE_KEY: check}
                        integration.credentials = encrypt_json(current)
                # Do not expose success or refresh the local cache before commit succeeds.
                self.creds = copy.deepcopy(current)
            return self._result(check, cached=cached)
        except CarrefourError as error:
            return TestResult(ok=False, detail=str(error))
        except Exception:
            return TestResult(
                ok=False, detail="Não foi possível salvar ou consultar a verificação do Carrefour."
            )

    async def test_connection(self) -> TestResult:
        if self._integration_id is not None:
            return await self._test_saved_integration()
        try:
            api_key, shop_id = self._credentials()
        except CarrefourError as error:
            return TestResult(ok=False, detail=str(error))
        fingerprint = hashlib.sha256(f"{shop_id}\0{api_key}".encode()).hexdigest()
        # Serialize clients in this process; persisted metadata survives requests/restarts.
        async with _ACCOUNT_LOCK:
            now = int(time.time())
            check = self.creds.get(ACCOUNT_CACHE_KEY)
            if not self._cached(check, fingerprint, now):
                check = _ACCOUNT_CACHE.get(fingerprint)
            cached = self._cached(check, fingerprint, now)
            if not cached:
                check = await self._check_account(shop_id, fingerprint, now)
                # Drop expired entries; never store API keys in the shared cache.
                for key, item in list(_ACCOUNT_CACHE.items()):
                    if now - item["checked_at"] >= ACCOUNT_CACHE_SECONDS:
                        del _ACCOUNT_CACHE[key]
                _ACCOUNT_CACHE[fingerprint] = copy.deepcopy(check)
            try:
                if self.creds.get(ACCOUNT_CACHE_KEY) != check:
                    await self._save_check(check)
            except CarrefourError as error:
                return TestResult(ok=False, detail=str(error))
            return self._result(check, cached=cached)

    async def _require_verified_shop(self) -> None:
        result = await self.test_connection()
        if not result.ok:
            raise CarrefourError("account_not_verified", result.detail or "Loja não verificada.")

    async def _pages(
        self, path: str, field: str, *, page_size: int, max_pages: int | None,
        filters: dict | None = None,
    ) -> AsyncIterator[dict]:
        if max_pages is not None and max_pages <= 0:
            return
        await self._require_verified_shop()
        limit = min(max(int(page_size), 1), 100)
        offset = 0
        page = 0
        seen: set[str] = set()
        id_field = "offer_id" if field == "offers" else "order_id"
        while max_pages is None or page < max_pages:
            data = await self._read_json(
                path, {**(filters or {}), "max": limit, "offset": offset},
            )
            rows = data.get(field)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise CarrefourError("invalid_page", "O Carrefour retornou uma página inválida.")
            new_rows = 0
            for row in rows:
                raw_id = row.get(id_field)
                if raw_id is None or isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
                    raise CarrefourError("invalid_item", "O Carrefour retornou um registro sem ID.")
                identity = str(raw_id).strip()
                if not identity:
                    raise CarrefourError("invalid_item", "O Carrefour retornou um registro sem ID.")
                if identity in seen:
                    continue
                seen.add(identity)
                new_rows += 1
                yield row
            offset += len(rows)
            page += 1
            total = _number(data.get("total_count"), integer=True)
            if rows and not new_rows:
                raise CarrefourError("pagination_stalled", "A paginação do Carrefour não avançou.")
            if not rows or (total is not None and offset >= total):
                # Offset counts positions, not distinct records. Overlapping pages
                # or an early empty page must not report a complete import. A
                # changing catalogue can legitimately lower total_count, so having
                # MORE distinct records than the latest total remains acceptable.
                if total is not None and len(seen) < total:
                    raise CarrefourError(
                        "incomplete_results",
                        "Consulta incompleta: faltam registros no resultado do Carrefour.",
                    )
                break
            if total is None and len(rows) < limit:
                break

    async def list_listings(
        self, *, page_size: int = 50, max_pages: int | None = None,
    ) -> AsyncIterator[dict]:
        async for offer in self._pages(
            "/offers", "offers", page_size=page_size, max_pages=max_pages,
        ):
            yield _normalize_offer(offer)

    async def list_orders(
        self, *, start_update_date: str | None = None, page_size: int = 50,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict]:
        """OR11 raw orders; callers own polling cadence and idempotent persistence."""
        filters = {}
        if start_update_date is not None:
            try:
                since = datetime.fromisoformat(start_update_date.replace("Z", "+00:00"))
                if since.tzinfo is None:
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                raise CarrefourError(
                    "invalid_date", "Informe a data de atualização com fuso horário."
                ) from None
            filters["start_update_date"] = since.astimezone(UTC).isoformat()
        async for order in self._pages(
            "/orders", "orders", page_size=page_size, max_pages=max_pages, filters=filters,
        ):
            yield order

    async def list_threads(
        self, *, updated_since: datetime | None = None, page_token: str | None = None,
        limit: int = INBOX_PAGE_MAX, with_messages: bool = True,
    ) -> dict:
        """M11, one raw page: `data`, `next_page_token`. The caller owns the cursor.

        No A01 here: shop_id is explicit, and A01's daily limit (with its cached
        negative result) would silence the chat for 24 h after one timeout.
        """
        params: dict = {"limit": min(max(int(limit), 1), INBOX_PAGE_MAX)}
        if page_token is not None:
            if not isinstance(page_token, str) or not _RE_PAGE_TOKEN.match(page_token):
                raise CarrefourError("invalid_page", "O Carrefour retornou uma página inválida.")
            params["page_token"] = page_token
        else:
            if updated_since is not None:
                if updated_since.tzinfo is None:
                    raise CarrefourError(
                        "invalid_date", "Informe a data de atualização com fuso horário."
                    )
                params["updated_since"] = (
                    updated_since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                )
            if with_messages:
                params["with_messages"] = "true"
        data = await self._read_json(INBOX_THREADS_PATH, params)
        rows = data.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise CarrefourError("invalid_page", "O Carrefour retornou uma página inválida.")
        return data

    async def reply_thread(
        self, thread_id: str, body: str, recipients: list[dict],
    ) -> httpx.Response:
        """M12: one message on an existing thread — the only remote write enabled.

        Never retried (a message to a buyer cannot be unsent). Transport errors
        propagate as httpx exceptions so the caller can tell "not sent"
        (connection refused) from "maybe sent" (timeout after the request).
        """
        if not isinstance(thread_id, str) or not _RE_THREAD_ID.match(thread_id):
            raise ValueError("invalid_thread_id")
        if not isinstance(body, str) or not (REPLY_BODY_MIN <= len(body) <= REPLY_BODY_MAX):
            raise ValueError("invalid_body_length")
        to = []
        for recipient in recipients:
            kind = recipient.get("type") if isinstance(recipient, dict) else None
            if kind not in REPLY_RECIPIENT_TYPES:
                raise ValueError("invalid_recipient")
            item = {"type": kind}
            ident = recipient.get("id")
            if isinstance(ident, str) and ident.strip():
                item["id"] = ident.strip()
            to.append(item)
        if not to:
            raise ValueError("invalid_recipient")
        api_key, shop_id = self._credentials()
        message_input = json.dumps({"body": body, "to": to}, ensure_ascii=False)
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            return await client.post(
                f"{CARREFOUR_API_BASE}{INBOX_THREADS_PATH}/{thread_id}/message",
                params={"shop_id": shop_id},
                files={"message_input": (None, message_input, "application/json")},
                headers={"Authorization": api_key, "Accept": "application/json"},
            )

    async def update_stock(
        self, link: ProductLink, qty: int, *, bling_store_id: int | None = None,
        force: bool = False,
    ) -> SyncResult:
        return SyncResult(
            status=SyncStatus.REQUIRES_REVIEW,
            error_code="carrefour_write_not_enabled",
            error_detail=(
                "Carrefour está em modo de leitura. O envio de estoque ainda não foi habilitado."
            ),
            payload={"mode": "read_only"},
        )
