"""Fotos de produtos via MEGA — endpoints da Tabela de Preços (aba Produtos).

Fluxo do operador:
  * GET  /status  → sidecar no ar? conta logada?
  * POST /login   → login na conta MEGA (senha vai direto pro sidecar e não
                    é armazenada; a sessão persiste no volume do container).
  * POST /sync    → casa pastas de fotos ↔ produtos pelo nome; dry_run
                    devolve a prévia, apply gera link público (mega-export)
                    e grava fotos_url/fotos_path.
  * POST /products/{id}/fotos/upload → sobe fotos pro MEGA na pasta do
                    produto (cria se não existir) e salva o link.
"""
from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission, user_scope
from app.models import PricingProduct, User
from app.services.mega_fotos import (
    MegaError,
    match_products_to_folders,
    sidecar_request,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/pricing/mega", tags=["pricing"])


def _root() -> str:
    return get_settings().mega_fotos_root.strip() or "/"


def _sidecar_http_error(exc: MegaError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": "mega_sidecar", "message": exc.message},
    )


@router.get("/status")
async def mega_status(
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "view"))
    ],
) -> dict[str, Any]:
    try:
        health = await sidecar_request("GET", "/health", timeout=20.0)
    except MegaError as exc:
        return {
            "available": False,
            "logged_in": False,
            "email": None,
            "error": exc.message,
        }
    return {
        "available": True,
        "logged_in": bool(health.get("logged_in")),
        "email": health.get("email"),
        "root": _root(),
    }


class MegaLoginIn(BaseModel):
    email: str
    password: str
    code: str | None = None


@router.post("/login")
async def mega_login(
    body: MegaLoginIn,
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> dict[str, Any]:
    try:
        res = await sidecar_request(
            "POST", "/login", json=body.model_dump(), timeout=180.0
        )
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc
    logger.info("mega_login", ok=bool(res.get("ok")), user_id=str(user.id))
    return res


class MegaSyncIn(BaseModel):
    dry_run: bool = True
    # only_missing: não sobrescreve fotos_url já preenchido (ex.: link
    # colado à mão) — só completa os produtos sem link.
    only_missing: bool = True
    root: str | None = None


@router.post("/sync")
async def mega_sync(
    body: MegaSyncIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> dict[str, Any]:
    root = (body.root or _root()).strip() or "/"
    try:
        listing = await sidecar_request(
            "GET", "/folders", params={"root": root}, timeout=300.0
        )
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc

    products = (
        (
            await session.execute(
                select(PricingProduct)
                .where(user_scope(PricingProduct, user))
                .order_by(PricingProduct.sku)
            )
        )
        .scalars()
        .all()
    )
    rep = match_products_to_folders(list(products), listing.get("items", []))

    applied = 0
    errors: list[str] = []
    if not body.dry_run:
        url_by_path: dict[str, str] = {}
        for p, f in rep["matches"]:
            if body.only_missing and p.fotos_url:
                continue
            path = f["path"]
            url = url_by_path.get(path)
            if url is None:
                try:
                    exp = await sidecar_request(
                        "POST", "/export", json={"path": path}, timeout=120.0
                    )
                    url = str(exp["url"])
                    url_by_path[path] = url
                except MegaError as exc:
                    errors.append(f"{f['name']}: {exc.message}")
                    continue
            p.fotos_url = url
            p.fotos_path = path
            applied += 1
        await session.commit()
        logger.info(
            "mega_fotos_sync",
            applied=applied,
            matched=len(rep["matches"]),
            errors=len(errors),
            user_id=str(user.id),
        )

    to_apply = sum(
        1
        for p, _f in rep["matches"]
        if not (body.only_missing and p.fotos_url)
    )
    return {
        "dry_run": body.dry_run,
        "root": root,
        "folders_total": rep["folders_total"],
        "matched_total": len(rep["matches"]),
        "to_apply": to_apply,
        "applied": applied,
        "matched": [
            {
                "sku": p.sku,
                "name": p.name,
                "folder": f["name"],
                "has_url": bool(p.fotos_url),
            }
            for p, f in rep["matches"][:800]
        ],
        "ambiguous": [
            {
                "sku": a["product"].sku,
                "name": a["product"].name,
                "candidates": a["candidates"][:6],
            }
            for a in rep["ambiguous"][:200]
        ],
        "unmatched_products": [
            {"sku": p.sku, "name": p.name}
            for p in rep["unmatched_products"][:500]
        ],
        "unmatched_folders": [f["name"] for f in rep["unmatched_folders"][:500]],
        "errors": errors[:50],
    }


@router.post("/products/{product_id}/fotos/upload")
async def upload_product_fotos(
    product_id: UUID,
    files: Annotated[list[UploadFile], File(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(PricingProduct).where(
                PricingProduct.id == product_id,
                user_scope(PricingProduct, user),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "not_found"})
    if not files:
        raise HTTPException(400, detail={"code": "no_files"})
    if len(files) > 60:
        raise HTTPException(400, detail={"code": "too_many_files"})

    folder_name = (row.name or row.sku).strip().replace("/", "-")
    dest = row.fotos_path or f"{_root().rstrip('/')}/{folder_name}"
    files_payload = [
        (
            "files",
            (
                f.filename or "foto.jpg",
                f.file,
                f.content_type or "application/octet-stream",
            ),
        )
        for f in files
    ]
    try:
        up = await sidecar_request(
            "POST",
            "/upload",
            data={"dest": dest},
            files=files_payload,
            timeout=3600.0,
        )
        exp = await sidecar_request(
            "POST", "/export", json={"path": dest}, timeout=120.0
        )
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc

    row.fotos_path = dest
    row.fotos_url = str(exp["url"])
    await session.commit()
    logger.info(
        "mega_fotos_upload",
        sku=row.sku,
        uploaded=up.get("uploaded"),
        dest=dest,
        user_id=str(user.id),
    )
    return {
        "fotos_url": row.fotos_url,
        "fotos_path": dest,
        "uploaded": up.get("uploaded", len(files)),
    }
