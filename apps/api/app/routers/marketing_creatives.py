"""Aba Criativos do Marketing — briefing de imagens/vídeos.

Fluxo: admin (ou quem tiver edit) cadastra as linhas (modelo/marca/sku/
roteiro); o criador de conteúdo anexa um ou mais arquivos; o admin aprova
(V) ou reprova (X). Ao aprovar, TODOS os arquivos sobem pra pasta do
produto no MEGA — o produto é achado pelo SKU na tabela de preços (aba
Produtos) e o destino é o fotos_path dele. pushed_at/pushed_dest
registram o envio.

Permissões: recurso "marketing_criativos" (view/edit); aprovação é
sempre admin. Independente do recurso "marketing" (dashboards de Ads).
"""
from __future__ import annotations

import contextlib
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
from app.models import (
    MarketingCreative,
    MarketingCreativeFile,
    PricingProduct,
    User,
    UserRole,
)
from app.services.mega_fotos import MegaError, sidecar_request

logger = structlog.get_logger()
router = APIRouter(prefix="/api/marketing/creatives", tags=["marketing"])

MAX_FILES_PER_ROW = 20


def _file_out(f: MarketingCreativeFile) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "file_name": f.file_name,
        "file_mime": f.file_mime,
        "file_size": f.file_size,
    }


def _row_out(row: MarketingCreative) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "modelo": row.modelo,
        "marca": row.marca,
        "sku": row.sku,
        "equipe": row.equipe,
        "roteiro": row.roteiro,
        "files": [_file_out(f) for f in row.files],
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


def _user_equipes(user: User) -> set[str] | None:
    """Equipes (lowercase) que restringem o que o usuário vê. None =
    sem restrição (admin ou usuário sem equipe de marketing) — mesmo
    espírito das stock_tags no Controle de Estoque."""
    if user.role == UserRole.ADMIN:
        return None
    teams = {
        t.strip().lower()
        for t in (user.marketing_teams or [])
        if isinstance(t, str) and t.strip()
    }
    return teams or None


def _ensure_equipe(user: User, row: MarketingCreative) -> None:
    allowed = _user_equipes(user)
    if allowed is None:
        return
    if (row.equipe or "").strip().lower() not in allowed:
        raise HTTPException(403, detail={"code": "fora_da_sua_equipe"})


@router.get("/equipes")
async def list_equipes(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "view"))],
) -> list[str]:
    """Opções de equipe pros selects: união das equipes de marketing dos
    usuários + valores já usados nas linhas (pra nada órfão sumir)."""
    out: dict[str, str] = {}
    for lst in (await session.execute(select(User.marketing_teams))).scalars().all():
        for t in lst or []:
            if isinstance(t, str) and t.strip():
                out.setdefault(t.strip().lower(), t.strip())
    for t in (await session.execute(select(MarketingCreative.equipe))).scalars().all():
        if t and t.strip():
            out.setdefault(t.strip().lower(), t.strip())
    return sorted(out.values(), key=str.lower)


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
    allowed = _user_equipes(user)
    if allowed is not None:
        rows = [r for r in rows if (r.equipe or "").strip().lower() in allowed]
    return [_row_out(r) for r in rows]


class CreativeIn(BaseModel):
    modelo: str
    marca: str | None = None
    sku: str | None = None
    equipe: str | None = None
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
    equipe = (payload.equipe or "").strip() or None
    allowed = _user_equipes(user)
    if allowed is not None and (equipe is None or equipe.lower() not in allowed):
        # Usuário restrito: a linha nasce na equipe dele (senão sumiria
        # da própria listagem). Usa a primeira equipe com grafia original.
        firsts = [t.strip() for t in (user.marketing_teams or []) if isinstance(t, str) and t.strip()]
        equipe = sorted(firsts, key=str.lower)[0]
    row = MarketingCreative(
        id=uuid4(),
        modelo=modelo,
        marca=(payload.marca or "").strip() or None,
        sku=(payload.sku or "").strip() or None,
        equipe=equipe,
        roteiro=payload.roteiro,
        created_by=user.id,
    )
    session.add(row)
    await session.commit()
    row = await _get_row(session, row.id)
    return _row_out(row)


class CreativePatch(BaseModel):
    modelo: str | None = None
    marca: str | None = None
    sku: str | None = None
    equipe: str | None = None
    roteiro: str | None = None


@router.patch("/{creative_id}")
async def patch_creative(
    creative_id: UUID,
    payload: CreativePatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "edit"))],
) -> dict[str, Any]:
    row = await _get_row(session, creative_id)
    _ensure_equipe(user, row)
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
    if "equipe" in data:
        nova = (data["equipe"] or "").strip() or None
        allowed = _user_equipes(user)
        if allowed is not None and (nova is None or nova.lower() not in allowed):
            raise HTTPException(403, detail={"code": "fora_da_sua_equipe"})
        row.equipe = nova
    if "roteiro" in data:
        row.roteiro = data["roteiro"]
    await session.commit()
    return _row_out(row)


@router.post("/{creative_id}/arquivo")
async def upload_arquivos(
    creative_id: UUID,
    files: Annotated[list[UploadFile], File(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "edit"))],
) -> dict[str, Any]:
    row = await _get_row(session, creative_id)
    _ensure_equipe(user, row)
    if row.pushed_at is not None:
        raise HTTPException(409, detail={"code": "ja_enviado_pro_mega"})
    if not files:
        raise HTTPException(400, detail={"code": "sem_arquivo"})
    if len(row.files) + len(files) > MAX_FILES_PER_ROW:
        raise HTTPException(400, detail={"code": "muitos_arquivos"})

    base = _file_dir(row)
    base.mkdir(parents=True, exist_ok=True)
    existing = {f.file_name: f for f in row.files}
    added: list[str] = []
    for up in files:
        name = Path(up.filename or "arquivo").name
        if not name or name in {".", ".."}:
            raise HTTPException(400, detail={"code": "nome_invalido"})
        abs_path = base / name
        with abs_path.open("wb") as fh:
            shutil.copyfileobj(up.file, fh)
        old = existing.get(name)
        if old is not None:  # mesmo nome substitui o registro antigo
            row.files.remove(old)
        rec = MarketingCreativeFile(
            id=uuid4(),
            file_name=name,
            file_mime=up.content_type or "application/octet-stream",
            file_size=abs_path.stat().st_size,
            file_rel=f"creatives/{row.id}/{name}",
        )
        row.files.append(rec)
        existing[name] = rec
        added.append(name)

    row.aprovado = None  # arquivo novo volta pra "pendente"
    await session.commit()
    logger.info(
        "creative_files_upload",
        creative_id=str(row.id),
        files=added,
        user_id=str(user.id),
    )
    return _row_out(row)


@router.get("/{creative_id}/arquivo/{file_id}")
async def download_arquivo(
    creative_id: UUID,
    file_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "view"))],
    download: bool = False,
) -> FileResponse:
    row = await _get_row(session, creative_id)
    _ensure_equipe(user, row)
    rec = next((f for f in row.files if f.id == file_id), None)
    if rec is None:
        raise HTTPException(404, detail={"code": "sem_arquivo"})
    abs_path = Path(get_settings().uploads_dir) / rec.file_rel
    if not abs_path.is_file():
        raise HTTPException(404, detail={"code": "arquivo_sumiu"})
    # inline = abre no navegador (preview de imagem/vídeo); ?download=1 força baixar
    return FileResponse(
        abs_path,
        filename=rec.file_name,
        media_type=rec.file_mime or "application/octet-stream",
        content_disposition_type="attachment" if download else "inline",
    )


@router.delete("/{creative_id}/arquivo/{file_id}")
async def delete_arquivo(
    creative_id: UUID,
    file_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "edit"))],
) -> dict[str, Any]:
    row = await _get_row(session, creative_id)
    _ensure_equipe(user, row)
    if row.pushed_at is not None:
        raise HTTPException(409, detail={"code": "ja_enviado_pro_mega"})
    rec = next((f for f in row.files if f.id == file_id), None)
    if rec is None:
        raise HTTPException(404, detail={"code": "sem_arquivo"})
    abs_path = Path(get_settings().uploads_dir) / rec.file_rel
    with contextlib.suppress(OSError):
        abs_path.unlink(missing_ok=True)
    name = rec.file_name
    row.files.remove(rec)
    await session.commit()
    logger.info(
        "creative_file_delete",
        creative_id=str(row.id),
        file=name,
        user_id=str(user.id),
    )
    return _row_out(row)


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
        return _row_out(row)

    if row.pushed_at is not None:  # já foi pro MEGA — só garante o V
        row.aprovado = True
        await session.commit()
        return _row_out(row)

    recs = list(row.files)
    if not recs:
        raise HTTPException(400, detail={"code": "sem_arquivo"})
    sku = (row.sku or "").strip()
    if not sku:
        raise HTTPException(400, detail={"code": "sem_sku"})
    uploads_dir = Path(get_settings().uploads_dir)
    paths: list[tuple[MarketingCreativeFile, Path]] = []
    for rec in recs:
        abs_path = uploads_dir / rec.file_rel
        if not abs_path.is_file():
            raise HTTPException(404, detail={"code": "arquivo_sumiu"})
        paths.append((rec, abs_path))

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
        with contextlib.ExitStack() as stack:
            await sidecar_request(
                "POST",
                "/upload",
                data={"dest": dest},
                files=[
                    (
                        "files",
                        (
                            rec.file_name,
                            stack.enter_context(abs_path.open("rb")),
                            rec.file_mime or "application/octet-stream",
                        ),
                    )
                    for rec, abs_path in paths
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
    logger.info(
        "creative_pushed_to_mega",
        creative_id=str(row.id),
        sku=sku,
        dest=dest,
        n_files=len(paths),
        user_id=str(user.id),
    )
    out = _row_out(row)
    out["enviados"] = len(paths)
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
    _ensure_equipe(user, row)
    if row.pushed_at is not None and user.role != UserRole.ADMIN:
        raise HTTPException(403, detail={"code": "ja_enviado_pro_mega"})
    base = _file_dir(row)
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    await session.delete(row)
    await session.commit()
    return {"status": "deleted"}
