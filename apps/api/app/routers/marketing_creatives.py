"""Aba Criativos do Marketing — briefing de imagens/vídeos.

Fluxo: admin (ou quem tiver edit) cadastra as linhas (modelo/marca/sku/
roteiro); o criador de conteúdo anexa o arquivo; o admin aprova (V) ou
reprova (X). Ao aprovar, o arquivo sobe pra pasta do produto no MEGA —
o produto é achado pelo SKU na tabela de preços (aba Produtos) e o
destino é o fotos_path dele. pushed_at/pushed_dest registram o envio.

Permissões: recurso "marketing_criativos" (view/edit); aprovação é
sempre admin. Independente do recurso "marketing" (dashboards de Ads).
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_admin, require_permission
from app.models import MarketingCreative, PricingProduct, User, UserRole
from app.services.mega_fotos import MegaError, sidecar_request

logger = structlog.get_logger()
router = APIRouter(prefix="/api/marketing/creatives", tags=["marketing"])


def _row_out(row: MarketingCreative) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "modelo": row.modelo,
        "marca": row.marca,
        "sku": row.sku,
        "roteiro": row.roteiro,
        "file_name": row.file_name,
        "file_mime": row.file_mime,
        "file_size": row.file_size,
        "aprovado": row.aprovado,
        "pushed_at": row.pushed_at.isoformat() if row.pushed_at else None,
        "pushed_dest": row.pushed_dest,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _get_row(session: AsyncSession, creative_id: UUID) -> MarketingCreative:
    row = (
        await session.execute(
            select(MarketingCreative).where(MarketingCreative.id == creative_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "not_found"})
    return row


def _file_dir(row: MarketingCreative) -> Path:
    return Path(get_settings().uploads_dir) / "creatives" / str(row.id)


@router.get("")
async def list_creatives(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "view"))],
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(MarketingCreative).order_by(MarketingCreative.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_row_out(r) for r in rows]


class CreativeIn(BaseModel):
    modelo: str
    marca: str | None = None
    sku: str | None = None
    roteiro: str | None = None


@router.post("")
async def create_creative(
    payload: CreativeIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "edit"))],
) -> dict[str, Any]:
    modelo = payload.modelo.strip()
    if not modelo:
        raise HTTPException(400, detail={"code": "modelo_obrigatorio"})
    row = MarketingCreative(
        id=uuid4(),
        modelo=modelo,
        marca=(payload.marca or "").strip() or None,
        sku=(payload.sku or "").strip() or None,
        roteiro=payload.roteiro,
        created_by=user.id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _row_out(row)


class CreativePatch(BaseModel):
    modelo: str | None = None
    marca: str | None = None
    sku: str | None = None
    roteiro: str | None = None


@router.patch("/{creative_id}")
async def patch_creative(
    creative_id: UUID,
    payload: CreativePatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "edit"))],
) -> dict[str, Any]:
    row = await _get_row(session, creative_id)
    data = payload.model_dump(exclude_unset=True)
    if "modelo" in data:
        modelo = (data["modelo"] or "").strip()
        if not modelo:
            raise HTTPException(400, detail={"code": "modelo_obrigatorio"})
        row.modelo = modelo
    if "marca" in data:
        row.marca = (data["marca"] or "").strip() or None
    if "sku" in data:
        row.sku = (data["sku"] or "").strip() or None
    if "roteiro" in data:
        row.roteiro = data["roteiro"]
    await session.commit()
    await session.refresh(row)
    return _row_out(row)


@router.post("/{creative_id}/arquivo")
async def upload_arquivo(
    creative_id: UUID,
    file: Annotated[UploadFile, File(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "edit"))],
) -> dict[str, Any]:
    row = await _get_row(session, creative_id)
    if row.pushed_at is not None:
        raise HTTPException(409, detail={"code": "ja_enviado_pro_mega"})

    name = Path(file.filename or "arquivo").name
    if not name or name in {".", ".."}:
        raise HTTPException(400, detail={"code": "nome_invalido"})

    base = _file_dir(row)
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    abs_path = base / name
    with abs_path.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

    row.file_name = name
    row.file_mime = file.content_type or "application/octet-stream"
    row.file_size = abs_path.stat().st_size
    row.file_rel = f"creatives/{row.id}/{name}"
    row.aprovado = None  # arquivo novo volta pra "pendente"
    await session.commit()
    await session.refresh(row)
    logger.info(
        "creative_file_upload",
        creative_id=str(row.id),
        file=name,
        size=row.file_size,
        user_id=str(user.id),
    )
    return _row_out(row)


@router.get("/{creative_id}/arquivo")
async def download_arquivo(
    creative_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "view"))],
) -> FileResponse:
    row = await _get_row(session, creative_id)
    if not row.file_rel:
        raise HTTPException(404, detail={"code": "sem_arquivo"})
    abs_path = Path(get_settings().uploads_dir) / row.file_rel
    if not abs_path.is_file():
        raise HTTPException(404, detail={"code": "arquivo_sumiu"})
    return FileResponse(
        abs_path,
        filename=row.file_name or abs_path.name,
        media_type=row.file_mime or "application/octet-stream",
    )


def _match_product_by_sku(
    products: list[PricingProduct], sku: str
) -> PricingProduct | None:
    want = sku.strip().lower()
    for p in products:
        parts = [s.strip().lower() for s in (p.sku or "").split(",")]
        if want in parts:
            return p
    return None


class AprovarIn(BaseModel):
    aprovado: bool


@router.post("/{creative_id}/aprovar")
async def aprovar_creative(
    creative_id: UUID,
    payload: AprovarIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_admin)],
) -> dict[str, Any]:
    row = await _get_row(session, creative_id)

    if payload.aprovado is False:
        row.aprovado = False
        await session.commit()
        await session.refresh(row)
        return _row_out(row)

    if row.pushed_at is not None:  # já foi pro MEGA — só garante o V
        row.aprovado = True
        await session.commit()
        await session.refresh(row)
        return _row_out(row)

    if not row.file_rel:
        raise HTTPException(400, detail={"code": "sem_arquivo"})
    sku = (row.sku or "").strip()
    if not sku:
        raise HTTPException(400, detail={"code": "sem_sku"})
    abs_path = Path(get_settings().uploads_dir) / row.file_rel
    if not abs_path.is_file():
        raise HTTPException(404, detail={"code": "arquivo_sumiu"})

    candidates = (
        (
            await session.execute(
                select(PricingProduct).where(PricingProduct.sku.ilike(f"%{sku}%"))
            )
        )
        .scalars()
        .all()
    )
    product = _match_product_by_sku(list(candidates), sku)
    if product is None:
        raise HTTPException(404, detail={"code": "produto_nao_encontrado"})
    dest = (product.fotos_path or "").strip()
    if not dest:
        raise HTTPException(400, detail={"code": "produto_sem_pasta"})

    try:
        with abs_path.open("rb") as fh:
            await sidecar_request(
                "POST",
                "/upload",
                data={"dest": dest},
                files=[
                    (
                        "files",
                        (
                            row.file_name or abs_path.name,
                            fh,
                            row.file_mime or "application/octet-stream",
                        ),
                    )
                ],
                timeout=3600.0,
            )
    except MegaError as exc:
        raise HTTPException(
            502, detail={"code": "mega_error", "message": str(exc)}
        ) from exc

    fotos_count = videos_count = None
    try:
        cnt = await sidecar_request(
            "GET", "/media_counts", params={"path": dest}, timeout=300.0
        )
        fotos_count = int(cnt["fotos"])
        videos_count = int(cnt["videos"])
    except (MegaError, KeyError, TypeError, ValueError):
        pass  # contagem é cosmética; o envio em si já deu certo
    if fotos_count is not None:
        siblings = (
            (
                await session.execute(
                    select(PricingProduct).where(PricingProduct.fotos_path == dest)
                )
            )
            .scalars()
            .all()
        )
        for sib in siblings:
            sib.fotos_count = fotos_count
            sib.videos_count = videos_count

    row.aprovado = True
    row.pushed_at = datetime.now(timezone.utc)
    row.pushed_dest = dest
    await session.commit()
    await session.refresh(row)
    logger.info(
        "creative_pushed_to_mega",
        creative_id=str(row.id),
        sku=sku,
        dest=dest,
        user_id=str(user.id),
    )
    out = _row_out(row)
    out["fotos_count"] = fotos_count
    out["videos_count"] = videos_count
    return out


@router.delete("/{creative_id}")
async def delete_creative(
    creative_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "edit"))],
) -> dict[str, str]:
    row = await _get_row(session, creative_id)
    if row.pushed_at is not None and user.role != UserRole.ADMIN:
        raise HTTPException(403, detail={"code": "ja_enviado_pro_mega"})
    base = _file_dir(row)
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    await session.delete(row)
    await session.commit()
    return {"status": "deleted"}
