"""Administração da aba E-mail (12/11/2026): a lista de usuários.

Uma linha por usuário (login, nome, caixas, estado, criação, último acesso) e,
no Editar, cada conta de e-mail com as pastas dela em checkbox. Só admin que mexe no /atendimento
(`acesso.pode_mexer`) vê e grava. A regra mora em
`services/mail_atendimento/usuarios.py`; a antiga (regra por pessoa × caixa ×
loja × venda e a lixeira geral) saiu — a lixeira de cada caixa continua em
Caixas › Lixeira desta caixa.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin
from app.models import User
from app.models.mail import MailMailbox
from app.routers import mail_caixa
from app.routers.atendimento import _so_admin
from app.services.atendimento import acesso
from app.services.mail_atendimento import usuarios

router = APIRouter(
    prefix="/api/atendimento/email/admin", tags=["mail"], dependencies=[Depends(_so_admin)]
)
Session = Annotated[AsyncSession, Depends(get_session)]


def _admin(user: Annotated[User, Depends(require_admin)]) -> User:
    if not acesso.pode_mexer(user):
        raise HTTPException(403, detail={"code": "atendimento_permission_required"})
    return user


Admin = Annotated[User, Depends(_admin)]


PastaChave = Annotated[str, Field(pattern=r"^(s\d{1,2}|p[0-9a-f]{31}|x)$")]


class CaixaDoUsuarioPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    # None = a conta inteira (também as pastas que vierem); lista = só estas.
    pastas: list[PastaChave] | None = Field(default=None, max_length=usuarios.MAX_PASTAS)


class UsuarioEmailPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ligado: bool
    caixas: list[CaixaDoUsuarioPut] = Field(default_factory=list, max_length=200)


@router.get("/usuarios")
async def listar_usuarios(session: Session, user: Admin, response: Response):
    """Uma linha por usuário e, de cada caixa, as pastas (a mesma lista da coluna
    de pastas da aba E-mail, vista pelo admin)."""
    dados = await usuarios.listar(session)
    for caixa in dados["caixas"]:
        mailbox = await session.get(MailMailbox, UUID(caixa["id"]))
        caixa["pastas"] = [
            {"id": p["id"], "nome": p["nome"], "tipo": p["tipo"], "total": p["total"]}
            for p in await mail_caixa.montar_pastas(session, mailbox, user)
        ]
    response.headers["Cache-Control"] = "no-store"
    return dados


@router.put("/usuarios/{user_id}")
async def salvar_usuario(
    user_id: UUID, body: UsuarioEmailPut, session: Session, user: Admin, response: Response
):
    try:
        linha = await usuarios.salvar(
            session,
            user_id,
            ligar=body.ligado,
            caixas={c.id: c.pastas for c in body.caixas},
            autor=user,
        )
    except usuarios.UsuariosError as e:
        raise HTTPException(e.status, detail={"code": e.codigo}) from None
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return linha
