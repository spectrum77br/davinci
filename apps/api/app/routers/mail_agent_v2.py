"""Rotas v2 do agente do Mac (o NOSSO conector do Tuta) — 08/10/2026.

  POST /api/mail/agent/{id}/v2/sync     — instância, versões, contadores, pastas
                                          e aliases da conta → pastas a ler
  POST /api/mail/agent/{id}/v2/ingest   — até 20 e-mails, resultado POR e-mail
  POST /api/mail/agent/{id}/v2/count    — os ids de uma pasta numa janela
  POST /api/mail/agent/{id}/v2/changes  — pasta nova / apagado

A MESMA autenticação do v1 da Central (`routers/mail.agent_mailbox`: a chave
por caixa no Bearer, a linha da caixa travada — um agente por vez) e o mesmo
`limited_body` (teto de bytes; o 422 nunca ecoa conteúdo). O v1
(heartbeat/ingest/lease/receipt) não muda nada: o conector continua a pulsar,
pedir os envios e mandar o recibo por ele. Nada aqui manda e-mail.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.mail import MailMailbox
from app.routers.mail import agent_mailbox, fail, limited_body
from app.schemas.mail_v2 import ChangesIn, CountIn, IngestV2, SyncIn
from app.services import mail_v2 as servico

router = APIRouter(prefix="/api/mail/agent/{mailbox_id}/v2", tags=["mail"])
Session = Annotated[AsyncSession, Depends(get_session)]
AgentMailbox = Annotated[MailMailbox, Depends(agent_mailbox)]


@router.post("/sync")
async def sincronizar(request: Request, session: Session, mailbox: AgentMailbox):
    body = await limited_body(request, SyncIn, 256 * 1024)
    try:
        resultado = await servico.sync(session, mailbox, body)
    except servico.MailV2Error as erro:
        # Guarda o "dois agentes" que o serviço anotou antes de recusar.
        await session.commit()
        raise fail(erro.code, erro.status) from None
    await session.commit()
    return resultado


@router.post("/ingest")
async def ingerir(request: Request, session: Session, mailbox: AgentMailbox):
    body = await limited_body(request, IngestV2, 25 * 1024 * 1024)
    resultado = await servico.ingest(session, mailbox, body)
    await session.commit()
    return resultado


@router.post("/count")
async def contar(request: Request, session: Session, mailbox: AgentMailbox):
    body = await limited_body(request, CountIn, 1024 * 1024)
    try:
        resultado = await servico.count(session, mailbox, body)
    except servico.MailV2Error as erro:
        raise fail(erro.code, erro.status) from None
    await session.commit()
    return resultado


@router.post("/changes")
async def mudancas(request: Request, session: Session, mailbox: AgentMailbox):
    body = await limited_body(request, ChangesIn, 256 * 1024)
    resultado = await servico.changes(session, mailbox, body)
    await session.commit()
    return resultado
