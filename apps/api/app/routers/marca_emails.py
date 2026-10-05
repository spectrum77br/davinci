"""Cadastros › E-mails — os endereços do Tuta de cada marca (05/10/2026).

Só entram as marcas com conta em Redes Sociais (hoje uranyx, charlots, 7buyers
e locagil): "deixar registrar só os e-mails das que temos em redes sociais".
Cada uma tem sac@, duvidas@ e atacado@ (enums.MarcaEmailTipo), todos aliases
da conta principal do Tuta. Substituiu a matriz de assinaturas por canal: o
Tuta tem uma assinatura só por conta, então rodapé por marca/canal não tinha
onde ser colado. Só cadastro — nada aqui envia e-mail ou mexe no Tuta.
"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import MARCA_EMAIL_TIPOS, Marca, MarcaEmail, RedeSocial, User
from app.schemas.marca_emails import (
    MarcaEmailsGridOut,
    MarcaEmailsRef,
    MarcaEmailsRow,
    MarcaEmailsWrite,
)

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
