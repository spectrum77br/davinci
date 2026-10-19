"""Administração explícita de leitura e lixeira LOCAL na aba E-mail."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin
from app.models import AtendimentoMensagem, User, UserRole, UserStatus
from app.models.mail import MailMailbox, MailMessage
from app.models.mail_admin import MailAcesso, MailLixeira
from app.models.mail_atendimento import MailCaixaIndice, MailMessageMeta
from app.routers.atendimento import _so_admin
from app.schemas.mail_admin import AcessoPut, ExcluirEmailIn
from app.security.cipher import decrypt_json
from app.services.atendimento import acesso
from app.services.mail_atendimento import administracao as service
from app.services.mail_atendimento import classificacao, codigos, indice

router = APIRouter(
    prefix="/api/atendimento/email/admin", tags=["mail"], dependencies=[Depends(_so_admin)]
)
Session = Annotated[AsyncSession, Depends(get_session)]


def _admin(user: Annotated[User, Depends(require_admin)]) -> User:
    if not acesso.pode_mexer(user):
        raise HTTPException(403, detail={"code": "atendimento_permission_required"})
    return user


Admin = Annotated[User, Depends(_admin)]


@router.get("")
async def painel(session: Session, user: Admin, response: Response):
    cad, _, lojas = await service.catalogo_lojas(session)
    pessoas = (
        await session.scalars(
            select(User).where(User.role != UserRole.ADMIN).order_by(User.name, User.email)
        )
    ).all()
    caixas = (await session.scalars(select(MailMailbox).order_by(MailMailbox.label))).all()
    politicas = (
        await session.scalars(
            select(MailAcesso).order_by(MailAcesso.mailbox_id, MailAcesso.user_id)
        )
    ).all()
    response.headers["Cache-Control"] = "no-store"
    return {
        "pessoas": [
            {
                "id": str(u.id),
                "nome": u.name or u.email.split("@", 1)[0],
                "ativo": u.status == UserStatus.ACTIVE,
            }
            for u in pessoas
        ],
        "caixas": [
            {
                "id": str(c.id),
                "label": c.label,
                "address": decrypt_json(c.config_enc).get("address", ""),
            }
            for c in caixas
        ],
        "lojas": lojas,
        "politicas": [await service.politica_visao(session, p, cad=cad) for p in politicas],
        "lixeira_total": int(
            await session.scalar(select(func.count()).where(MailLixeira.excluido_em.is_not(None)))
            or 0
        ),
    }


@router.put("/acessos/{mailbox_id}/{user_id}")
async def salvar_acesso(
    mailbox_id: UUID,
    user_id: UUID,
    body: AcessoPut,
    session: Session,
    user: Admin,
    response: Response,
):
    result = await service.salvar_acesso(session, mailbox_id, user_id, body, user)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


def _cursor(quando, mid):
    return (
        base64.urlsafe_b64encode(json.dumps([quando.isoformat(), str(mid)]).encode())
        .decode()
        .rstrip("=")
    )


def _antes(valor):
    if not valor:
        return None
    try:
        obj = json.loads(base64.urlsafe_b64decode(valor + "=" * (-len(valor) % 4)))
        if not isinstance(obj, list) or len(obj) != 2:
            raise ValueError
        quando, mid = datetime.fromisoformat(obj[0]), UUID(obj[1])
        if quando.tzinfo is None:
            raise ValueError
        return quando, mid
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(422, detail={"code": "cursor_invalido"}) from None


@router.get("/lixeira")
async def lixeira(
    session: Session,
    user: Admin,
    response: Response,
    mailbox_id: UUID | None = None,
    q: str | None = Query(default=None, max_length=120),
    antes: str | None = Query(default=None, max_length=200),
    limite: int = Query(default=50, ge=1, le=100),
):
    """Busca apenas assuntos/metadados, decifrados só para este admin.

    Assunto não é indexado em claro. A busca percorre páginas limitadas,
    devolvendo cursor mesmo quando a próxima coincidência estiver adiante.
    """
    ponto, busca = _antes(antes), (q or "").strip().casefold()
    cad, _, lojas = await service.catalogo_lojas(session)
    nomes = {r["chave"]: r["nome"] for r in lojas}
    caixas = {c.id: c.label for c in (await session.scalars(select(MailMailbox))).all()}
    autores = {
        u.id: u.name or u.email.split("@", 1)[0]
        for u in (await session.scalars(select(User))).all()
    }
    itens, ultimo, pode_ter_mais = [], None, False
    for _ in range(10):
        query = (
            select(
                MailLixeira,
                MailMessage,
                MailMessageMeta,
                indice.colunas_do_selo()["chave"].label("chave"),
                indice.colunas_do_selo()["selo"].label("selo"),
                AtendimentoMensagem.payload,
            )
            .join(MailMessage, MailMessage.id == MailLixeira.message_id)
            .outerjoin(MailMessageMeta, MailMessageMeta.message_id == MailMessage.id)
            .outerjoin(MailCaixaIndice, MailCaixaIndice.message_id == MailMessage.id)
            .outerjoin(AtendimentoMensagem, AtendimentoMensagem.id == MailMessageMeta.mensagem_id)
            .where(MailLixeira.excluido_em.is_not(None))
        )
        if mailbox_id:
            query = query.where(MailMessage.mailbox_id == mailbox_id)
        if ponto:
            query = query.where(
                tuple_(MailLixeira.excluido_em, MailLixeira.message_id) < tuple_(*ponto)
            )
        linhas = (
            await session.execute(
                query.order_by(MailLixeira.excluido_em.desc(), MailLixeira.message_id.desc()).limit(
                    201
                )
            )
        ).all()
        for tr, message, meta, chave, selo, payload in linhas[:200]:
            ultimo = (tr.excluido_em, tr.message_id)
            try:
                assunto = str(decrypt_json(message.content_enc).get("subject") or "")
                assunto = (
                    indice.ASSUNTO_DE_SEGURANCA
                    if selo == indice.SELO_SEGURANCA
                    else codigos.proteger(assunto, "").assunto
                )
            except Exception:  # noqa: BLE001 — lista mantém o item ilegível restaurável
                assunto = "(não foi possível ler)"
            loja = nomes.get(indice.canonica(chave, cad)) if chave else None
            pedido = meta.pedido_marketplace if meta else None
            if (
                busca
                and busca
                not in " ".join(
                    (assunto, loja or "", pedido or "", caixas.get(message.mailbox_id, ""))
                ).casefold()
            ):
                continue
            itens.append(
                {
                    "message_id": str(message.id),
                    "mailbox_id": str(message.mailbox_id),
                    "caixa": caixas.get(message.mailbox_id, "Caixa"),
                    "assunto": assunto or "E-mail recebido",
                    "loja": loja,
                    "pedido": pedido,
                    "tipo_email": classificacao.tipos_de_meta(meta, payload)["tipo_email"]
                    if meta
                    else None,
                    "excluido_em": tr.excluido_em,
                    "excluido_por": autores.get(tr.excluido_por),
                    "motivo": tr.motivo,
                }
            )
            if len(itens) >= limite:
                pode_ter_mais = ultimo != (linhas[-1][0].excluido_em, linhas[-1][0].message_id)
                break
        if len(itens) >= limite:
            break
        if len(linhas) <= 200:
            pode_ter_mais = False
            break
        ponto, pode_ter_mais = ultimo, True
    response.headers["Cache-Control"] = "no-store"
    return {"itens": itens, "proximo": _cursor(*ultimo) if pode_ter_mais and ultimo else None}


@router.post("/emails/{message_id}/excluir")
async def excluir(
    message_id: UUID, body: ExcluirEmailIn, session: Session, user: Admin, response: Response
):
    result = await service.mudar_lixeira(
        session, message_id, user, excluir=True, motivo=body.motivo
    )
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/emails/{message_id}/restaurar")
async def restaurar(message_id: UUID, session: Session, user: Admin, response: Response):
    result = await service.mudar_lixeira(session, message_id, user, excluir=False)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result
