"""Private inbox + mailbox-scoped Mac worker. No mailbox credentials on the server."""

import secrets
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.db import get_session
from app.deps.auth import require_active_user, require_admin
from app.models.enums import UserRole, UserStatus
from app.models.mail import MailAttachment, MailDraft, MailMailbox, MailMessage, MailOutbox
from app.models.user import User
from app.schemas.mail import (
    ComposeDraftIn,
    ComposeSendIn,
    Heartbeat,
    Ingest,
    MailboxCreate,
    MailboxPatch,
    OutboxResolve,
    Receipt,
    ReplyIn,
)
from app.schemas.mail_bridge_sync import BridgeSnapshot
from app.security.cipher import decrypt_bytes, decrypt_json, encrypt_json
from app.services import mail_central as service
from app.services import mail_composer
from app.services.mail_atendimento import administracao as mail_admin
from app.services.mail_atendimento import caixa as mailbox_settings
from app.services.mail_atendimento import responder as mail_responder

router = APIRouter(prefix="/api/mail", tags=["mail"])
Session = Annotated[AsyncSession, Depends(get_session)]
ActiveUser = Annotated[User, Depends(require_active_user)]
Admin = Annotated[User, Depends(require_admin)]


def fail(code: str, status: int = 404) -> HTTPException:
    return HTTPException(status, detail={"code": code})


async def limited_body(request: Request, model: type[BaseModel], limit: int) -> BaseModel:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise fail("body_too_large", 413)
    raw = bytearray()
    async for part in request.stream():
        if len(raw) + len(part) > limit:
            raise fail("body_too_large", 413)
        raw.extend(part)
    try:
        return model.model_validate_json(raw or b"{}")
    except ValidationError as error:
        # Do not echo mailbox content, attachments or tokens through validation errors.
        fields = [
            {"field": ".".join(map(str, item["loc"])), "type": item["type"]}
            for item in error.errors(include_input=False, include_url=False)[:20]
        ]
        raise HTTPException(422, detail={"code": "invalid_body", "fields": fields}) from None


async def user_mailbox(session: AsyncSession, mailbox_id: UUID, user: User) -> MailMailbox:
    mailbox = await session.get(MailMailbox, mailbox_id)
    if (
        mailbox is None
        or not service.allowed(mailbox, user)
        or not await mail_admin.pode_ver_caixa(session, mailbox, user)
    ):
        raise fail("mailbox_not_found")
    return mailbox


async def readable_mailbox(session: AsyncSession, mailbox_id: UUID, user: User) -> MailMailbox:
    # READ-only routes: owner/admin, or an active user on the mailbox's
    # "Quem mais vê" list (mailbox_readers.pode_ver_caixa). Writing stays on
    # `user_mailbox` (allowed()). Same 404 for whoever cannot see it.
    mailbox = await session.get(MailMailbox, mailbox_id)
    if mailbox is None or not await mail_admin.pode_ver_caixa(session, mailbox, user):
        raise fail("mailbox_not_found")
    return mailbox


async def require_operator(session: AsyncSession, mailbox: MailMailbox, user: User) -> None:
    # Company mailboxes (our settings table): sending-related actions through the
    # raw mailbox also need the Atendimento "mexe" permission. Private: unchanged.
    if await mailbox_settings.bloqueia_sem_mexer(session, mailbox.id, user):
        raise fail("atendimento_permission_required", 403)


async def user_message(
    session: AsyncSession,
    message_id: UUID,
    user: User,
    *,
    lock: bool = False,
    read: bool = False,
) -> tuple[MailMessage, MailMailbox]:
    query = select(MailMessage).where(
        MailMessage.id == message_id, mail_admin.filtro_mensagem(user)
    )
    if lock:
        query = query.with_for_update()
    message = await session.scalar(query)
    if message is None:
        raise fail("message_not_found")
    check = readable_mailbox if read else user_mailbox
    return message, await check(session, message.mailbox_id, user)


async def agent_mailbox(
    mailbox_id: UUID,
    session: Session,
    authorization: Annotated[str | None, Header()] = None,
) -> MailMailbox:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 256:
        raise fail("agent_unauthorized", 401)
    mailbox = await session.scalar(
        select(MailMailbox).where(MailMailbox.id == mailbox_id).with_for_update()
    )
    expected = mailbox.agent_token_hash if mailbox else "0" * 64
    if not secrets.compare_digest(expected, service.token_hash(token)) or mailbox is None:
        raise fail("agent_unauthorized", 401)
    return mailbox


AgentMailbox = Annotated[MailMailbox, Depends(agent_mailbox)]


@router.get("/mailboxes")
async def list_mailboxes(session: Session, user: ActiveUser, response: Response):
    response.headers["Cache-Control"] = "no-store"
    query = select(MailMailbox).order_by(MailMailbox.label)
    if user.role != UserRole.ADMIN:
        query = query.where(mail_admin.filtro_caixas(user))
    mailboxes = (await session.scalars(query)).all()
    items = []
    for mailbox in mailboxes:
        item = service.mailbox_view(mailbox)
        if not service.allowed(mailbox, user):
            item["so_leitura"] = True
            # Só lê a caixa, mas pode RESPONDER o e-mail recebido (permissão
            # `atendimento_email` ver+editar, 28/10/2026): a tela mostra só o
            # formulário de resposta; o resto da caixa continua só leitura.
            item["pode_responder"] = await mail_admin.pode_responder_caixa(session, mailbox, user)
            # ... e ESCREVE e-mail novo (Nova mensagem, rascunhos, encaminhar,
            # anexos) quando vê a caixa inteira (29/10/2026).
            item["pode_compor"] = await mail_composer.can_compose(session, mailbox, user)
        if await mail_admin.tem_politica(session, mailbox.id, user):
            # A concessão é de mensagens, não da lista de aliases das outras lojas.
            item.update(address="", aliases=[], leitura_filtrada=True)
        items.append(item)
    return {"items": items}


@router.post("/mailboxes", status_code=201)
async def create_mailbox(body: MailboxCreate, session: Session, user: Admin, response: Response):
    owner_id = body.owner_user_id or user.id
    owner = await session.get(User, owner_id)
    if owner is None or owner.status != UserStatus.ACTIVE:
        raise fail("owner_not_active", 422)
    address = str(body.address)
    aliases = list(dict.fromkeys([address.lower(), *(str(a).lower() for a in body.aliases)]))
    token = secrets.token_urlsafe(32)
    mailbox = MailMailbox(
        owner_user_id=owner_id,
        label=body.label,
        config_enc=encrypt_json({"address": address, "aliases": aliases}),
        agent_token_hash=service.token_hash(token),
        send_enabled=False,
        agent_can_send=False,
        state="offline",
    )
    session.add(mailbox)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {**service.mailbox_view(mailbox), "agent_token": token}


@router.patch("/mailboxes/{mailbox_id}")
async def update_mailbox(mailbox_id: UUID, body: MailboxPatch, session: Session, user: Admin):
    mailbox = await user_mailbox(session, mailbox_id, user)
    if body.send_enabled is not None or body.aliases is not None:
        await require_operator(session, mailbox, user)
    if body.label is not None:
        mailbox.label = body.label
    if body.send_enabled is not None:
        mailbox.send_enabled = body.send_enabled
    if body.aliases is not None:
        config = decrypt_json(mailbox.config_enc)
        address = str(config["address"])
        config["aliases"] = list(
            dict.fromkeys([address.lower(), *(str(a).lower() for a in body.aliases)])
        )
        mailbox.config_enc = encrypt_json(config)
    await session.commit()
    return service.mailbox_view(mailbox)


@router.post("/mailboxes/{mailbox_id}/token")
async def rotate_token(mailbox_id: UUID, session: Session, user: Admin, response: Response):
    mailbox = await user_mailbox(session, mailbox_id, user)
    await require_operator(session, mailbox, user)
    token = secrets.token_urlsafe(32)
    mailbox.agent_token_hash = service.token_hash(token)
    mailbox.agent_can_send = False
    mailbox.state = "offline"
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"agent_token": token}


@router.get("/mailboxes/{mailbox_id}/messages")
async def list_messages(
    mailbox_id: UUID,
    session: Session,
    user: ActiveUser,
    response: Response,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
):
    await readable_mailbox(session, mailbox_id, user)
    messages = (
        await session.scalars(
            select(MailMessage)
            .where(
                MailMessage.mailbox_id == mailbox_id,
                mail_admin.filtro_mensagem(user),
            )
            .order_by(MailMessage.received_at.desc(), MailMessage.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [service.message_view(message) for message in messages[:limit]],
        "more": len(messages) > limit,
    }


@router.get("/messages/{message_id}")
async def get_message(message_id: UUID, session: Session, user: ActiveUser, response: Response):
    message, mailbox = await user_message(session, message_id, user, read=True)
    attachments = (
        await session.scalars(
            select(MailAttachment)
            .where(
                MailAttachment.message_id == message.id,
            )
            .options(load_only(MailAttachment.id, MailAttachment.size, MailAttachment.metadata_enc))
        )
    ).all()
    outbox_rows = await session.stream_scalars(
        select(MailOutbox)
        .where(
            MailOutbox.message_id == message.id,
        )
        .order_by(MailOutbox.created_at)
        .execution_options(yield_per=1)
    )
    dono = service.allowed(mailbox, user)
    responde = dono or await mail_admin.pode_responder_caixa(session, mailbox, user)
    pode_compor = await mail_composer.can_compose(session, mailbox, user) if responde else False
    outbox = []
    async for job in outbox_rows:
        item = service.outbox_view(job)
        acao = decrypt_json(job.content_enc).get("action")
        propria = job.author_user_id == user.id
        # A mesma regra do POST /outbox/{id}/resolve (28/10/2026).
        item["pode_conferir"] = bool(
            (dono and (acao is None or (propria and pode_compor)))
            or (responde and not dono and propria and (acao is None or pode_compor))
        )
        outbox.append(item)
    caixa_cfg = await mailbox_settings.config_da_caixa(session, mailbox.id)
    reply = service.reply_envelope(
        mailbox,
        message,
        strict=caixa_cfg.remetente_estrito,
        main_allowed=not caixa_cfg.empresa,
    )
    if await mail_admin.tem_politica(session, mailbox.id, user):
        # A message-scoped grant must not reveal the other stores' aliases.
        visible_senders = {*reply["receiving_aliases"], reply["from_address"]}
        reply["allowed_from_addresses"] = [
            address for address in reply["allowed_from_addresses"] if address in visible_senders
        ]
    response.headers["Cache-Control"] = "no-store"
    return {
        **service.message_view(message, detail=True),
        "attachments": [
            {"id": str(item.id), "size": item.size, **decrypt_json(item.metadata_enc)}
            for item in attachments
        ],
        "reply": {
            **reply,
            "send_ready": service.send_ready(mailbox),
        },
        "outbox": outbox,
    }


@router.get("/attachments/{attachment_id}")
async def download_attachment(attachment_id: UUID, session: Session, user: ActiveUser):
    attachment = await session.get(MailAttachment, attachment_id)
    if attachment is None:
        raise fail("attachment_not_found")
    await user_message(session, attachment.message_id, user, read=True)
    metadata = decrypt_json(attachment.metadata_enc)
    filename = metadata["filename"].replace("\\", "/").rsplit("/", 1)[-1] or "attachment"
    return Response(
        content=decrypt_bytes(attachment.content_enc),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.post("/messages/{message_id}/reply", status_code=202)
async def reply(message_id: UUID, request: Request, session: Session, user: ActiveUser):
    # Dono/admin como sempre, ou quem tem `atendimento_email` ver+editar e vê a
    # caixa (28/10/2026). Quem não pode recebe o mesmo 404 de antes.
    message, mailbox = await user_message(session, message_id, user, lock=True, read=True)
    if not await mail_admin.pode_responder_caixa(session, mailbox, user):
        raise fail("mailbox_not_found")
    if await mailbox_settings.bloqueia_resposta(session, mailbox.id, user):
        raise fail("atendimento_permission_required", 403)
    mailbox = await session.scalar(
        select(MailMailbox)
        .where(MailMailbox.id == mailbox.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    body = await limited_body(request, ReplyIn, 512 * 1024)
    recorte = await mail_admin.remetentes_da_concessao(session, mailbox, message, user)
    if recorte is not None:
        # Concessão filtrada: só o remetente que a pessoa vê. Qualquer outro
        # (de outra loja, inexistente ou o principal) = o MESMO código, antes de
        # todas as outras recusas — sem dar pista de quais endereços existem.
        permitidos, padrao = recorte
        if str(body.from_address or padrao or "").lower() not in permitidos:
            raise fail("sender_not_authorized", 409)
    try:
        job = await service.queue_reply(session, mailbox, message, user, body)
    except service.MailError as error:
        raise fail(error.code, error.status) from None
    await session.commit()
    return service.outbox_view(job)


@router.post("/outbox/{job_id}/resolve")
async def resolve_outbox(job_id: UUID, body: OutboxResolve, session: Session, user: ActiveUser):
    # A person checked Tuta; never resends. Owner/admin only, like reading.
    job = await session.get(MailOutbox, job_id)
    mailbox = await session.get(MailMailbox, job.mailbox_id) if job else None
    if job is None or mailbox is None:
        raise fail("job_not_found")
    dono = service.allowed(mailbox, user)
    # Quem só responde (`atendimento_email` ver+editar, 28/10/2026) confere
    # apenas o PRÓPRIO envio: a resposta simples e, desde 29/10/2026, o do
    # compositor (Nova mensagem/encaminhar) enquanto ainda pode compor.
    acao = decrypt_json(job.content_enc).get("action")
    propria = (
        not dono
        and acao is None
        and job.author_user_id == user.id
        and await mail_admin.pode_responder_caixa(session, mailbox, user)
    )
    if not (dono or propria or acao is not None):
        raise fail("job_not_found")
    if acao is not None and (
        job.author_user_id != user.id or not await mail_composer.can_compose(session, mailbox, user)
    ):
        raise fail("job_not_found")
    if job.author_user_id == user.id:
        # O próprio envio: a mesma trava de responder (dono não-admin com a
        # permissão também confere o dele na caixa da empresa).
        if await mailbox_settings.bloqueia_resposta(session, mailbox.id, user):
            raise fail("atendimento_permission_required", 403)
    else:
        await require_operator(session, mailbox, user)
    job = await session.scalar(
        select(MailOutbox)
        .where(MailOutbox.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    try:
        service.resolve_uncertain(job, user, sent=body.saiu)
    except service.MailError as error:
        raise fail(error.code, error.status) from None
    # Our side table records who resolved it (and syncs an Atendimento reply).
    await mail_responder.apos_resolver_na_caixa(session, job, user, saiu=body.saiu)
    await session.commit()
    return service.outbox_view(job)


async def compose_mailbox(session, mailbox_id, user, *, lock=False):
    # Who sees the mailbox (404 otherwise); composing is `can_compose`: the
    # owner/admin, or `atendimento_email` view+edit on the whole mailbox.
    mailbox = await readable_mailbox(session, mailbox_id, user)
    if not await mail_composer.can_compose(session, mailbox, user):
        raise fail("compose_not_allowed", 403)
    if lock:
        mailbox = await session.scalar(
            select(MailMailbox)
            .where(MailMailbox.id == mailbox.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    return mailbox


@router.get("/mailboxes/{mailbox_id}/compose-context")
async def compose_context(
    mailbox_id: UUID,
    session: Session,
    user: ActiveUser,
    response: Response,
    source_message_id: UUID | None = None,
    action: str = Query("new", pattern="^(new|reply|reply_all|forward)$"),
):
    mailbox = await readable_mailbox(session, mailbox_id, user)
    try:
        source = await mail_composer.source_message(session, mailbox, user, source_message_id)
        result = await mail_composer.context(session, mailbox, user, source, action)
    except service.MailError as error:
        raise fail(error.code, error.status) from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/mailboxes/{mailbox_id}/send", status_code=202)
async def compose_send(mailbox_id: UUID, request: Request, session: Session, user: ActiveUser):
    mailbox = await compose_mailbox(session, mailbox_id, user, lock=True)
    body = await limited_body(request, ComposeSendIn, mail_composer.MAX_REQUEST_BYTES)
    try:
        job = await mail_composer.queue(session, mailbox, user, body)
    except service.MailError as error:
        raise fail(error.code, error.status) from None
    await session.commit()
    return mail_composer.outbox_view(job)


@router.get("/mailboxes/{mailbox_id}/outbox")
async def compose_outbox(mailbox_id: UUID, session: Session, user: ActiveUser, response: Response):
    await compose_mailbox(session, mailbox_id, user)
    jobs = await session.stream_scalars(
        select(MailOutbox)
        .where(
            MailOutbox.mailbox_id == mailbox_id,
            MailOutbox.author_user_id == user.id,
        )
        .order_by(MailOutbox.created_at.desc())
        .limit(100)
        .execution_options(yield_per=1)
    )
    response.headers["Cache-Control"] = "no-store"
    return {"items": [mail_composer.outbox_view(job) async for job in jobs]}


@router.get("/mailboxes/{mailbox_id}/drafts")
async def list_drafts(mailbox_id: UUID, session: Session, user: ActiveUser, response: Response):
    await compose_mailbox(session, mailbox_id, user)
    drafts = await session.stream_scalars(
        select(MailDraft)
        .where(
            MailDraft.mailbox_id == mailbox_id,
            MailDraft.author_user_id == user.id,
        )
        .order_by(MailDraft.updated_at.desc())
        .limit(100)
        .execution_options(yield_per=1)
    )
    response.headers["Cache-Control"] = "no-store"
    return {"items": [mail_composer.draft_view(draft, detail=False) async for draft in drafts]}


@router.post("/mailboxes/{mailbox_id}/drafts", status_code=201)
async def create_draft(mailbox_id: UUID, request: Request, session: Session, user: ActiveUser):
    mailbox = await compose_mailbox(session, mailbox_id, user, lock=True)
    body = await limited_body(request, ComposeDraftIn, mail_composer.MAX_REQUEST_BYTES)
    count = await session.scalar(
        select(func.count())
        .select_from(MailDraft)
        .where(
            MailDraft.mailbox_id == mailbox_id,
            MailDraft.author_user_id == user.id,
        )
    )
    if count >= 100:
        raise fail("draft_limit_reached", 409)
    try:
        await mail_composer.validate_draft(session, mailbox, user, body)
    except service.MailError as error:
        raise fail(error.code, error.status) from None
    draft = MailDraft(
        mailbox_id=mailbox_id,
        author_user_id=user.id,
        content_enc=encrypt_json(body.model_dump(mode="json")),
    )
    session.add(draft)
    await session.commit()
    await session.refresh(draft)
    return mail_composer.draft_view(draft)


async def own_draft(session, draft_id, user, *, lock=False):
    statement = select(MailDraft).where(
        MailDraft.id == draft_id,
        MailDraft.author_user_id == user.id,
    )
    if lock:
        statement = statement.with_for_update()
    draft = await session.scalar(statement)
    if draft is None:
        raise fail("draft_not_found")
    mailbox = await compose_mailbox(session, draft.mailbox_id, user)
    return draft, mailbox


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: UUID, session: Session, user: ActiveUser, response: Response):
    draft, mailbox = await own_draft(session, draft_id, user)
    body = ComposeDraftIn.model_validate(decrypt_json(draft.content_enc))
    try:
        await mail_composer.source_message(session, mailbox, user, body.source_message_id)
    except service.MailError as error:
        raise fail(error.code, error.status) from None
    response.headers["Cache-Control"] = "no-store"
    return mail_composer.draft_view(draft)


@router.patch("/drafts/{draft_id}")
async def update_draft(draft_id: UUID, request: Request, session: Session, user: ActiveUser):
    draft, mailbox = await own_draft(session, draft_id, user, lock=True)
    body = await limited_body(request, ComposeDraftIn, mail_composer.MAX_REQUEST_BYTES)
    try:
        await mail_composer.validate_draft(session, mailbox, user, body)
    except service.MailError as error:
        raise fail(error.code, error.status) from None
    draft.content_enc = encrypt_json(body.model_dump(mode="json"))
    await session.commit()
    await session.refresh(draft)
    return mail_composer.draft_view(draft)


@router.delete("/drafts/{draft_id}", status_code=204)
async def delete_draft(draft_id: UUID, session: Session, user: ActiveUser):
    draft, _ = await own_draft(session, draft_id, user, lock=True)
    await session.delete(draft)
    await session.commit()
    return Response(status_code=204)


@router.post("/agent/{mailbox_id}/heartbeat")
async def heartbeat(request: Request, session: Session, mailbox: AgentMailbox):
    body = await limited_body(request, Heartbeat, 4096)
    mailbox.last_seen_at = datetime.now(UTC)
    mailbox.state = body.state
    mailbox.agent_can_send = body.can_send
    mailbox.error_code = body.error_code
    # Company mailboxes: off as well while the global brake or a pause is on.
    send_enabled = await mailbox_settings.envio_efetivo(session, mailbox)
    await session.commit()
    return {"ok": True, "send_enabled": send_enabled}


@router.post("/agent/{mailbox_id}/reconcile")
async def reconcile(request: Request, session: Session, mailbox: AgentMailbox):
    from app.services.mail_bridge_sync import reconcile as apply_snapshot

    body = await limited_body(request, BridgeSnapshot, 8 * 1024 * 1024)
    try:
        result = await apply_snapshot(session, mailbox, body)
    except service.MailError as error:
        raise fail(error.code, error.status) from None
    await session.commit()
    return result


@router.post("/agent/{mailbox_id}/ingest")
async def ingest(request: Request, session: Session, mailbox: AgentMailbox):
    body = await limited_body(request, Ingest, 25 * 1024 * 1024)
    try:
        result = await service.ingest(session, mailbox, body)
    except service.MailError as error:
        raise fail(error.code, error.status) from None
    await session.commit()
    return result


@router.post("/agent/{mailbox_id}/outbox/lease")
async def lease(request: Request, session: Session, mailbox: AgentMailbox):
    # Validate even an empty request under a small cap; do not accept arbitrary jobs.
    from app.schemas.mail import StrictModel

    await limited_body(request, StrictModel, 1024)
    jobs = await service.lease(session, mailbox)
    await session.commit()
    return {"jobs": jobs}


@router.post("/agent/{mailbox_id}/outbox/{job_id}/receipt")
async def receipt(job_id: UUID, request: Request, session: Session, mailbox: AgentMailbox):
    body = await limited_body(request, Receipt, 8192)
    job = await session.scalar(
        select(MailOutbox)
        .where(
            MailOutbox.id == job_id,
            MailOutbox.mailbox_id == mailbox.id,
        )
        .with_for_update()
    )
    if job is None:
        raise fail("job_not_found")
    try:
        service.verify_receipt(job, body.lease_token)
    except service.MailError as error:
        raise fail(error.code, error.status) from None
    recorded = body.model_dump(exclude={"lease_token"})
    if job.completed_at is not None:
        if job.status != body.status or decrypt_json(job.receipt_enc) != recorded:
            # A person resolved it first: keep the late receipt aside (the job
            # itself never changes) so the conversation can warn about it.
            await mail_responder.registrar_recibo_tardio(session, job, status=body.status)
            await session.commit()
            raise fail("receipt_conflict", 409)
        return {"ok": True, "status": job.status}
    job.status = body.status
    job.error_code = body.error_code
    job.completed_at = datetime.now(UTC)
    job.receipt_enc = encrypt_json(recorded)
    await session.commit()
    return {"ok": True, "status": job.status}
