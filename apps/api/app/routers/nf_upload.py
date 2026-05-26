"""Upload de NF-e XML → envio automático ao Mercado Livre.

Migrated from the standalone xml-up container so the DaVinci API is the
single source of truth. Flow:

  1. Receive XML(s) via multipart upload.
  2. Extract the ML order_id from infCpl (16+ digits after "Fonte IBPT.").
  3. For each operator-selected ML store: resolve shipping_id, POST the
     raw XML to /shipments/{shipping_id}/invoice_data. Stop as soon as
     one store accepts (the order belongs to exactly one).

Notes:
  * Decrypts ML credentials via `app.security.cipher.decrypt_json`
    (the cipher used everywhere else in DaVinci). NO ad-hoc AESGCM.
  * IntegrationPlatform.ML — `MERCADOLIVRE` is not the enum value here.
  * No DB writes — this router is a thin proxy. Audit trail lives in
    logger.info events (`nf_upload_*`).
"""
from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from typing import Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import Integration, IntegrationPlatform, User
from app.security.cipher import decrypt_json

logger = structlog.get_logger()
router = APIRouter(prefix="/api/nf", tags=["notas_fiscais"])

_MAX_BYTES = 16 * 1024 * 1024  # 16 MiB per file
_ML_BASE = "https://api.mercadolibre.com"
_INF_CPL_TAG_SUFFIX = "infCpl"
_ORDER_RE_PRIMARY = re.compile(r"Fonte IBPT\.?\s*(\d{16,})")
_ORDER_RE_FALLBACK = re.compile(r"(\d{16,})")


# ─── XML parsing ──────────────────────────────────────────────────────


def _find_inf_cpl(root: ET.Element) -> str | None:
    """Recursively find the <infCpl> text — namespace-agnostic so we
    don't have to enumerate Bling's XML emissions."""
    if root.tag.endswith(_INF_CPL_TAG_SUFFIX):
        return root.text
    for child in root:
        found = _find_inf_cpl(child)
        if found:
            return found
    return None


def _extract_order_number(xml_content: str) -> str | None:
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None
    inf_cpl = _find_inf_cpl(root)
    if not inf_cpl:
        return None
    m = _ORDER_RE_PRIMARY.search(inf_cpl)
    if m:
        return m.group(1)
    m = _ORDER_RE_FALLBACK.search(inf_cpl)
    return m.group(1) if m else None


def _validate_xml(xml_content: str) -> bool:
    try:
        ET.fromstring(xml_content)
        return True
    except ET.ParseError:
        return False


# ─── ML credentials ───────────────────────────────────────────────────


async def _list_ml_stores(session: AsyncSession) -> list[dict[str, str]]:
    """Returns [{name, access_token, integration_id}] for every active
    ML integration. Bad-blob rows are skipped with a warning rather than
    breaking the whole list."""
    integrations = (
        await session.execute(
            select(Integration)
            .where(
                Integration.platform == IntegrationPlatform.ML,
                Integration.status == "active",
            )
            .order_by(Integration.name)
        )
    ).scalars().all()

    out: list[dict[str, str]] = []
    for integ in integrations:
        try:
            creds = decrypt_json(bytes(integ.credentials))
            at = creds.get("access_token") or creds.get("at")
            if not at:
                continue
            out.append({
                "name": integ.name or "",
                "access_token": str(at),
                "integration_id": str(integ.id),
            })
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "nf_decrypt_failed",
                integration=getattr(integ, "name", "?"),
                err=str(e)[:120],
            )
    return out


# ─── ML API ───────────────────────────────────────────────────────────


async def _resolve_shipping_id(
    access_token: str, order_id: str,
) -> str | None:
    """Tries /orders/{id} first, falls back to /packs/{id}. Returns
    the shipping_id from whichever endpoint matched, or None if
    neither does (the order doesn't belong to this store)."""
    headers = {
        "Authorization": f"Bearer {access_token.strip()}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.get(f"{_ML_BASE}/orders/{order_id}", headers=headers)
            if r.status_code == 200:
                sid = (((r.json() or {}).get("shipping") or {}) or {}).get("id")
                if sid:
                    return str(sid)
        except Exception:  # noqa: BLE001
            pass
        try:
            r = await c.get(f"{_ML_BASE}/packs/{order_id}", headers=headers)
            if r.status_code == 200:
                sid = (((r.json() or {}).get("shipment") or {}) or {}).get("id")
                if sid:
                    return str(sid)
        except Exception:  # noqa: BLE001
            pass
    return None


async def _post_invoice(
    access_token: str, shipping_id: str, xml_bytes: bytes,
) -> tuple[bool, str]:
    """POST /shipments/{shipping_id}/invoice_data?siteId=MLB with raw XML.
    Returns (success, error_str)."""
    url = f"{_ML_BASE}/shipments/{shipping_id}/invoice_data"
    headers = {
        "Authorization": f"Bearer {access_token.strip()}",
        "Content-Type": "application/xml",
    }
    params = {"siteId": "MLB"}
    async with httpx.AsyncClient(timeout=30) as c:
        try:
            r = await c.post(url, params=params, headers=headers, content=xml_bytes)
        except Exception as e:  # noqa: BLE001
            return False, f"conexao: {str(e)[:200]}"
    if r.status_code in (200, 201):
        return True, ""
    if r.status_code == 401:
        return False, "token expirado/invalido"
    if r.status_code == 404:
        return False, f"shipping {shipping_id} nao encontrado"
    if r.status_code == 400:
        return False, f"dados invalidos: {r.text[:200]}"
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


async def _send_to_first_matching_store(
    order_id: str,
    xml_bytes: bytes,
    stores: list[dict[str, str]],
) -> dict[str, Any]:
    """Walks the store list looking for the one that owns the order.
    Returns the per-attempt log + the winning store (if any)."""
    attempts: list[dict[str, Any]] = []
    for store in stores:
        name = store["name"]
        at = store["access_token"]
        shipping_id = await _resolve_shipping_id(at, order_id)
        if not shipping_id:
            attempts.append({
                "store": name, "success": False,
                "error": "pedido nao encontrado nesta loja",
                "shipping_id": None,
            })
            continue

        # Up to 3 attempts with exponential backoff — ML occasionally
        # 5xx's the invoice_data endpoint under load.
        ok, err = False, ""
        for n in range(3):
            if n > 0:
                await asyncio.sleep(2 ** n)
            ok, err = await _post_invoice(at, shipping_id, xml_bytes)
            if ok:
                break
        attempts.append({
            "store": name, "success": ok, "error": None if ok else err,
            "shipping_id": shipping_id,
        })
        if ok:
            return {
                "success": True, "order_id": order_id, "shipping_id": shipping_id,
                "store_name": name, "attempts_details": attempts,
            }
    return {
        "success": False, "order_id": order_id,
        "error": f"falha em {len(attempts)} tentativa(s)",
        "attempts_details": attempts,
    }


# ─── Routes ───────────────────────────────────────────────────────────


@router.get("/stores")
async def list_stores(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
) -> dict[str, Any]:
    """Names of active ML integrations available for NF upload."""
    stores = await _list_ml_stores(session)
    return {"stores": [s["name"] for s in stores]}


@router.post("/upload")
async def upload_single(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("controle_estoque", "edit"))],
    file: Annotated[UploadFile, File(...)],
    selected_stores: Annotated[list[str], Form()] = [],
) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".xml"):
        raise HTTPException(400, detail={"code": "not_xml"})
    raw = await file.read()
    if len(raw) > _MAX_BYTES:
        raise HTTPException(413, detail={"code": "file_too_large"})
    try:
        xml_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"success": False, "error": "XML nao e UTF-8"}

    if not _validate_xml(xml_text):
        return {"success": False, "error": "XML invalido ou corrompido"}
    order_id = _extract_order_number(xml_text)
    if not order_id:
        return {
            "success": False, "order_id": None,
            "error": "numero do pedido nao encontrado no infCpl",
            "attempts_details": [],
        }
    if not selected_stores:
        return {"success": False, "error": "nenhuma loja selecionada", "order_id": order_id}

    all_stores = await _list_ml_stores(session)
    to_try = [s for s in all_stores if s["name"] in selected_stores]
    if not to_try:
        return {
            "success": False, "order_id": order_id,
            "error": "nenhuma loja correspondente ativa",
            "attempts_details": [],
        }
    result = await _send_to_first_matching_store(order_id, raw, to_try)
    logger.info(
        "nf_upload_single",
        order_id=order_id, ok=result.get("success"),
        winning_store=result.get("store_name"),
        attempts=len(result.get("attempts_details") or []),
    )
    return result


@router.post("/upload-multiple")
async def upload_multiple(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("controle_estoque", "edit"))],
    files: Annotated[list[UploadFile], File(...)],
    selected_stores: Annotated[list[str], Form()] = [],
) -> dict[str, Any]:
    """Sequential — one ML call at a time to stay polite under the token's
    rate budget. UI shows per-file progress by polling this single
    response after it returns (or, in the future, via a streamed
    progress endpoint)."""
    if not files:
        raise HTTPException(400, detail={"code": "no_files"})
    if not selected_stores:
        return {"success": False, "error": "nenhuma loja selecionada"}

    all_stores = await _list_ml_stores(session)
    to_try = [s for s in all_stores if s["name"] in selected_stores]

    results: list[dict[str, Any]] = []
    for f in files:
        fname = f.filename or "(sem nome)"
        if not fname.lower().endswith(".xml"):
            results.append({"filename": fname, "success": False, "error": "nao e .xml"})
            continue
        raw = await f.read()
        if len(raw) > _MAX_BYTES:
            results.append({"filename": fname, "success": False, "error": "arquivo > 16 MiB"})
            continue
        try:
            xml_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            results.append({"filename": fname, "success": False, "error": "XML nao e UTF-8"})
            continue
        if not _validate_xml(xml_text):
            results.append({"filename": fname, "success": False, "error": "XML invalido"})
            continue
        order_id = _extract_order_number(xml_text)
        if not order_id:
            results.append({
                "filename": fname, "success": False,
                "error": "numero do pedido nao encontrado no infCpl",
                "order_id": None,
            })
            continue
        r = await _send_to_first_matching_store(order_id, raw, to_try)
        results.append({"filename": fname, **r})

    total = len(results)
    ok_n = sum(1 for r in results if r.get("success"))
    return {
        "success": ok_n > 0,
        "total_files": total,
        "successful_files": ok_n,
        "failed_files": total - ok_n,
        "results": results,
    }
