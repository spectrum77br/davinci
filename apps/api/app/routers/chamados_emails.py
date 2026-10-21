"""Pós-venda › Chamados × e-mail (21/10/2026, decisão 3 do Eduardo) — rotas NOVAS.

Só acrescenta (regra do dono, 21/10/2026: "não mudar nada do que os Chamados já
fazem"): nada aqui escreve nos chamados. O e-mail de uma pasta com a categoria
"Chamado (Pós-venda)" fica ligado ao chamado pelo lado do e-mail
(`mail_message_meta.chamado_id`, `services/mail_atendimento/posvenda`), e a tela
do chamado ganha um bloco só de leitura com os e-mails ligados.

    GET  /api/chamados/{id}/emails           — os e-mails ligados ao chamado (às
                                               linhas do CASO, como o histórico).
                                               Texto protegido só pra quem LÊ o
                                               e-mail; os outros, `pode_ler=false`
    GET  /api/chamados/emails/anexos/{id}    — o anexo de um e-mail ligado (baixa)
    GET  /api/chamados/emails/sem-chamado    — os e-mails guardados pra ligar à mão
                                               (sem pedido / pedido sem chamado)
    POST /api/chamados/emails/{mid}/ligar    — ligar um deles a um chamado escolhido

Quem LÊ o texto: a trava do /atendimento (a mesma do `_view` de lá), a loja no
escopo da equipe e a política do e-mail por pessoa — quem tem só `chamados.view`
vê que o e-mail existe (data, pasta, loja), nunca o conteúdo. Ligar à mão é
escrita no e-mail: `chamados.edit` E mexer no /atendimento. Nada sai para a
plataforma nem para o cliente.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import MailAttachment, User, UserRole
from app.models.mail_atendimento import MailMessageMeta
from app.routers.atendimento import SO_ADMIN as ATENDIMENTO_SO_ADMIN
from app.routers.chamados import _get, _ids_do_caso
from app.security.cipher import decrypt_bytes, decrypt_json
from app.services.atendimento import acesso
from app.services.mail_atendimento import posvenda

router = APIRouter(prefix="/api/chamados", tags=["chamados"])

Session = Annotated[AsyncSession, Depends(get_session)]


class LigarEmailIn(BaseModel):
    chamado_id: UUID


def _le_os_emails(user: User) -> bool:
    """A trava do /atendimento para LER — a mesma do `_view` de lá."""
    if ATENDIMENTO_SO_ADMIN:
        return acesso.pode_ver(user)
    if user.role == UserRole.ADMIN:
        return True
    return bool(((user.permissions or {}).get("atendimento") or {}).get("view", False))


def _mexe_nos_emails(user: User) -> None:
    if not acesso.pode_mexer(user):
        raise HTTPException(403, detail={"code": "atendimento_so_quem_mexe"})


@router.get("/emails/anexos/{attachment_id}")
async def anexo_do_email(
    attachment_id: UUID,
    session: Session,
    user: Annotated[User, Depends(require_permission("chamados", "view"))],
) -> Response:
    """O anexo de um e-mail ligado a um chamado — só pra quem lê esse e-mail. Os
    mesmos cabeçalhos da Central: baixa, nunca abre no navegador."""
    a = await session.get(MailAttachment, attachment_id)
    if a is None or not await posvenda.anexo_do_chamado(
        session, user, a, ve_o_atendimento=_le_os_emails(user)
    ):
        raise HTTPException(404, detail={"code": "chamado_email_anexo_not_found"})
    nome = str(decrypt_json(a.metadata_enc).get("filename") or "anexo")
    nome = nome.replace("\\", "/").rsplit("/", 1)[-1] or "anexo"
    return Response(
        content=decrypt_bytes(a.content_enc),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(nome, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.get("/emails/sem-chamado")
async def emails_sem_chamado(
    session: Session,
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
    response: Response,
) -> dict[str, Any]:
    """Os e-mails de pasta "Chamado (Pós-venda)" guardados pra uma pessoa ligar:
    sem pedido desta loja (`chamado_sem_pedido`) ou com pedido sem chamado
    (`chamado_sem_chamado` — o chamado se abre do jeito de sempre, na aba)."""
    _mexe_nos_emails(user)
    response.headers["Cache-Control"] = "no-store"
    return await posvenda.emails_sem_chamado(session, user, ve_o_atendimento=_le_os_emails(user))


@router.post("/emails/{message_id}/ligar")
async def ligar_email(
    message_id: UUID,
    body: LigarEmailIn,
    session: Session,
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> dict[str, Any]:
    """Liga o e-mail guardado ao chamado escolhido (da mesma loja e plataforma).
    Só a meta do e-mail muda; o chamado, não."""
    _mexe_nos_emails(user)
    meta = await session.get(MailMessageMeta, message_id)
    if meta is None or not await posvenda.visiveis(
        session, user, [meta], ve_o_atendimento=_le_os_emails(user)
    ):
        raise HTTPException(404, detail={"code": "email_nao_encontrado"})
    chamado = await _get(session, body.chamado_id)
    try:
        r = await posvenda.ligar_a_mao(session, meta, chamado, user)
    except posvenda.LigacaoError as e:
        await session.rollback()
        raise HTTPException(409, detail={"code": e.code}) from e
    await session.commit()
    return {"id": str(message_id), "chamado_id": str(r.chamado_id), "estado": meta.estado}


@router.get("/{chamado_id}/emails")
async def emails_do_chamado(
    chamado_id: UUID,
    session: Session,
    user: Annotated[User, Depends(require_permission("chamados", "view"))],
    response: Response,
) -> dict[str, Any]:
    """Os e-mails ligados ao chamado (às linhas do CASO, como o histórico)."""
    ch = await _get(session, chamado_id)
    response.headers["Cache-Control"] = "no-store"
    return await posvenda.emails_do_chamado(
        session, user, await _ids_do_caso(session, ch), ve_o_atendimento=_le_os_emails(user)
    )
