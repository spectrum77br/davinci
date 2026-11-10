"""Excluir uma linha das Filas usando a mesma lixeira LOCAL das Caixas.

Os originais e as referências da ponte ficam intactos para a restauração.
Como a política da timeline oculta a conversa quando qualquer original está
na lixeira, um snapshot desatualizado recusa a operação inteira.
E-mails futuros ligados à mesma conversa continuam acessíveis nas Caixas;
a conversa reaparece nas Filas quando seus originais forem restaurados.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import AtendimentoMensagem, MailMessage, MailOutbox
from app.models.mail_atendimento import MailMessageMeta
from app.routers.atendimento import _conversa_ou_404, _travar_ou_409
from app.routers.mail import MailManager, managed_message
from app.schemas.mail_admin import ExcluirEmailIn
from app.services.atendimento import separa_email
from app.services.atendimento.escopo import escopo_do_atendimento
from app.services.mail_atendimento import administracao

router = APIRouter(prefix="/api/atendimento/email", tags=["atendimento"])
Session = Annotated[AsyncSession, Depends(get_session)]


class ExcluirConversaEmailIn(ExcluirEmailIn):
    # Obrigatório: exatamente o email_visto_ate da linha que a pessoa viu.
    ate: AwareDatetime


class ExcluirConversaEmailOut(BaseModel):
    emails: int


def _mudou() -> HTTPException:
    return HTTPException(
        409,
        detail={
            "code": "email_conversa_mudou",
            "detail": "Esta conversa recebeu uma atualização. Atualize a fila antes de excluir.",
        },
    )


@router.post("/conversas/{conversa_id}/lixeira", response_model=ExcluirConversaEmailOut)
async def excluir_conversa_email(
    conversa_id: UUID,
    body: ExcluirConversaEmailIn,
    session: Session,
    user: MailManager,
) -> ExcluirConversaEmailOut:
    """Move todos os originais da linha visível à lixeira, ou não muda nenhum."""
    scope = await escopo_do_atendimento(session, user)
    conversa = await _conversa_ou_404(session, str(conversa_id), scope)
    if not separa_email.e_conversa_de_email(conversa):
        raise HTTPException(409, detail={"code": "email_conversa_invalida"})
    # A ponte/enfileiramento também trava a conversa antes da mensagem. Quem
    # esperou o sync precisa reler o snapshot e o acesso depois dessa trava.
    await _travar_ou_409(session, conversa, "email_conversa_ocupada")
    await _conversa_ou_404(session, str(conversa_id), scope)
    ultima = await session.scalar(
        select(func.max(AtendimentoMensagem.created_at)).where(
            AtendimentoMensagem.conversa_id == conversa.id
        )
    )
    if ultima is None or ultima != body.ate:
        raise _mudou()

    metas = select(MailMessageMeta.message_id).where(MailMessageMeta.conversa_id == conversa.id)
    copias = select(AtendimentoMensagem.payload["mail"]["message_id"].astext).where(
        AtendimentoMensagem.conversa_id == conversa.id
    )
    # A cópia também conta quando a meta foi reprocessada ou removida. Não
    # converte JSON legado para UUID: referência inválida nunca quebra a rota.
    ids = list(
        (
            await session.scalars(
                select(MailMessage.id)
                .where(or_(MailMessage.id.in_(metas), cast(MailMessage.id, String).in_(copias)))
                .order_by(MailMessage.id)
            )
        ).all()
    )
    if not ids:
        raise HTTPException(409, detail={"code": "email_conversa_sem_emails"})

    # Mesma autorização de /mail/messages/{id}/trash, para TODOS os e-mails,
    # antes de qualquer escrita. A ordem estável também evita travas cruzadas.
    for message_id in ids:
        message = await managed_message(session, message_id, user)
        if message.created_at > body.ate:
            raise _mudou()
    if await session.scalar(
        select(MailOutbox.id)
        .where(
            MailOutbox.message_id.in_(ids),
            MailOutbox.status.in_(("queued", "leased", "uncertain")),
        )
        .limit(1)
    ):
        raise HTTPException(409, detail={"code": "email_envio_pendente"})

    total = 0
    async with session.begin_nested():
        for message_id in ids:
            resultado = await administracao.mudar_lixeira(
                session,
                message_id,
                user,
                excluir=True,
                motivo=body.motivo,
            )
            total += int(resultado["alterado"])
    await session.commit()
    return ExcluirConversaEmailOut(emails=total)
