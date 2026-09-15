"""Cadastro de assinaturas por marca/canal. Todas as operações são locais ao cadastro."""

from base64 import b64encode
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.db import get_session
from app.deps.auth import require_permission
from app.models import EMAIL_CONTEXTOS, Company, Marca, MarcaEmailAssinatura, User
from app.schemas.email_assinaturas import (
    AssinaturaImagem,
    AssinaturaOut,
    AssinaturaPreviewIn,
    AssinaturaPreviewOut,
    AssinaturaRenderOut,
    AssinaturasGridOut,
    AssinaturasGridRow,
    AssinaturaWrite,
    MarcaAssinaturaRef,
)
from app.schemas.marcas import EmailContexto
from app.services.email_assinatura import render_assinatura

router = APIRouter(prefix="/api/email-assinaturas", tags=["email_padroes"])
_view = require_permission("email_padroes", "view")
_edit = require_permission("email_padroes", "edit")


@router.get("/grid", response_model=AssinaturasGridOut)
async def grid_assinaturas(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> AssinaturasGridOut:
    marcas = (
        await session.execute(
            select(Marca, Company.razao_social)
            .outerjoin(Company, Company.id == Marca.company_id)
            .order_by(Marca.nome)
        )
    ).all()
    assinaturas = (await session.scalars(select(MarcaEmailAssinatura))).all()
    cells = {(a.marca_id, a.contexto): AssinaturaOut.model_validate(a) for a in assinaturas}
    rows = []
    for m, razao in marcas:
        marca = MarcaAssinaturaRef.model_validate(m)
        marca.has_logo = bool(m.logo_mime)
        marca.empresa_razao_social = razao
        rows.append(
            AssinaturasGridRow(
                marca=marca,
                cells={c: cells.get((m.id, c)) for c in EMAIL_CONTEXTOS},
            )
        )
    return AssinaturasGridOut(contextos=list(EMAIL_CONTEXTOS), rows=rows)


@router.post("/preview", response_model=AssinaturaPreviewOut)
async def preview_assinatura(
    body: AssinaturaPreviewIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> AssinaturaPreviewOut:
    marca = await session.scalar(
        select(Marca).options(undefer(Marca.logo)).where(Marca.id == body.marca_id)
    )
    if marca is None:
        raise HTTPException(404, detail={"code": "marca_not_found"})
    company = await session.get(Company, marca.company_id) if marca.company_id else None
    r = render_assinatura(
        marca,
        company,
        texto=body.texto,
        incluir_logo=body.incluir_logo,
        incluir_dados_marca=body.incluir_dados_marca,
        imagens="data",
    )
    return AssinaturaPreviewOut(html=r.html, text=r.text, avisos=r.avisos)


@router.get("/{marca_id}/{contexto}/render", response_model=AssinaturaRenderOut)
async def render_assinatura_salva(
    marca_id: UUID,
    contexto: EmailContexto,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> AssinaturaRenderOut:
    """Rodapé e imagens inline para o remetente anexar ao e-mail que já montou."""
    assinatura = await session.scalar(
        select(MarcaEmailAssinatura).where(
            MarcaEmailAssinatura.marca_id == marca_id,
            MarcaEmailAssinatura.contexto == contexto,
        )
    )
    if assinatura is None:
        raise HTTPException(404, detail={"code": "assinatura_not_found"})
    if not assinatura.ativo:
        return AssinaturaRenderOut(ativo=False, html="", text="", avisos=[], inline_images=[])
    marca = await session.scalar(
        select(Marca).options(undefer(Marca.logo)).where(Marca.id == marca_id)
    )
    if marca is None:
        raise HTTPException(404, detail={"code": "marca_not_found"})
    company = await session.get(Company, marca.company_id) if marca.company_id else None
    r = render_assinatura(
        marca,
        company,
        texto=assinatura.texto,
        incluir_logo=assinatura.incluir_logo,
        incluir_dados_marca=assinatura.incluir_dados_marca,
    )
    return AssinaturaRenderOut(
        ativo=True,
        html=r.html,
        text=r.text,
        avisos=r.avisos,
        inline_images=[
            AssinaturaImagem(content_id=cid, mime=mime, base64=b64encode(raw).decode())
            for cid, (mime, raw) in r.inline_images.items()
        ],
    )


@router.put("/{marca_id}/{contexto}", response_model=AssinaturaOut)
async def save_assinatura(
    marca_id: UUID,
    contexto: EmailContexto,
    body: AssinaturaWrite,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> AssinaturaOut:
    if await session.get(Marca, marca_id) is None:
        raise HTTPException(404, detail={"code": "marca_not_found"})
    dados = body.model_dump()
    stmt = (
        insert(MarcaEmailAssinatura)
        .values(
            id=uuid4(),
            marca_id=marca_id,
            contexto=contexto,
            **dados,
        )
        .on_conflict_do_update(
            constraint="uq_marca_email_assinatura_canal",
            set_={**dados, "updated_at": func.now()},
        )
        .returning(MarcaEmailAssinatura)
    )
    assinatura = (await session.scalars(stmt.execution_options(populate_existing=True))).one()
    await session.commit()
    return AssinaturaOut.model_validate(assinatura)
