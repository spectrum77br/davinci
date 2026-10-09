"""Private mail ingestion and human reply queue. No network or automatic replies."""

import base64
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.mail import MailAttachment, MailMailbox, MailMessage, MailOutbox
from app.models.user import User
from app.schemas.mail import Ingest, MessageIn, ReplyIn
from app.security.cipher import decrypt_bytes, decrypt_json, encrypt_bytes, encrypt_json
from app.services.mail_atendimento import caixa as mailbox_settings

# A leased job without a receipt after this long is ambiguous (never resent).
LEASE_TIMEOUT = timedelta(minutes=15)
# Jobs handed to the agent per lease, and how many queued rows one lease looks
# at (held jobs — see `mailbox_settings.trava_no_lease` — must not starve the rest).
LEASE_BATCH = 5
LEASE_SCAN = 50


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


def receiving_aliases(aliases: set[str], content: dict) -> list[str]:
    """Mailbox addresses that actually received this message, in order.

    `delivered_to` (optional, from a v2 agent) first, then To, then Cc.
    """
    found = (
        address.lower()
        for address in [
            *(content.get("delivered_to") or []),
            *content.get("to", []),
            *content.get("cc", []),
        ]
    )
    return list(dict.fromkeys(address for address in found if address in aliases))


def reply_envelope(
    mailbox: MailMailbox,
    message: MailMessage,
    *,
    strict: bool = False,
    main_allowed: bool = True,
) -> dict:
    config = decrypt_json(mailbox.config_enc)
    content = decrypt_json(message.content_enc)
    aliases = {address.lower() for address in config["aliases"]}
    recipient = content.get("reply_to") or content["from_address"]
    received = receiving_aliases(aliases, content)
    if not main_allowed:
        # Company mailboxes (our settings table): the main address is the Tuta
        # LOGIN of the account and never answers a customer — not even when
        # it was the one that received the message.
        main = str(config["address"]).lower()
        received = [address for address in received if address != main]
    if strict:
        # Strict sender (per-mailbox setting): only an address that received
        # the message may answer it; never fall back to the main address.
        sender = received[0] if received else None
    elif main_allowed:
        sender = next(
            (address for address in content["to"] if address.lower() in aliases),
            config["address"],
        ).lower()
    else:
        sender = received[0] if received else None
    return {
        "to": recipient,
        "from_address": sender,
        # Strict: no receiving alias = no reply. Otherwise a person may still
        # pick an authorized alias by hand (the company mailbox without a
        # receiving alias suggests none rather than the main address).
        "can_reply": message.direction == "inbound"
        and recipient.lower() not in aliases
        and (sender is not None or not strict),
        "strict_sender": strict,
        "receiving_aliases": received,
    }


def attachment_metadata(attachment) -> dict:
    return {
        "filename": attachment.filename,
        "content_type": attachment.content_type,
        "content_id": attachment.content_id,
        "disposition": attachment.disposition,
    }


async def enrich_presentation(session: AsyncSession, message: MailMessage, item: MessageIn) -> bool:
    """Repair presentation only. Never reclassify, move, reply or process a queue."""
    if item.html is None and not any(a.content_id for a in item.attachments):
        return False
    content = decrypt_json(message.content_enc)
    if (
        str(content.get("from_address", "")).lower() != str(item.from_address).lower()
        or content.get("subject", "") != item.subject
        or content.get("message_id", "") != item.message_id
    ):
        raise MailError("mail_enrichment_identity_conflict")
    # Populate missing presentation; a conflicting existing HTML is not a repair.
    if item.html is not None and content.get("html") not in (None, "", item.html):
        raise MailError("mail_enrichment_html_conflict")
    stored = (
        await session.scalars(
            select(MailAttachment)
            .where(MailAttachment.message_id == message.id)
            .order_by(MailAttachment.id)
        )
    ).all()
    existing = []
    for attachment in stored:
        meta = decrypt_json(attachment.metadata_enc)
        signature = (
            meta["filename"],
            meta["content_type"],
            hashlib.sha256(decrypt_bytes(attachment.content_enc)).digest(),
        )
        existing.append((attachment, meta, signature))
    updates = []
    additions = []
    used = set()
    known_cids = {
        meta.get("content_id"): attachment.id
        for attachment, meta, _ in existing
        if meta.get("content_id")
    }
    for attachment in item.attachments:
        if attachment.content_id is None:
            continue
        data = base64.b64decode(attachment.data_base64, validate=True)
        signature = (attachment.filename, attachment.content_type, hashlib.sha256(data).digest())
        candidates = [
            (old, meta) for old, meta, sig in existing if sig == signature and old.id not in used
        ]
        candidates.sort(key=lambda pair: pair[1].get("content_id") != attachment.content_id)
        if candidates:
            old, meta = candidates[0]
            if meta.get("content_id") not in (None, attachment.content_id) or (
                attachment.content_id in known_cids and known_cids[attachment.content_id] != old.id
            ):
                raise MailError("mail_enrichment_attachment_conflict")
            used.add(old.id)
            updated = dict(meta, content_id=attachment.content_id)
            if attachment.disposition == "inline" or "disposition" not in updated:
                updated["disposition"] = attachment.disposition
            if updated != meta:
                updates.append((old, updated))
            known_cids[attachment.content_id] = old.id
        else:
            if attachment.content_id in known_cids or any(
                sig[:2] == signature[:2] for _, _, sig in existing
            ):
                # Same name/type with different bytes is ambiguous, not a
                # missing inline part. Never append a replacement version.
                raise MailError("mail_enrichment_attachment_conflict")
            if attachment.disposition != "inline" or item.html is None:
                raise MailError("mail_enrichment_attachment_not_inline")
            additions.append((attachment, data))
            known_cids[attachment.content_id] = None
    if len(stored) + len(additions) > 10:
        raise MailError("mail_enrichment_attachment_limit")
    html_changed = item.html is not None and content.get("html") != item.html
    changed = html_changed or bool(updates or additions)
    if not changed:
        return False
    # Every validation above precedes writes, including v2 per-message rejection.
    if html_changed:
        content["html"] = item.html
        message.content_enc = encrypt_json(content)
    for old, meta in updates:
        old.metadata_enc = encrypt_json(meta)
    for attachment, data in additions:
        session.add(
            MailAttachment(
                message_id=message.id,
                metadata_enc=encrypt_json(attachment_metadata(attachment)),
                content_enc=encrypt_bytes(data),
                size=len(data),
            )
        )
    message.attachment_count = len(stored) + len(additions)
    return True


async def ingest(session: AsyncSession, mailbox: MailMailbox, body: Ingest) -> dict:
    # The route locks the mailbox: ingestion is serial per account and the
    # unique constraint is the second defense against replay/concurrent batches.
    accepted = duplicates = updated = 0
    for item in body.messages:
        exists = await session.scalar(
            select(MailMessage).where(
                MailMessage.mailbox_id == mailbox.id,
                MailMessage.source_id == item.source_id,
            )
        )
        if exists:
            if await enrich_presentation(session, exists, item):
                updated += 1
            duplicates += 1
            continue
        if body.enrich_existing_only:
            raise MailError("mail_enrichment_source_not_found", 404)
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
                    metadata_enc=encrypt_json(attachment_metadata(attachment)),
                    content_enc=encrypt_bytes(data),
                    size=len(data),
                )
            )
        accepted += 1
    mailbox.last_sync_at = datetime.now(UTC)
    await session.flush()
    result = {"accepted": accepted, "duplicates": duplicates}
    if updated:
        result["updated"] = updated
    return result


FORM_TAG = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,31}$")


async def queue_reply(
    session: AsyncSession,
    mailbox: MailMailbox,
    message: MailMessage,
    user: User,
    body: ReplyIn,
    *,
    form_recipient: str | None = None,
    subject_tag: str | None = None,
) -> MailOutbox:
    # The route locks the original message. A repeated request never sends twice.
    #
    # `form_recipient` / `subject_tag` are internal-only (never from HTTP: the
    # raw /reply route does not pass them). The Atendimento layer uses them for
    # site contact forms (RF6), which arrive FROM our own sac@ alias TO it with
    # the customer's address in the body: the recipient is computed server-side
    # from the original message, only in company mailboxes, and only when the
    # original sender is one of this mailbox's addresses. `subject_tag` puts the
    # site protocol ("US-26-0014") in brackets when the subject lacks it.
    existing = await session.scalar(
        select(MailOutbox).where(
            MailOutbox.mailbox_id == mailbox.id,
            MailOutbox.request_id == body.request_id,
        )
    )
    settings = await mailbox_settings.config_da_caixa(session, mailbox.id)
    envelope = reply_envelope(
        mailbox, message, strict=settings.remetente_estrito, main_allowed=not settings.empresa
    )
    config = decrypt_json(mailbox.config_enc)
    if form_recipient is not None:
        own = {address.lower() for address in config["aliases"]}
        original_from = str(decrypt_json(message.content_enc).get("from_address") or "").lower()
        recipient = form_recipient.strip().lower()
        if (
            not settings.empresa
            or original_from not in own
            or not recipient
            or recipient in own
            or "@" not in recipient
        ):
            raise MailError("form_recipient_not_allowed")
        envelope = {
            **envelope,
            "to": recipient,
            "can_reply": message.direction == "inbound" and envelope["from_address"] is not None,
        }
    if subject_tag is not None and not FORM_TAG.match(subject_tag):
        raise MailError("invalid_subject_tag")
    sender = str(body.from_address or envelope["from_address"] or "")
    if not sender:
        raise MailError("no_receiving_alias")
    if settings.empresa and sender.lower() == str(config["address"]).lower():
        # Company mailbox: the main address (the Tuta login) never sends.
        raise MailError("main_address_not_allowed")
    if sender.lower() not in {address.lower() for address in config["aliases"]}:
        raise MailError("sender_not_authorized")
    if settings.remetente_estrito and sender.lower() not in envelope["receiving_aliases"]:
        raise MailError("sender_not_receiving_alias")
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
    # Company mailboxes: pause, test-mode recipients and rate limits are
    # checked when queueing (private mailboxes keep the rules above).
    blocked = await mailbox_settings.trava_de_envio(session, settings, envelope["to"])
    if blocked:
        raise MailError(blocked)
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
    if subject_tag and subject_tag.lower() not in subject.lower():
        # One "Re:" in front of the tag ("Re: [US-26-0014] ...").
        base = subject[3:].lstrip() if subject.lower().startswith("re:") else subject
        subject = f"Re: [{subject_tag}] {base}"[:990]
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
    # Company mailboxes: the job keeps the company rules (brake, pause, test
    # mode) at lease time even if the mailbox later goes back to private.
    await mailbox_settings.registrar_job(session, job, settings)
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
                MailOutbox.leased_at < now - LEASE_TIMEOUT,
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
            .limit(LEASE_SCAN)
            .with_for_update(skip_locked=True)
        )
    ).all()
    result = []
    config = decrypt_json(mailbox.config_enc) if jobs else {"address": "", "aliases": []}
    aliases = {address.lower() for address in config["aliases"]}
    main = str(config["address"]).lower()
    for job in jobs:
        if len(result) >= LEASE_BATCH:
            break
        # Recheck the author still has access before dispatching a queued reply.
        author = await session.get(User, job.author_user_id)
        if author is None or author.status.value != "active" or not allowed(mailbox, author):
            job.status = "failed"
            job.error_code = "author_access_revoked"
            job.completed_at = now
            continue
        payload = decrypt_json(job.content_enc)
        # Aliases can be edited after queueing: a removed sender never goes out.
        if payload["from_address"].lower() not in aliases:
            job.status = "failed"
            job.error_code = "sender_not_authorized"
            job.completed_at = now
            continue
        # Atendimento replies and company mailboxes: the global brake, a pause
        # or test mode switched on after queueing hold/fail the job here; one
        # queued for too long fails. Raw replies of a private mailbox: no-op.
        held = await mailbox_settings.trava_no_lease(
            session,
            job,
            payload.get("to"),
            agora=now,
            from_main=payload["from_address"].lower() == main,
        )
        if held == mailbox_settings.SEGURAR:
            continue
        if held:
            job.status = "failed"
            job.error_code = held
            job.completed_at = now
            continue
        token = secrets.token_urlsafe(32)
        job.lease_token_hash = token_hash(token)
        job.leased_at = now
        job.status = "leased"
        result.append({"id": str(job.id), "lease_token": token, **payload})
    await session.flush()
    return result


def resolve_uncertain(job: MailOutbox, user: User, *, sent: bool) -> None:
    """A person checked Tuta and says whether an ambiguous reply went out.

    Only `uncertain` jobs, or `leased` ones past the receipt timeout (the next
    lease would mark them uncertain), can be resolved. Nothing is resent: a
    resolved job just leaves the active set, so the message can be answered
    again if it did not go out. The decision is final and kept encrypted in
    `receipt_enc` with who made it; a late agent receipt then gets the usual
    409 receipt_conflict instead of overwriting it.
    """
    now = datetime.now(UTC)
    stale_lease = (
        job.status == "leased" and job.leased_at is not None and job.leased_at < now - LEASE_TIMEOUT
    )
    if job.status != "uncertain" and not stale_lease:
        raise MailError("job_not_uncertain")
    previous = {
        "status": job.status,
        "error_code": job.error_code,
        "receipt": decrypt_json(job.receipt_enc) if job.receipt_enc else None,
    }
    job.status = "sent" if sent else "failed"
    job.error_code = None if sent else "resolved_not_sent"
    job.completed_at = now
    job.receipt_enc = encrypt_json(
        {
            "status": job.status,
            "message_id": None,
            "error_code": job.error_code,
            "resolved_by": str(user.id),
            "resolved_at": now.isoformat(),
            "previous": previous,
        }
    )


def verify_receipt(job: MailOutbox, lease_token: str) -> None:
    if not job.lease_token_hash or not secrets.compare_digest(
        job.lease_token_hash,
        token_hash(lease_token),
    ):
        raise MailError("invalid_lease", 401)
    if job.status == "queued":
        raise MailError("job_not_leased")
