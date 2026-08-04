"""Fotos de produtos via MEGA — endpoints da Tabela de Preços (aba Produtos).

Fluxo do operador:
  * GET  /status  → sidecar no ar? conta logada?
  * POST /login   → login na conta MEGA (senha vai direto pro sidecar e não
                    é armazenada; a sessão persiste no volume do container).
  * POST /sync    → casa pastas de fotos ↔ produtos pelo nome (2 níveis:
                    marca/modelo); dry_run devolve a prévia, apply gera
                    link público (mega-export) e grava fotos_url/fotos_path.
  * POST /scaffold → cria a estrutura marca/modelo no MEGA a partir de uma
                    lista de nomes e já preenche os links dos produtos.
  * POST /counts/refresh → reconta fotos/vídeos por pasta (mega-find no
                    sidecar) e grava fotos_count/videos_count nos produtos.
  * POST /products/{id}/fotos/upload → sobe fotos/vídeos pro MEGA na pasta
                    do produto (cria se não existir), salva o link e atualiza
                    a contagem de todos os produtos que dividem a pasta.
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
            "GET", "/folders", params={"root": root, "depth": 2}, timeout=600.0
        )
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc

    # Containers de marca (pastas com subpastas, ex.: /Uranyx) ficam fora do
    # matching — os modelos são as subpastas deles.
    folder_items = [
        f for f in listing.get("items", []) if not f.get("has_children")
    ]

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
    rep = match_products_to_folders(list(products), folder_items)

    applied = 0
    errors: list[str] = []
    if not body.dry_run:
        url_by_path: dict[str, str] = {}
        for p, f in rep["matches"]:
            if body.only_missing and p.fotos_url:
                # Não mexe no link, mas corrige o destino de upload se a
                # pasta mudou de lugar (o link público sobrevive a moves).
                if p.fotos_path != f["path"]:
                    p.fotos_path = f["path"]
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
        "unmatched_folders": [
            f.get("path", f["name"]) for f in rep["unmatched_folders"][:500]
        ],
        "errors": errors[:50],
    }


class MegaCountsIn(BaseModel):
    root: str | None = None


@router.post("/counts/refresh")
async def mega_counts_refresh(
    body: MegaCountsIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> dict[str, Any]:
    """Reconta as mídias de cada pasta-folha e espelha nos produtos que
    apontam pra ela (mesma pasta ⇒ mesma contagem em todas as linhas)."""
    root = (body.root or _root()).strip() or "/"
    try:
        listing = await sidecar_request(
            "GET",
            "/folders",
            params={"root": root, "depth": 2, "media_counts": 1},
            timeout=1800.0,
        )
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc

    counts_by_path: dict[str, tuple[int, int]] = {}
    for f in listing.get("items", []):
        if f.get("has_children") or not f.get("is_folder"):
            continue
        if f.get("fotos") is None:
            continue
        counts_by_path[f["path"]] = (int(f["fotos"]), int(f.get("videos") or 0))

    products = (
        (
            await session.execute(
                select(PricingProduct).where(
                    user_scope(PricingProduct, user),
                    PricingProduct.fotos_path.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    updated = 0
    for p in products:
        c = counts_by_path.get(p.fotos_path or "")
        if c is None:
            continue
        if (p.fotos_count, p.videos_count) != c:
            p.fotos_count, p.videos_count = c
            updated += 1
    await session.commit()
    logger.info(
        "mega_fotos_counts_refresh",
        folders=len(counts_by_path),
        updated=updated,
        user_id=str(user.id),
    )
    return {
        "root": root,
        "folders_counted": len(counts_by_path),
        "products_updated": updated,
        "products_with_folder": len(products),
    }


class MegaScaffoldIn(BaseModel):
    # Cria <root>/<brand>/<name> pra cada nome (uma pasta por modelo) e já
    # preenche fotos_url/fotos_path dos produtos que casarem pelo nome —
    # links de pasta valem mesmo vazias, as fotos entram depois.
    brand: str
    names: list[str]
    only_missing: bool = True


@router.post("/scaffold")
async def mega_scaffold(
    body: MegaScaffoldIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> dict[str, Any]:
    brand = body.brand.strip().strip("/").replace("/", "-")
    if not brand:
        raise HTTPException(400, detail={"code": "brand_required"})
    seen: set[str] = set()
    names: list[str] = []
    for raw in body.names:
        n = (raw or "").strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            names.append(n)
    if not names:
        raise HTTPException(400, detail={"code": "names_required"})
    if len(names) > 300:
        raise HTTPException(400, detail={"code": "too_many_folders"})

    brand_path = f"{_root().rstrip('/')}/{brand}"
    try:
        res = await sidecar_request(
            "POST",
            "/scaffold",
            json={"root": brand_path, "names": names},
            timeout=1800.0,
        )
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc
    results = res.get("results", [])
    created = [r for r in results if r.get("url")]
    errors = [f"{r['name']}: {r['error']}" for r in results if r.get("error")]

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
    folder_items = [
        {"name": r["name"], "path": r["path"], "is_folder": True}
        for r in created
    ]
    rep = match_products_to_folders(list(products), folder_items)
    url_by_path = {r["path"]: r["url"] for r in created}
    applied = 0
    for p, f in rep["matches"]:
        url = url_by_path.get(f["path"])
        if not url:
            continue
        if body.only_missing and p.fotos_url:
            if p.fotos_path != f["path"]:
                p.fotos_path = f["path"]
            continue
        p.fotos_url = url
        p.fotos_path = f["path"]
        applied += 1
    await session.commit()
    logger.info(
        "mega_fotos_scaffold",
        brand_path=brand_path,
        folders=len(created),
        applied=applied,
        errors=len(errors),
        user_id=str(user.id),
    )
    return {
        "brand_path": brand_path,
        "folders_created": len(created),
        "applied": applied,
        "matched": [
            {"sku": p.sku, "name": p.name, "folder": f["name"]}
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
        "unmatched_folders": [
            f.get("path", f["name"]) for f in rep["unmatched_folders"][:500]
        ],
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

    fotos_count = videos_count = None
    try:
        cnt = await sidecar_request(
            "GET", "/media_counts", params={"path": dest}, timeout=300.0
        )
        fotos_count = int(cnt["fotos"])
        videos_count = int(cnt["videos"])
    except (MegaError, KeyError, TypeError, ValueError):
        pass  # contagem é cosmética; o upload em si já deu certo
    if fotos_count is not None:
        siblings = (
            (
                await session.execute(
                    select(PricingProduct).where(
                        PricingProduct.fotos_path == dest
                    )
                )
            )
            .scalars()
            .all()
        )
        for sib in siblings:
            sib.fotos_count = fotos_count
            sib.videos_count = videos_count
        row.fotos_count = fotos_count
        row.videos_count = videos_count

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
        "fotos_count": fotos_count,
        "videos_count": videos_count,
    }
