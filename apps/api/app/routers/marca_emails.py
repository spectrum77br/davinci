"""Cadastros › E-mails — os endereços do Tuta de cada marca (05/10/2026).

Só entram as marcas com conta em Redes Sociais (hoje uranyx, charlots, 7buyers
e locagil): "deixar registrar só os e-mails das que temos em redes sociais".
Cada uma tem sac@ e admin@ (enums.MarcaEmailTipo; desde 28/10/2026 — os
antigos duvidas@ e atacado@ saíram), aliases da conta principal do Tuta.
Substituiu a matriz de assinaturas por canal: o Tuta tem uma assinatura só por
conta, então rodapé por marca/canal não tinha onde ser colado. Só cadastro —
nada aqui envia e-mail ou mexe no Tuta.

A assinatura da tela (`GET /{marca_id}/assinatura`) é montada aqui, pela
mesma função da resposta do /atendimento (app.services.assinatura_email).
"""

from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import MARCA_EMAIL_TIPOS, Marca, MarcaEmail, RedeSocial, User
from app.schemas.marca_emails import (
    AssinaturaOut,
    MarcaEmailsGridOut,
    MarcaEmailsRef,
    MarcaEmailsRow,
    MarcaEmailsWrite,
)
from app.services import assinatura_email

router = APIRouter(prefix="/api/marca-emails", tags=["email_padroes"])
_view = require_permission("email_padroes", "view")
_edit = require_permission("email_padroes", "edit")


def _tem_rede(marca_id):
    """Marca "das redes": tem pelo menos uma conta de verdade (linha sem @ é
    só e-mail/fone reservado e não conta)."""
    return exists().where(RedeSocial.marca_id == marca_id, RedeSocial.conta.is_not(None))


def _row(m: Marca, emails: dict[str, str]) -> MarcaEmailsRow:
    marca = MarcaEmailsRef.model_validate(m)
    marca.has_logo = bool(m.logo_mime)
    visual = assinatura_email.visual_de(m.slug)
    marca.nome_exibicao = assinatura_email.nome_exibicao(m)
    marca.cor = visual.cor
    marca.logo_publico = visual.logo is not None
    marca.fone_formatado = assinatura_email.formatar_fone(m.sac_fone)
    return MarcaEmailsRow(marca=marca, emails=MarcaEmailsWrite.model_construct(**emails))


@router.get("", response_model=MarcaEmailsGridOut)
async def grid_marca_emails(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> MarcaEmailsGridOut:
    marcas = (
        await session.scalars(select(Marca).where(_tem_rede(Marca.id)).order_by(Marca.nome))
    ).all()
    linhas = (
        await session.scalars(
            select(MarcaEmail).where(MarcaEmail.marca_id.in_([m.id for m in marcas]))
        )
    ).all()
    por_marca: dict[UUID, dict[str, str]] = {}
    for e in linhas:
        if e.tipo in MARCA_EMAIL_TIPOS:
            por_marca.setdefault(e.marca_id, {})[e.tipo] = e.email
    return MarcaEmailsGridOut(
        tipos=list(MARCA_EMAIL_TIPOS),
        rows=[_row(m, por_marca.get(m.id, {})) for m in marcas],
    )


@router.put("/{marca_id}", response_model=MarcaEmailsRow)
async def save_marca_emails(
    marca_id: UUID,
    body: MarcaEmailsWrite,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> MarcaEmailsRow:
    marca = await session.get(Marca, marca_id)
    if marca is None:
        raise HTTPException(404, detail={"code": "marca_not_found"})
    if not await session.scalar(select(_tem_rede(marca_id))):
        raise HTTPException(409, detail={"code": "marca_sem_redes_sociais"})
    dados = body.model_dump()
    for tipo in MARCA_EMAIL_TIPOS:
        email = dados[tipo]
        if email is None:
            await session.execute(
                delete(MarcaEmail).where(MarcaEmail.marca_id == marca_id, MarcaEmail.tipo == tipo)
            )
            continue
        # Upsert que não regrava o mesmo valor: salvar a linha sem mudar nada
        # não vira evento no Histórico.
        await session.execute(
            insert(MarcaEmail)
            .values(id=uuid4(), marca_id=marca_id, tipo=tipo, email=email)
            .on_conflict_do_update(
                constraint="uq_marca_emails_tipo",
                set_={"email": email, "updated_at": func.now()},
                where=MarcaEmail.email != email,
            )
        )
    await session.commit()
    return _row(marca, {t: e for t, e in dados.items() if e})


@router.get("/{marca_id}/assinatura", response_model=AssinaturaOut)
async def assinatura_da_marca(
    marca_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
    tipo: Annotated[Literal["sac", "admin"], Query()] = "sac",
    email: Annotated[str, Query(max_length=254)] = "",
    fone: Annotated[str, Query(max_length=40)] = "",
) -> AssinaturaOut:
    """A prévia de Cadastros › E-mails: a MESMA assinatura que a resposta do
    /atendimento põe no fim do e-mail. `email`/`fone` vêm da tela (mudar lá
    vale só para a cópia); nada é gravado."""
    marca = await session.get(Marca, marca_id)
    if marca is None:
        raise HTTPException(404, detail={"code": "marca_not_found"})
    return AssinaturaOut(
        html=assinatura_email.montar_html(marca, tipo, email, fone),
        texto=assinatura_email.montar_texto(marca, tipo, email, fone),
    )
