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
  * Equipe de vendas (30/10/2026): quem é de uma equipe (101 Londres, 102
    Israel, 103 Marrocos) só vê e só usa as lojas ML da equipe dele — a
    lista vem cercada e o envio recusa loja de fora (403
    `loja_de_outra_equipe`). Admin e quem não tem equipe: como sempre.
"""
from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from typing import Annotated, Any
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.deps.team_scope import nome_da_equipe, resolve_team_scope
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


async def _list_ml_stores(
    session: AsyncSession, integration_ids: set[UUID] | None = None,
) -> list[dict[str, str]]:
    """Returns [{name, access_token, integration_id}] for every active
    ML integration. Bad-blob rows are skipped with a warning rather than
    breaking the whole list.

    `integration_ids` is the sales-team fence (`_integracoes_da_equipe`):
    None = no fence (admin / no team); an empty set = a team with no store,
    which gets NO store at all (never "all of them")."""
    stmt = (
        select(Integration)
        .where(
            Integration.platform == IntegrationPlatform.ML,
            Integration.status == "active",
        )
        .order_by(Integration.name)
    )
    if integration_ids is not None:
        stmt = stmt.where(
            Integration.id.in_(list(integration_ids)) if integration_ids else false()
        )
    integrations = (await session.execute(stmt)).scalars().all()

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


# ─── Equipe de vendas ─────────────────────────────────────────────────
# Eduardo, 30/10/2026: 101 Londres, 102 Israel, 103 Marrocos — cada equipe
# só enxerga as lojas dela. Aqui a cerca é a integração ML das lojas da
# equipe (`TeamScope.integration_ids`, a mesma régua da tela Integrações):
# a lista de lojas alvo só traz as da equipe e o envio recusa loja de fora,
# então o XML nunca sai pelo token de loja de outra equipe. Admin e quem não
# tem equipe (o pessoal do galpão) seguem com todas as lojas ML.


async def _integracoes_da_equipe(session: AsyncSession, user: User) -> set[UUID] | None:
    """Integrações que o chamador pode usar. None = sem cerca de equipe.
    Vazio = equipe sem loja cadastrada: nenhuma loja (nunca "todas")."""
    scope = await resolve_team_scope(session, user)
    if scope.unrestricted:
        return None
    return set(scope.integration_ids)


def _equipe_out(user: User, ids: set[UUID] | None) -> dict[str, Any] | None:
    """Pra tela avisar que a lista de lojas está cercada pela equipe (None =
    não está). Mesmo formato do `equipe` de /api/estoque/pedidos."""
    if ids is None:
        return None
    numeros: set[int] = set()
    for t in user.sales_teams or []:
        try:
            numeros.add(int(t))
        except (TypeError, ValueError):
            continue
    return {
        "numeros": sorted(numeros),
        "nomes": [nome_da_equipe(n) for n in sorted(numeros)],
    }


async def _exigir_lojas_da_equipe(
    session: AsyncSession, user: User, selected_stores: list[str],
) -> set[UUID] | None:
    """Cerca de equipe do envio. Devolve as integrações que o chamador pode
    usar (None = sem cerca) e dá 403 `loja_de_outra_equipe` se alguma loja
    escolhida não é uma loja ML da equipe — inclusive nome que nem existe:
    não dá pra provar que é da equipe (a régua do `pedido_de_outra_equipe`
    do Controle de Estoque). Loja da equipe só inativa ou sem token não é
    "de outra equipe": passa aqui e fica de fora das tentativas, como sempre.
    Roda ANTES de ler o XML e de chamar o ML."""
    ids = await _integracoes_da_equipe(session, user)
    if ids is None:
        return None
    nomes: set[str] = set()
    if ids:
        nomes = set(
            (
                await session.execute(
                    select(Integration.name).where(
                        Integration.platform == IntegrationPlatform.ML,
                        Integration.id.in_(list(ids)),
                    )
                )
            ).scalars().all()
        )
    fora = list(dict.fromkeys(s for s in selected_stores if s not in nomes))
    if fora:
        logger.info("nf_upload_loja_de_outra_equipe", user_id=str(user.id), lojas=fora)
        raise HTTPException(403, detail={"code": "loja_de_outra_equipe", "lojas": fora})
    return ids


# ─── ML API ───────────────────────────────────────────────────────────


_ERR_OUTRA_LOJA = "pedido nao encontrado nesta loja"
_ERR_SEM_ENVIO = (
    "pedido encontrado, mas o ML ainda nao liberou o envio "
    "(buffering / data de envio adiada) — reenviar depois da liberacao"
)
_ERR_TOKEN = "token expirado/invalido"


async def _resolve_shipping_id(
    access_token: str, order_id: str,
) -> tuple[str | None, str]:
    """Tries /orders/{id} first, falls back to /packs/{id}.

    Returns (shipping_id, motivo). `motivo` is "" on success and otherwise
    says WHY there is no shipping_id — a 200 without `shipping.id` means the
    store DOES own the order but the ML shipment isn't released yet
    (buffering), which is very different from the order living elsewhere.
    """
    headers = {
        "Authorization": f"Bearer {access_token.strip()}",
        "Accept": "application/json",
    }
    dono = False
    token_ruim = False
    async with httpx.AsyncClient(timeout=15) as c:
        for path, chave in (("orders", "shipping"), ("packs", "shipment")):
            try:
                r = await c.get(f"{_ML_BASE}/{path}/{order_id}", headers=headers)
            except Exception:  # noqa: BLE001
                continue
            if r.status_code == 401:
                token_ruim = True
                continue
            if r.status_code != 200:
                continue
            dono = True
            sid = (((r.json() or {}).get(chave) or {}) or {}).get("id")
            if sid:
                return str(sid), ""
    if dono:
        return None, _ERR_SEM_ENVIO
    if token_ruim:
        return None, _ERR_TOKEN
    return None, _ERR_OUTRA_LOJA


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


def _resumo_falha(attempts: list[dict[str, Any]]) -> str:
    """Summary line for the UI — says WHY it failed, not just how many
    stores were tried. The most informative attempt wins: a store that
    owns the order beats one that simply doesn't have it."""
    com_envio = [a for a in attempts if a.get("shipping_id")]
    if com_envio:
        a = com_envio[0]
        return f"{a['store']}: {a.get('error') or 'falha ao enviar o XML'}"
    for erro in (_ERR_SEM_ENVIO, _ERR_TOKEN):
        a = next((x for x in attempts if x.get("error") == erro), None)
        if a:
            return f"{a['store']}: {erro}"
    outros = [
        a for a in attempts
        if a.get("error") not in (None, "", _ERR_OUTRA_LOJA)
    ]
    if outros:
        return f"{outros[0]['store']}: {outros[0]['error']}"
    return (
        f"pedido nao encontrado em nenhuma das {len(attempts)} "
        "loja(s) selecionada(s)"
    )


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
        shipping_id, motivo = await _resolve_shipping_id(at, order_id)
        if not shipping_id:
            attempts.append({
                "store": name, "success": False,
                "error": motivo,
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
        "error": _resumo_falha(attempts),
        "attempts_details": attempts,
    }


# ─── Routes ───────────────────────────────────────────────────────────


@router.get("/stores")
async def list_stores(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
) -> dict[str, Any]:
    """Names of active ML integrations available for NF upload — for a
    sales-team user, only the team's stores (`equipe` says which team;
    None = no fence)."""
    ids = await _integracoes_da_equipe(session, user)
    stores = await _list_ml_stores(session, ids)
    return {"stores": [s["name"] for s in stores], "equipe": _equipe_out(user, ids)}


@router.post("/upload")
async def upload_single(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "edit"))],
    file: Annotated[UploadFile, File(...)],
    selected_stores: Annotated[list[str], Form()] = [],
) -> dict[str, Any]:
    ids = await _exigir_lojas_da_equipe(session, user, selected_stores)
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

    all_stores = await _list_ml_stores(session, ids)
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
    user: Annotated[User, Depends(require_permission("controle_estoque", "edit"))],
    files: Annotated[list[UploadFile], File(...)],
    selected_stores: Annotated[list[str], Form()] = [],
) -> dict[str, Any]:
    """Sequential — one ML call at a time to stay polite under the token's
    rate budget. UI shows per-file progress by polling this single
    response after it returns (or, in the future, via a streamed
    progress endpoint). A store outside the caller's sales team refuses
    the whole batch (403 `loja_de_outra_equipe`) before any file is read."""
    ids = await _exigir_lojas_da_equipe(session, user, selected_stores)
    if not files:
        raise HTTPException(400, detail={"code": "no_files"})
    if not selected_stores:
        return {"success": False, "error": "nenhuma loja selecionada"}

    all_stores = await _list_ml_stores(session, ids)
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
