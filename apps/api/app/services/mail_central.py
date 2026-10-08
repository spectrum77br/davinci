"""Private mail ingestion and human reply queue. No network or automatic replies."""

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.mail import MailAttachment, MailMailbox, MailMessage, MailOutbox
from app.models.user import User
from app.schemas.mail import Ingest, ReplyIn
from app.security.cipher import decrypt_json, encrypt_bytes, encrypt_json


class MailError(ValueError):
    def __init__(self, code: str, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(code)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def allowed(mailbox: MailMailbox, user: User) -> bool:
    return user.role == UserRole.ADMIN or mailbox.owner_user_id == user.id


def send_ready(mailbox: MailMailbox) -> bool:
    return bool(
        mailbox.send_enabled
        and mailbox.agent_can_send
        and mailbox.state == "online"
        and mailbox.last_seen_at
        and mailbox.last_seen_at > datetime.now(UTC) - timedelta(minutes=3)
    )


def mailbox_view(mailbox: MailMailbox) -> dict:
    config = decrypt_json(mailbox.config_enc)
    return {
        "id": str(mailbox.id),
        "label": mailbox.label,
        "owner_user_id": str(mailbox.owner_user_id),
        "address": config["address"],
        "aliases": config["aliases"],
        "send_enabled": mailbox.send_enabled,
        "can_send": send_ready(mailbox),
        "state": mailbox.state
        if mailbox.last_seen_at and mailbox.last_seen_at > datetime.now(UTC) - timedelta(minutes=3)
        else "offline",
        "last_seen_at": mailbox.last_seen_at,
        "last_sync_at": mailbox.last_sync_at,
        "error_code": mailbox.error_code,
    }


def message_view(message: MailMessage, *, detail: bool = False) -> dict:
    content = decrypt_json(message.content_enc)
    result = {
        "id": str(message.id),
        "mailbox_id": str(message.mailbox_id),
        "received_at": message.received_at,
        "direction": message.direction,
        "folder": content["folder"],
        "subject": content["subject"],
        "from_address": content["from_address"],
        "from_name": content["from_name"],
        "has_attachments": message.attachment_count > 0,
    }
    if detail:
        result.update(content)
    return result


def reply_envelope(mailbox: MailMailbox, message: MailMessage) -> dict:
    config = decrypt_json(mailbox.config_enc)
    content = decrypt_json(message.content_enc)
    aliases = {address.lower() for address in config["aliases"]}
    recipient = content.get("reply_to") or content["from_address"]
    sender = next(
        (address for address in content["to"] if address.lower() in aliases),
        config["address"],
    ).lower()
    return {
        "to": recipient,
        "from_address": sender,
        "can_reply": message.direction == "inbound" and recipient.lower() not in aliases,
    }


async def ingest(session: AsyncSession, mailbox: MailMailbox, body: Ingest) -> dict:
    # The route locks the mailbox: ingestion is serial per account and the
    # unique constraint is the second defense against replay/concurrent batches.
    accepted = duplicates = 0
    for item in body.messages:
        exists = await session.scalar(
            select(MailMessage.id).where(
                MailMessage.mailbox_id == mailbox.id,
                MailMessage.source_id == item.source_id,
            )
        )
        if exists:
            duplicates += 1
            continue
        content = item.model_dump(
            mode="json",
            exclude={
                "source_id",
                "received_at",
                "direction",
                "attachments",
            },
        )
        message = MailMessage(
            id=uuid4(),
            mailbox_id=mailbox.id,
            source_id=item.source_id,
            direction=item.direction,
            received_at=item.received_at,
            content_enc=encrypt_json(content),
            attachment_count=len(item.attachments),
        )
        session.add(message)
        await session.flush()
        for attachment in item.attachments:
            data = base64.b64decode(attachment.data_base64, validate=True)
            session.add(
                MailAttachment(
                    message_id=message.id,
                    metadata_enc=encrypt_json(
                        {
                            "filename": attachment.filename,
                            "content_type": attachment.content_type,
                        }
                    ),
                    content_enc=encrypt_bytes(data),
                    size=len(data),
                )
            )
        accepted += 1
    mailbox.last_sync_at = datetime.now(UTC)
    await session.flush()
    return {"accepted": accepted, "duplicates": duplicates}


async def queue_reply(
    session: AsyncSession,
    mailbox: MailMailbox,
    message: MailMessage,
    user: User,
    body: ReplyIn,
) -> MailOutbox:
    # The route locks the original message. A repeated request never sends twice.
    existing = await session.scalar(
        select(MailOutbox).where(
            MailOutbox.mailbox_id == mailbox.id,
            MailOutbox.request_id == body.request_id,
        )
    )
    envelope = reply_envelope(mailbox, message)
    config = decrypt_json(mailbox.config_enc)
    sender = str(body.from_address or envelope["from_address"])
    if sender.lower() not in {address.lower() for address in config["aliases"]}:
        raise MailError("sender_not_authorized")
    if existing:
        original = decrypt_json(existing.content_enc)
        if (
            existing.message_id != message.id
            or existing.author_user_id != user.id
            or (original["text"] != body.text or original["from_address"] != sender)
        ):
            raise MailError("request_id_conflict")
        return existing
    if not envelope["can_reply"]:
        raise MailError("message_not_replyable")
    if not send_ready(mailbox):
        raise MailError("sending_unavailable")
    active = await session.scalar(
        select(MailOutbox.id).where(
            MailOutbox.message_id == message.id,
            MailOutbox.status.in_(["queued", "leased", "uncertain"]),
        )
    )
    if active:
        raise MailError("reply_already_pending")
    content = decrypt_json(message.content_enc)
    job_id = uuid4()
    subject = content["subject"]
    payload = {
        "from_address": sender,
        "to": envelope["to"],
        "subject": subject if subject.lower().startswith("re:") else f"Re: {subject}",
        "text": body.text,
        "in_reply_to": content.get("message_id") or None,
        "references": (content.get("references", []) + [content.get("message_id")])[-50:],
        "message_id": f"<{job_id}@mail.davinci.local>",
    }
    payload["references"] = [value for value in payload["references"] if value]
    job = MailOutbox(
        id=job_id,
        mailbox_id=mailbox.id,
        message_id=message.id,
        author_user_id=user.id,
        request_id=body.request_id,
        content_enc=encrypt_json(payload),
        status="queued",
    )
    session.add(job)
    await session.flush()
    return job


def outbox_view(job: MailOutbox) -> dict:
    content = decrypt_json(job.content_enc)
    return {
        "id": str(job.id),
        "status": job.status,
        "text": content["text"],
        "to": content["to"],
        "from_address": content["from_address"],
        "created_at": job.created_at,
        "error_code": job.error_code,
    }


async def lease(session: AsyncSession, mailbox: MailMailbox) -> list[dict]:
    # The mailbox row is locked by the agent dependency. Expired claims are
    # ambiguous, not retryable: SMTP may have succeeded before a crash.
    now = datetime.now(UTC)
    expired = (
        await session.scalars(
            select(MailOutbox)
            .where(
                MailOutbox.mailbox_id == mailbox.id,
                MailOutbox.status == "leased",
                MailOutbox.leased_at < now - timedelta(minutes=15),
            )
            .with_for_update()
        )
    ).all()
    for job in expired:
        job.status = "uncertain"
        job.error_code = "receipt_timeout"
    if not send_ready(mailbox):
        return []
    jobs = (
        await session.scalars(
            select(MailOutbox)
            .where(
                MailOutbox.mailbox_id == mailbox.id,
                MailOutbox.status == "queued",
            )
            .order_by(MailOutbox.created_at)
            .limit(5)
            .with_for_update(skip_locked=True)
        )
    ).all()
    result = []
    for job in jobs:
        # Recheck the author still has access before dispatching a queued reply.
        author = await session.get(User, job.author_user_id)
        if author is None or author.status.value != "active" or not allowed(mailbox, author):
            job.status = "failed"
            job.error_code = "author_access_revoked"
            job.completed_at = now
            continue
        token = secrets.token_urlsafe(32)
        job.lease_token_hash = token_hash(token)
        job.leased_at = now
        job.status = "leased"
        result.append({"id": str(job.id), "lease_token": token, **decrypt_json(job.content_enc)})
    await session.flush()
    return result


def verify_receipt(job: MailOutbox, lease_token: str) -> None:
    if not job.lease_token_hash or not secrets.compare_digest(
        job.lease_token_hash,
        token_hash(lease_token),
    ):
        raise MailError("invalid_lease", 401)
    if job.status == "queued":
        raise MailError("job_not_leased")
