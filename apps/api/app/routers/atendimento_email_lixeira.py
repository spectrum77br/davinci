"""Excluir uma linha das Filas usando a mesma lixeira LOCAL das Caixas.

Os originais e as referências da ponte ficam intactos para a restauração.
Como a política da timeline oculta a conversa quando qualquer original está
na lixeira, um snapshot desatualizado recusa a operação inteira.
E-mails futuros ligados à mesma conversa continuam acessíveis nas Caixas;
a conversa reaparece nas Filas quando seus originais forem restaurados.
"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.team_scope import TeamScope
from app.models import AtendimentoMensagem, MailMessage, MailOutbox, User
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


class ItemLixeiraIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    ate: AwareDatetime | None = None


class LixeiraLoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["conversas", "emails"]
    itens: list[ItemLixeiraIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validar_itens(self) -> Self:
        if len({item.id for item in self.itens}) != len(self.itens):
            raise ValueError("ids_duplicados")
        if self.tipo == "conversas" and any(item.ate is None for item in self.itens):
            raise ValueError("ate_obrigatorio_para_conversas")
        return self


class FalhaLixeiraOut(BaseModel):
    id: str
    code: str


class LixeiraLoteOut(BaseModel):
    excluidos: list[str]
    falhas: list[FalhaLixeiraOut]


def _mudou() -> HTTPException:
    return HTTPException(
        409,
        detail={
            "code": "email_conversa_mudou",
            "detail": "Esta conversa recebeu uma atualização. Atualize a fila antes de excluir.",
        },
    )


async def _excluir_conversa(
    session: AsyncSession,
    user: User,
    scope: TeamScope,
    conversa_id: UUID,
    *,
    ate: datetime,
    motivo: str | None = None,
) -> int:
    """Valida e exclui uma conversa; o chamador controla savepoint e commit."""
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
    if ultima is None or ultima != ate:
        raise _mudou()

    referencias = set(
        await session.scalars(
            select(MailMessageMeta.message_id).where(MailMessageMeta.conversa_id == conversa.id)
        )
    )
    copias = await session.scalars(
        select(AtendimentoMensagem.payload["mail"]["message_id"].astext).where(
            AtendimentoMensagem.conversa_id == conversa.id
        )
    )
    # Mesma referência canônica da política de leitura. Converter só as cópias
    # desta conversa mantém a busca dos originais pelo índice UUID da PK.
    for ref in copias:
        try:
            ident = UUID(ref)
        except (ValueError, TypeError, AttributeError):
            continue
        if str(ident) == ref:
            referencias.add(ident)
    ids = list(
        (
            await session.scalars(
                select(MailMessage.id)
                .where(MailMessage.id.in_(referencias))
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
        if message.created_at > ate:
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
    for message_id in ids:
        resultado = await administracao.mudar_lixeira(
            session,
            message_id,
            user,
            excluir=True,
            motivo=motivo,
        )
        total += int(resultado["alterado"])
    return total


@router.post("/conversas/{conversa_id}/lixeira", response_model=ExcluirConversaEmailOut)
async def excluir_conversa_email(
    conversa_id: UUID,
    body: ExcluirConversaEmailIn,
    session: Session,
    user: MailManager,
) -> ExcluirConversaEmailOut:
    """Move todos os originais da linha visível à lixeira, ou não muda nenhum."""
    scope = await escopo_do_atendimento(session, user)
    async with session.begin_nested():
        total = await _excluir_conversa(
            session,
            user,
            scope,
            conversa_id,
            ate=body.ate,
            motivo=body.motivo,
        )
    await session.commit()
    return ExcluirConversaEmailOut(emails=total)


@router.post("/lixeira-lote", response_model=LixeiraLoteOut)
async def excluir_lote_email(
    body: LixeiraLoteIn,
    session: Session,
    user: MailManager,
) -> LixeiraLoteOut:
    """Exclui só os IDs selecionados, preservando cada item recusado por inteiro."""
    scope = await escopo_do_atendimento(session, user) if body.tipo == "conversas" else None
    excluidos: set[UUID] = set()
    falhas: dict[UUID, str] = {}
    # Lotes sobrepostos adquirem as travas na mesma ordem. A resposta mantém
    # a ordem da seleção, sem devolver corpo/assunto nem detalhes da exceção.
    for item in sorted(body.itens, key=lambda entry: entry.id):
        try:
            async with session.begin_nested():
                if body.tipo == "conversas":
                    assert scope is not None and item.ate is not None
                    await _excluir_conversa(session, user, scope, item.id, ate=item.ate)
                else:
                    await managed_message(session, item.id, user)
                    await administracao.mudar_lixeira(session, item.id, user, excluir=True)
        except HTTPException as error:
            if error.status_code not in (403, 404, 409):
                raise
            code = error.detail.get("code") if isinstance(error.detail, dict) else None
            falhas[item.id] = code if isinstance(code, str) else "email_exclusao_recusada"
        else:
            excluidos.add(item.id)
    await session.commit()
    return LixeiraLoteOut(
        excluidos=[str(item.id) for item in body.itens if item.id in excluidos],
        falhas=[
            FalhaLixeiraOut(id=str(item.id), code=falhas[item.id])
            for item in body.itens
            if item.id in falhas
        ],
    )
