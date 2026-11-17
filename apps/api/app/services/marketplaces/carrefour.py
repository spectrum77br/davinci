"""Carrefour Brasil / Mirakl: scoped reads and explicit offer import primitives.

Credentials: api_key, shop_id. The server-owned _carrefour_account_check caches
A01 for 24 hours; on_token_refresh persists this metadata, not a rotated key.
Never accept that cache from user input. See docs/carrefour-integracao.md.

Messages: M11 lists inbox threads and M12 replies on an existing thread.
STO01 and PRI01 submit independent stock/price imports and return a pending ID.
The integration queue owns activation and confirms processing plus OF22 readback.
No catalogue or full-offer replacement endpoint is used.
"""

from __future__ import annotations

import asyncio
import copy
import csv
import hashlib
import io
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

_SIMPLE_PRICE_FIELDS = frozenset({
    "channel_code", "context", "discount_end_date", "discount_start_date", "price",
    "unit_discount_price", "unit_origin_price", "volume_prices",
})
_PARTIAL_IMPORT_ID = re.compile(r"^[A-Za-z0-9-]{1,128}$")
_STOCK_STATUS_PATH = re.compile(r"^/offers/stock/imports/[A-Za-z0-9-]{1,128}/status$")
_OFFER_READ_PATH = re.compile(r"^/offers/[1-9][0-9]{0,18}$")


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


def _unsafe_offer(detail: str) -> CarrefourError:
    return CarrefourError("offer_preservation_required", detail)


def _offer_string(value: object, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (not empty and not value.strip())
        or any(ord(c) < 32 and c not in "\n\r\t" for c in value)
        or any(0xD800 <= ord(c) <= 0xDFFF for c in value)
    ):
        raise _unsafe_offer("A oferta contém um campo de texto inválido.")
    return value


def _offer_integer(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 2147483647:
        raise _unsafe_offer("A oferta contém uma quantidade ou prazo inválido.")
    return value


def _offer_money(value: object, *, requested: bool = False, zero: bool = False) -> Decimal:
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            raise ValueError
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > Decimal("999999999.99"):
            raise ValueError
        rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if (not zero and rounded <= 0) or (not requested and rounded != amount):
            raise ValueError
        return rounded
    except (ValueError, DecimalException):
        raise _unsafe_offer(
            "A oferta contém um preço inválido ou de precisão não suportada."
        ) from None


def _offer_date(value: object) -> str | None:
    if value is None:
        return None
    try:
        original = _offer_string(value)
        date = datetime.fromisoformat(original.replace("Z", "+00:00"))
        if date.utcoffset() is None:
            raise ValueError
        return original
    except (ValueError, TypeError):
        raise _unsafe_offer("A oferta contém uma data inválida ou sem fuso horário.") from None


def _simple_offer_pricing(raw: object) -> tuple[dict, Decimal, Decimal | None]:
    if not isinstance(raw, dict) or set(raw) - _SIMPLE_PRICE_FIELDS:
        raise _unsafe_offer("A oferta usa campos de preço não suportados.")
    if raw.get("channel_code") is not None or raw.get("context") not in (None, {}):
        raise _unsafe_offer("Preços por canal, cliente ou período exigem revisão antes do envio.")
    base = _offer_money(raw.get("unit_origin_price"))
    discount = (
        _offer_money(raw["unit_discount_price"])
        if raw.get("unit_discount_price") is not None
        else None
    )
    if discount is not None and discount >= base:
        raise _unsafe_offer("O desconto da oferta não pode ser preservado com segurança.")
    effective = _offer_money(raw.get("price"))
    if effective not in (base, discount):
        raise _unsafe_offer("O preço efetivo da oferta diverge dos preços informados.")
    start, end = (_offer_date(raw.get(k)) for k in ("discount_start_date", "discount_end_date"))
    if discount is None and (start is not None or end is not None):
        raise _unsafe_offer("A oferta possui datas de desconto sem preço de desconto.")
    if (
        start
        and end
        and datetime.fromisoformat(start.replace("Z", "+00:00"))
        > datetime.fromisoformat(end.replace("Z", "+00:00"))
    ):
        raise _unsafe_offer("O período de desconto da oferta é inválido.")
    volumes = raw.get("volume_prices")
    if not isinstance(volumes, list) or len(volumes) != 1:
        raise _unsafe_offer("Preços por volume exigem revisão antes do envio.")
    tier = volumes[0]
    if (
        not isinstance(tier, dict)
        or set(tier) - {"price", "quantity_threshold", "unit_discount_price", "unit_origin_price"}
        or type(tier.get("quantity_threshold")) is not int
        or tier["quantity_threshold"] != 1
        or _offer_money(tier.get("unit_origin_price")) != base
        or _offer_money(tier.get("price")) != effective
    ):
        raise _unsafe_offer("A faixa de preço da oferta não pode ser preservada com segurança.")
    tier_discount = (
        _offer_money(tier["unit_discount_price"])
        if tier.get("unit_discount_price") is not None
        else None
    )
    if tier_discount != discount:
        raise _unsafe_offer("O desconto da faixa de preço diverge do desconto da oferta.")
    result = {
        "channel_code": None,
        "unit_origin_price": float(base),
        "unit_discount_price": float(discount) if discount is not None else None,
        "discount_start_date": start,
        "discount_end_date": end,
        "volume_prices": [
            {
                "quantity_threshold": 1,
                "unit_origin_price": float(base),
                "unit_discount_price": float(discount) if discount is not None else None,
            }
        ],
    }
    return result, base, discount


def _partial_offer_sku(snapshot: dict, expected_sku: str) -> str:
    if not isinstance(snapshot, dict) or _positive_id(snapshot.get("offer_id")) is None:
        raise _unsafe_offer("A oferta não possui um identificador válido.")
    sku = _offer_string(snapshot.get("shop_sku"))
    if not expected_sku or sku != expected_sku or any(ord(c) < 32 for c in sku):
        raise CarrefourError("offer_sku_mismatch", "O SKU atual diverge do vínculo autorizado.")
    return sku


def build_stock_update(snapshot: dict, *, qty: int, expected_sku: str) -> dict:
    """STO01 updates global stock only; no offer/price fields are resubmitted."""
    sku = _partial_offer_sku(snapshot, expected_sku)
    if snapshot.get("warehouses") not in (None, []):
        raise _unsafe_offer("Estoque por depósito exige uma configuração específica.")
    if snapshot.get("fulfillment") != {"center": {"code": "DEFAULT"}}:
        raise _unsafe_offer("O centro de atendimento da oferta exige revisão.")
    _offer_integer(snapshot.get("quantity"))
    return {"shop_sku": sku, "quantity": _offer_integer(qty)}


def build_price_update(snapshot: dict, *, price, expected_sku: str) -> dict:
    """PRI01 replaces prices only. Restrict it to one default, undiscounted price."""
    sku = _partial_offer_sku(snapshot, expected_sku)
    if snapshot.get("currency_iso_code") != "BRL":
        raise _unsafe_offer("A moeda da oferta não foi confirmada como BRL.")
    if (
        snapshot.get("retail_prices") not in (None, [])
        or snapshot.get("msrp") is not None
        or snapshot.get("pricing_unit") is not None
    ):
        raise _unsafe_offer("Esta modalidade de preço exige revisão antes do envio.")
    prices = snapshot.get("all_prices")
    if not isinstance(prices, list) or len(prices) != 1:
        raise _unsafe_offer("Preços múltiplos exigem revisão antes do envio.")
    pricing, base, discount = _simple_offer_pricing(prices[0])
    if discount is not None or snapshot.get("discount") not in (None, {}):
        raise CarrefourError(
            "offer_discount_requires_review",
            "A oferta possui desconto. Revise a promoção antes de alterar o preço.",
        )
    if snapshot.get("price") is not None and _offer_money(snapshot["price"]) != base:
        raise _unsafe_offer("O preço atual diverge da tabela de preços retornada.")
    if snapshot.get("applicable_pricing") is not None:
        applicable, _, _ = _simple_offer_pricing(snapshot["applicable_pricing"])
        if applicable != pricing:
            raise _unsafe_offer("O preço aplicável diverge do preço padrão da oferta.")
    return {"shop_sku": sku, "price": float(_offer_money(price, requested=True))}


def _partial_import_id(value: object) -> str:
    if not isinstance(value, str) or not _PARTIAL_IMPORT_ID.fullmatch(value):
        raise CarrefourError("invalid_import_id", "O identificador da importação é inválido.")
    return value


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
        if (
            path not in _READ_PATHS and not _OFFER_READ_PATH.fullmatch(path)
            and not _STOCK_STATUS_PATH.fullmatch(path)
            and path != "/offers/pricing/imports"
        ):
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

    async def get_offer(self, offer_id: str | int) -> dict:
        """OF22, exactly one configured-shop offer; no A01 or broad catalogue read."""
        ident = _positive_id(offer_id)
        if ident is None:
            raise CarrefourError("invalid_offer_id", "O identificador da oferta é inválido.")
        data = await self._read_json(f"/offers/{ident}")
        if _positive_id(data.get("offer_id")) != ident:
            raise CarrefourError("offer_id_mismatch", "O Carrefour retornou outra oferta.")
        if not isinstance(data.get("shop_sku"), str) or not data["shop_sku"].strip():
            raise CarrefourError("invalid_offer", "O Carrefour retornou uma oferta sem SKU.")
        return data

    async def _submit_partial_csv(self, kind: str, items: list[dict]) -> str:
        # Official STO01/PRI01 contracts. Never fall back to replacing an offer.
        if (
            kind not in {"stock", "pricing"} or not isinstance(items, list)
            or not 1 <= len(items) <= 100
        ):
            raise CarrefourError("invalid_offer_batch", "O lote de ofertas é inválido.")
        field = "quantity" if kind == "stock" else "price"
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter=";", lineterminator="\n")
        writer.writerow(["offer-sku", field])
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict) or set(item) != {"shop_sku", field}:
                raise CarrefourError("invalid_offer_batch", "O lote contém campos não permitidos.")
            sku = _offer_string(item["shop_sku"])
            if sku in seen or any(ord(c) < 32 for c in sku):
                raise CarrefourError(
                    "invalid_offer_batch", "O lote contém SKUs inválidos ou repetidos."
                )
            seen.add(sku)
            value = (
                _offer_integer(item[field]) if kind == "stock"
                else format(_offer_money(item[field]), ".2f")
            )
            writer.writerow([sku, value])
        api_key, shop_id = self._credentials()
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            try:
                response = await client.post(
                    f"{CARREFOUR_API_BASE}/offers/{kind}/imports",
                    params={"shop_id": shop_id},
                    files={"file": (f"{kind}.csv", output.getvalue().encode("utf-8"), "text/csv")},
                    headers={"Authorization": api_key, "Accept": "application/json"},
                )
            except httpx.HTTPError:
                raise CarrefourError(
                    "submission_unknown",
                    "O resultado do envio é desconhecido. Confira a importação antes de reenviar.",
                ) from None
        status = response.status_code
        if status == 429:
            delay = _retry_after(response.headers.get("Retry-After"))
            raise CarrefourError(
                "rate_limited", f"Limite de envios do Carrefour. Aguarde {delay} segundos.",
                http_status=status, retry_after=delay,
            )
        if status >= 500 or status == 408:
            raise CarrefourError(
                "submission_unknown",
                "O resultado do envio é desconhecido. Confira antes de reenviar.",
                http_status=status,
            )
        if status != 201:
            raise CarrefourError(
                "http_error", f"O Carrefour não aceitou a importação (HTTP {status}).",
                http_status=status,
            )
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError
            # PRI01 retains the deprecated importId alias on some versions.
            return _partial_import_id(data.get("import_id") or data.get("importId"))
        except (ValueError, CarrefourError):
            raise CarrefourError(
                "submission_unknown",
                "O envio foi recebido sem identificador válido. Confira antes de reenviar.",
                http_status=status,
            ) from None

    async def submit_stock_updates(self, items: list[dict]) -> str:
        """STO01, global quantities; price/catalogue fields are never sent."""
        return await self._submit_partial_csv("stock", items)

    async def submit_price_updates(self, items: list[dict]) -> str:
        """PRI01, complete default prices validated by build_price_update."""
        return await self._submit_partial_csv("pricing", items)

    def _normalize_partial_import(
        self, data: dict, import_id: str, *, optional_counters: bool = False,
    ) -> dict:
        _, shop_id = self._credentials()
        if (
            not isinstance(data, dict) or data.get("import_id") != import_id
            or _positive_id(data.get("shop_id")) != shop_id
            or data.get("status") not in {"WAITING", "RUNNING", "COMPLETE", "FAILED"}
            or type(data.get("has_error_report")) is not bool
        ):
            raise CarrefourError(
                "invalid_import", "A importação não confirmou identidade e estado."
            )
        counters = {}
        for key in ("lines_in_error", "lines_in_success"):
            value = data.get(key)
            # STO02 documents these counters as optional, even at completion.
            # Preserve unknown terminal counts so the outbox can request review;
            # never fabricate successful lines or keep polling a completed import.
            if key not in data and optional_counters:
                counters[key] = 0 if data["status"] in {"WAITING", "RUNNING"} else None
                continue
            if type(value) is not int or value < 0:
                raise CarrefourError("invalid_import", "A importação retornou contagens inválidas.")
            counters[key] = value
        if data.get("offers_in_error") is not None:
            errors = data["offers_in_error"]
            if type(errors) is not int or errors < 0:
                raise CarrefourError("invalid_import", "A importação retornou contagens inválidas.")
            if counters["lines_in_error"] is not None:
                counters["lines_in_error"] = max(counters["lines_in_error"], errors)
        return {
            **data, **counters, "lines_in_pending": 0,
        }

    async def get_stock_import(self, import_id: str) -> dict:
        """STO02 is a single status object, scoped to the configured shop."""
        ident = _partial_import_id(import_id)
        data = await self._read_json(f"/offers/stock/imports/{ident}/status")
        return self._normalize_partial_import(data, ident, optional_counters=True)

    async def get_price_import(self, import_id: str) -> dict:
        """PRI02 filters its list by import ID; it is not /{id}/status."""
        ident = _partial_import_id(import_id)
        response = await self._read_json("/offers/pricing/imports", {"import_id": ident})
        rows = response.get("data")
        if not isinstance(rows, list) or len(rows) != 1:
            raise CarrefourError(
                "invalid_import", "A consulta não identificou uma única importação."
            )
        return self._normalize_partial_import(rows[0], ident)

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
        if self._integration_id is not None:
            from app.services.carrefour_offer_write import enqueue_offer_write

            return await enqueue_offer_write(
                integration_id=self._integration_id, link=link, qty=qty,
            )
        return SyncResult(
            status=SyncStatus.REQUIRES_REVIEW,
            error_code="carrefour_write_not_enabled",
            error_detail=(
                "Carrefour está em modo de leitura. O envio de estoque ainda não foi habilitado."
            ),
            payload={"mode": "read_only"},
        )

    async def update_price(self, link: ProductLink, price) -> SyncResult:
        if self._integration_id is not None:
            from app.services.carrefour_offer_write import enqueue_offer_write

            return await enqueue_offer_write(
                integration_id=self._integration_id, link=link, price=price,
            )
        return SyncResult(
            status=SyncStatus.REQUIRES_REVIEW,
            error_code="carrefour_write_not_enabled",
            error_detail="O envio de preços precisa de uma integração Carrefour habilitada.",
            payload={"mode": "read_only"},
        )
