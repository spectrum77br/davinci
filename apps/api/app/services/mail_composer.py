"""Explicit human composition. No network I/O or automatic messages."""

import base64
import hashlib
import json
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select

from app.models.mail import MailAttachment, MailMailbox, MailMessage, MailOutbox
from app.schemas.mail import ComposeAttachmentIn, ComposeDraftIn, ComposeSendIn
from app.security.cipher import decrypt_bytes, decrypt_json, encrypt_json
from app.services import mail_central
from app.services.mail_atendimento import administracao as mail_admin
from app.services.mail_atendimento import caixa as mailbox_settings

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_REQUEST_BYTES = 25 * 1024 * 1024


async def can_compose(session, mailbox, user):
    # A filtered grant authorizes individual messages, not a free composer
    # with arbitrary recipients or aliases from other stores.
    # Owner/admin as always, or whoever has `atendimento_email` view+edit and
    # sees the WHOLE mailbox (29/10/2026: "libere para o usuario marrocos poder
    # enviar a mensagem"): Nova mensagem, rascunhos, encaminhar, anexos.
    return (
        await mail_admin.pode_responder_caixa(session, mailbox, user)
        and not await mail_admin.tem_politica(session, mailbox.id, user)
        and not await mailbox_settings.bloqueia_resposta(session, mailbox.id, user)
    )


async def source_message(session, mailbox, user, message_id):
    if message_id is None:
        return None
    message = await session.scalar(
        select(MailMessage).where(
            MailMessage.id == message_id,
            MailMessage.mailbox_id == mailbox.id,
            mail_admin.filtro_mensagem(user),
        )
    )
    if message is None:
        raise mail_central.MailError("message_not_found", 404)
    return message


def recipients(body):
    seen = set()
    result = {}
    for field in ("to", "cc", "bcc"):
        result[field] = []
        for value in getattr(body, field):
            address = str(value).lower()
            if address not in seen:
                result[field].append(address)
                seen.add(address)
    return result


async def context(session, mailbox, user, source=None, action="new"):
    cfg = await mailbox_settings.config_da_caixa(session, mailbox.id)
    config = decrypt_json(mailbox.config_enc)
    own = {address.lower() for address in config["aliases"]}
    allowed = [
        address.lower()
        for address in config["aliases"]
        if not cfg.empresa or address.lower() != config["address"].lower()
    ]
    reply_to, reply_all_to, reply_all_cc = [], [], []
    if source is not None:
        envelope = mail_central.reply_envelope(
            mailbox, source, strict=cfg.remetente_estrito, main_allowed=not cfg.empresa
        )
        if action in ("reply", "reply_all"):
            allowed = envelope["allowed_from_addresses"]
        content = decrypt_json(source.content_enc)
        target = str(content.get("reply_to") or content["from_address"]).lower()
        reply_to = [target] if target not in own else []
        seen = set(own)
        for field, candidates in (
            (reply_all_to, [target, *content.get("to", [])]),
            (reply_all_cc, content.get("cc", [])),
        ):
            for address in candidates:
                address = address.lower()
                if address not in seen:
                    field.append(address)
                    seen.add(address)
    permitted = await can_compose(session, mailbox, user)
    return {
        "can_compose": permitted,
        "allowed_from_addresses": allowed if permitted else [],
        "send_ready": permitted
        and mail_central.send_ready(mailbox)
        and await mailbox_settings.envio_efetivo(session, mailbox),
        "send_mode": cfg.envio_modo if cfg.empresa else "real",
        "reply_to": reply_to,
        "reply_all_to": reply_all_to,
        "reply_all_cc": reply_all_cc,
        "limits": {
            "attachment_count": 10,
            "attachment_bytes": 10 * 1024 * 1024,
            "total_attachment_bytes": MAX_ATTACHMENT_BYTES,
            "recipients": 100,
        },
    }


async def attachments(session, source, body):
    result = [
        item.model_dump(include={"filename", "content_type", "data_base64"})
        for item in body.attachments
    ]
    if body.source_attachment_ids:
        if source is None or body.action != "forward":
            raise mail_central.MailError("source_attachments_require_forward", 422)
        rows = (
            await session.scalars(
                select(MailAttachment).where(
                    MailAttachment.message_id == source.id,
                    MailAttachment.id.in_(body.source_attachment_ids),
                )
            )
        ).all()
        if len(rows) != len(body.source_attachment_ids):
            raise mail_central.MailError("attachment_not_found", 404)
        by_id = {row.id: row for row in rows}
        for ident in body.source_attachment_ids:
            row = by_id[ident]
            metadata = decrypt_json(row.metadata_enc)
            raw = decrypt_bytes(row.content_enc)
            if len(raw) > 10 * 1024 * 1024:
                raise mail_central.MailError("attachment_too_large", 422)
            result.append(
                {
                    "filename": metadata["filename"].replace("\\", "/").rsplit("/", 1)[-1]
                    or "attachment",
                    "content_type": metadata["content_type"],
                    "data_base64": base64.b64encode(raw).decode(),
                }
            )
    if (
        len(result) > 10
        or sum(len(base64.b64decode(a["data_base64"])) for a in result) > MAX_ATTACHMENT_BYTES
    ):
        raise mail_central.MailError("attachments_too_large", 422)
    try:
        for item in result:
            ComposeAttachmentIn.model_validate(item)
    except ValidationError:
        raise mail_central.MailError("invalid_attachment", 422) from None
    return result


async def validate_draft(session, mailbox, user, body: ComposeDraftIn):
    source = await source_message(session, mailbox, user, body.source_message_id)
    await attachments(session, source, body)
    return source


async def queue(session, mailbox: MailMailbox, user, body: ComposeSendIn):
    if not await can_compose(session, mailbox, user):
        raise mail_central.MailError("compose_not_allowed", 403)
    fingerprint = hashlib.sha256(
        json.dumps(
            body.model_dump(mode="json", exclude={"request_id"}),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    existing = await session.scalar(
        select(MailOutbox).where(
            MailOutbox.mailbox_id == mailbox.id,
            MailOutbox.request_id == body.request_id,
        )
    )
    if existing:
        old = decrypt_json(existing.content_enc)
        if existing.author_user_id != user.id or old.get("compose_hash") != fingerprint:
            raise mail_central.MailError("request_id_conflict")
        return existing
    source = await source_message(session, mailbox, user, body.source_message_id)
    if body.action in ("reply", "reply_all") and source.direction != "inbound":
        raise mail_central.MailError("message_not_replyable")
    cfg = await mailbox_settings.config_da_caixa(session, mailbox.id)
    ctx = await context(session, mailbox, user, source, body.action)
    sender = str(body.from_address).lower()
    if sender not in ctx["allowed_from_addresses"]:
        raise mail_central.MailError("sender_not_authorized")
    if not ctx["send_ready"]:
        raise mail_central.MailError("sending_unavailable")
    destinations = recipients(body)
    for address in [*destinations["to"], *destinations["cc"], *destinations["bcc"]]:
        blocked = await mailbox_settings.trava_de_envio(session, cfg, address)
        if blocked:
            raise mail_central.MailError(blocked)
    if source is not None and await session.scalar(
        select(MailOutbox.id).where(
            MailOutbox.message_id == source.id,
            MailOutbox.status.in_(["queued", "leased", "uncertain"]),
        )
    ):
        raise mail_central.MailError("reply_already_pending")
    files = await attachments(session, source, body)
    original = decrypt_json(source.content_enc) if source is not None else {}
    threaded = body.action in ("reply", "reply_all")
    job_id = uuid4()
    payload = {
        "action": body.action,
        "compose_hash": fingerprint,
        "from_address": sender,
        **destinations,
        "subject": body.subject,
        "text": body.text,
        "attachments": files,
        "message_id": f"<{job_id}@mail.davinci.local>",
        "in_reply_to": original.get("message_id") if threaded else None,
        "references": [
            value
            for value in (original.get("references", []) + [original.get("message_id")])[-50:]
            if value
        ]
        if threaded
        else [],
    }
    job = MailOutbox(
        id=job_id,
        mailbox_id=mailbox.id,
        message_id=source.id if source else None,
        author_user_id=user.id,
        request_id=body.request_id,
        content_enc=encrypt_json(payload),
        status="queued",
    )
    session.add(job)
    await session.flush()
    await mailbox_settings.registrar_job(session, job, cfg)
    return job


def outbox_view(job):
    payload = decrypt_json(job.content_enc)
    return {
        "id": str(job.id),
        "status": job.status,
        "text": payload.get("text", ""),
        "to": payload.get("to", []),
        "from_address": payload.get("from_address", ""),
        "created_at": job.created_at,
        "error_code": job.error_code,
        "action": payload.get("action", "reply"),
        "subject": payload.get("subject", ""),
        "cc": payload.get("cc", []),
        "bcc": payload.get("bcc", []),
        "source_message_id": job.message_id,
        "attachments": [
            {
                "filename": item["filename"],
                "content_type": item["content_type"],
                "size": (len(item["data_base64"]) // 4) * 3
                - (len(item["data_base64"]) - len(item["data_base64"].rstrip("="))),
            }
            for item in payload.get("attachments", [])
        ],
    }


def draft_view(draft, *, detail=True):
    content = decrypt_json(draft.content_enc)
    if not detail:
        content = {key: content.get(key) for key in ("action", "subject", "source_message_id")}
    return {
        "id": draft.id,
        "mailbox_id": draft.mailbox_id,
        **content,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
    }
