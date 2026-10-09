"""Configuração NOSSA de cada caixa da Central de e-mail (08/10/2026).

  GET   /api/mail/mailboxes/{id}/settings — dono da caixa ou admin (quem não
        pode recebe 404, como na Central: nem fica sabendo que a caixa existe).
  PATCH /api/mail/mailboxes/{id}/settings — admin, como o PATCH da Central;
        numa caixa `empresa`, ou mudando a ponte/visibilidade de qualquer
        caixa, também quem MEXE no /atendimento (`acesso.pode_mexer`).

O que cada campo faz está em `models/mail_atendimento.py`. Sem linha, a caixa
segue os padrões (privada, ponte desligada, remetente como a Central sempre
escolheu): criar a configuração é sempre um ato de pessoa.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user, require_admin
from app.models.user import User
from app.routers.mail import fail, user_mailbox
from app.schemas.mail_atendimento import ConfigCaixaPatch
from app.services.atendimento import acesso
from app.services.mail_atendimento import caixa

router = APIRouter(prefix="/api/mail", tags=["mail"])
Session = Annotated[AsyncSession, Depends(get_session)]
ActiveUser = Annotated[User, Depends(require_active_user)]
Admin = Annotated[User, Depends(require_admin)]


@router.get("/mailboxes/{mailbox_id}/settings")
async def ver_configuracao(
    mailbox_id: UUID, session: Session, user: ActiveUser, response: Response
):
    mailbox = await user_mailbox(session, mailbox_id, user)
    response.headers["Cache-Control"] = "no-store"
    return caixa.visao(await caixa.config_da_caixa(session, mailbox.id))


@router.patch("/mailboxes/{mailbox_id}/settings")
async def mudar_configuracao(
    mailbox_id: UUID,
    body: ConfigCaixaPatch,
    session: Session,
    user: Admin,
    response: Response,
):
    mailbox = await user_mailbox(session, mailbox_id, user)
    mudancas = body.mudancas()
    atual = await caixa.config_da_caixa(session, mailbox.id)
    if caixa.exige_mexer(atual, mudancas) and not acesso.pode_mexer(user):
        raise fail("atendimento_permission_required", 403)
    try:
        nova = await caixa.salvar(session, mailbox.id, mudancas, user)
    except caixa.ConfigCaixaError as erro:
        await session.rollback()
        raise HTTPException(erro.status, detail={"code": erro.codigo}) from None
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return caixa.visao(nova)
