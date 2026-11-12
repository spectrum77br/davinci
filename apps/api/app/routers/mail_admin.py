"""Administração da aba E-mail (12/11/2026): a lista de usuários.

Uma linha por usuário (login, nome, caixas, estado, criação, último acesso) e,
no Editar, cada conta de e-mail com as pastas dela em checkbox. Qualquer admin
ativo vê e grava. A regra mora em
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
from app.services.mail_atendimento import usuarios

# Qualquer admin ativo (12/11/2026, o Eduardo: "habilite o user harry potter a ver
# a administracao tambem"; antes era só quem mexe no /atendimento).
router = APIRouter(
    prefix="/api/atendimento/email/admin", tags=["mail"], dependencies=[Depends(require_admin)]
)
Session = Annotated[AsyncSession, Depends(get_session)]
Admin = Annotated[User, Depends(require_admin)]


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
