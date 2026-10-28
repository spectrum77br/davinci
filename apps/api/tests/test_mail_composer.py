# ruff: noqa: F401, F811
"""Local database and in-process HTTP only. Never contacts a mail service."""

import base64
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.config import get_settings
from app.models import UserRole
from app.models.mail import MailDraft, MailMailbox, MailOutbox
from app.models.mail_admin import MailAcesso
from app.models.mail_atendimento import MailMailboxSettings
from app.schemas.mail import ComposeDraftIn, ComposeSendIn
from app.security.cipher import decrypt_json
from tests.test_mail_central import ROOT, account, claim, enable, ingest


def compose(**changes):
    return {
        "request_id": str(uuid4()),
        "action": "new",
        "from_address": "support@example.com",
        "to": ["customer@example.com"],
        "cc": [],
        "bcc": [],
        "subject": "Assunto",
        "text": "Mensagem escrita pela pessoa",
        "attachments": [],
        **changes,
    }


def file_data(raw=b"private contents", filename="comprovante.pdf"):
    return {
        "filename": filename,
        "content_type": "application/pdf",
        "data_base64": base64.b64encode(raw).decode(),
    }


async def send(client, account, body):
    return await client.post(f"{ROOT}/mailboxes/{account['mailbox']['id']}/send", json=body)


async def company(db, account, monkeypatch, **changes):
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "")
    cfg = MailMailboxSettings(
        mailbox_id=UUID(account["mailbox"]["id"]),
        visibilidade="empresa",
        envio_modo="real",
        **changes,
    )
    db.add(cfg)
    await db.commit()
    return cfg


async def test_new_mail_attachments_cc_bcc_idempotency_and_private_history(client, account, db):
    await enable(client, account)
    body = compose(cc=["copy@example.com"], bcc=["hidden@example.com"], attachments=[file_data()])
    first = await send(client, account, body)
    assert first.status_code == 202, first.text
    assert first.json()["source_message_id"] is None
    again = await send(client, account, body)
    assert again.json()["id"] == first.json()["id"]
    for field, value in (
        ("bcc", ["another@example.com"]),
        ("subject", "Changed"),
        ("attachments", [file_data(b"changed bytes")]),
    ):
        changed = await send(client, account, {**body, field: value})
        assert changed.status_code == 409
        assert changed.json()["detail"]["code"] == "request_id_conflict"
    job = await db.get(MailOutbox, UUID(first.json()["id"]))
    assert b"private contents" not in job.content_enc
    [leased] = await claim(client, account)
    assert leased["to"] == body["to"] and leased["cc"] == body["cc"]
    assert leased["bcc"] == body["bcc"] and leased["attachments"] == body["attachments"]
    listing = await client.get(f"{ROOT}/mailboxes/{job.mailbox_id}/outbox")
    item = listing.json()["items"][0]
    assert item["bcc"] == body["bcc"]
    assert "data_base64" not in str(item)


async def test_reply_all_context_threading_and_strict_sender(client, account, db, monkeypatch):
    mid = await ingest(
        client,
        account,
        to=["support@example.com", "copy@example.com"],
        cc=["copy@example.com", "another@example.com", "owner@example.com"],
    )
    await enable(client, account)
    await company(db, account, monkeypatch, remetente_estrito=True)
    context = await client.get(
        f"{ROOT}/mailboxes/{account['mailbox']['id']}/compose-context",
        params={"source_message_id": mid, "action": "reply_all"},
    )
    assert context.status_code == 200, context.text
    ctx = context.json()
    assert ctx["allowed_from_addresses"] == ["support@example.com"]
    assert ctx["reply_all_to"] == ["reply@example.com", "copy@example.com"]
    assert ctx["reply_all_cc"] == ["another@example.com"]
    body = compose(
        action="reply_all", source_message_id=mid, to=ctx["reply_all_to"], cc=ctx["reply_all_cc"]
    )
    rejected = await send(client, account, {**body, "from_address": "owner@example.com"})
    assert rejected.status_code == 409
    accepted = await send(client, account, body)
    assert accepted.status_code == 202, accepted.text
    [job] = await claim(client, account)
    assert job["in_reply_to"] == "<original@example.com>"
    assert job["references"] == ["<original@example.com>"]


async def test_forward_attachment_must_belong_to_source(client, account):
    mid = await ingest(client, account)
    source = (await client.get(f"{ROOT}/messages/{mid}")).json()
    aid = source["attachments"][0]["id"]
    other = await ingest(client, account, source_id="other", message_id="<other@example.com>")
    await enable(client, account)
    body = compose(action="forward", source_message_id=other, source_attachment_ids=[aid])
    denied = await send(client, account, body)
    assert denied.status_code == 404
    body["source_message_id"] = mid
    accepted = await send(client, account, body)
    assert accepted.status_code == 202, accepted.text
    [job] = await claim(client, account)
    assert job["in_reply_to"] is None and job["references"] == []
    assert job["attachments"][0]["filename"] == "private.html"
    assert base64.b64decode(job["attachments"][0]["data_base64"]) == b"private attachment"


async def test_cc_bcc_test_mode_enforced_at_queue_and_again_at_lease(
    client, account, db, monkeypatch
):
    cfg = await company(db, account, monkeypatch)
    cfg.envio_modo = "teste"
    cfg.destinatarios_teste = ["customer@example.com"]
    await db.commit()
    await enable(client, account)
    for field in ("cc", "bcc"):
        rejected = await send(client, account, compose(**{field: ["outside@example.com"]}))
        assert rejected.json()["detail"]["code"] == "test_mode_recipient"
    cfg.envio_modo = "real"
    await db.commit()
    accepted = await send(client, account, compose(bcc=["outside@example.com"]))
    assert accepted.status_code == 202
    cfg.envio_modo = "teste"
    await db.commit()
    assert await claim(client, account) == []
    await db.refresh(await db.get(MailOutbox, UUID(accepted.json()["id"])))
    job = await db.get(MailOutbox, UUID(accepted.json()["id"]))
    assert job.status == "failed" and job.error_code == "test_mode_recipient"


async def test_drafts_are_encrypted_private_and_allow_incomplete_body(
    client, account, db, make_user, auth_as
):
    url = f"{ROOT}/mailboxes/{account['mailbox']['id']}/drafts"
    created = await client.post(
        url, json={"subject": "Private draft", "attachments": [file_data()]}
    )
    assert created.status_code == 201, created.text
    draft_id = created.json()["id"]
    draft = await db.get(MailDraft, UUID(draft_id))
    assert b"Private draft" not in draft.content_enc
    assert decrypt_json(draft.content_enc)["to"] == []
    listing = (await client.get(url)).json()["items"]
    assert listing[0]["subject"] == "Private draft" and "attachments" not in listing[0]
    other_admin = await make_user(role=UserRole.ADMIN)
    auth_as(other_admin)
    assert (await client.get(url)).json() == {"items": []}
    for method in ("get", "patch", "delete"):
        response = await getattr(client, method)(
            f"{ROOT}/drafts/{draft_id}", **({"json": {}} if method == "patch" else {})
        )
        assert response.status_code == 404
    auth_as(account["admin"])
    edited = await client.patch(f"{ROOT}/drafts/{draft_id}", json={"text": "Saved again"})
    assert edited.status_code == 200 and edited.json()["text"] == "Saved again"
    assert (await client.delete(f"{ROOT}/drafts/{draft_id}")).status_code == 204
    assert (await client.get(f"{ROOT}/drafts/{draft_id}")).status_code == 404


async def test_attachment_lease_batch_and_revoked_author(client, account, db, make_user, auth_as):
    await enable(client, account)
    for _ in range(2):
        assert (await send(client, account, compose(attachments=[file_data()]))).status_code == 202
    assert len(await claim(client, account)) == 1
    assert len(await claim(client, account)) == 1
    accepted = await send(client, account, compose())
    author = account["admin"]
    from app.models import UserStatus

    author.status = UserStatus.SUSPENDED
    await db.commit()
    assert await claim(client, account) == []
    job = await db.get(MailOutbox, UUID(accepted.json()["id"]))
    await db.refresh(job)
    assert job.error_code == "author_access_revoked"


async def test_new_uncertain_mail_is_resolved_only_by_its_author(
    client, account, make_user, auth_as
):
    await enable(client, account)
    accepted = await send(client, account, compose(text="", attachments=[file_data()]))
    assert accepted.status_code == 202
    [job] = await claim(client, account)
    receipt = await client.post(
        f"{account['agent']}/outbox/{job['id']}/receipt",
        headers=account["headers"],
        json={"lease_token": job["lease_token"], "status": "uncertain"},
    )
    assert receipt.status_code == 200
    other = await make_user(role=UserRole.ADMIN)
    auth_as(other)
    url = f"{ROOT}/outbox/{job['id']}/resolve"
    assert (await client.post(url, json={"saiu": False})).status_code == 404
    auth_as(account["admin"])
    resolved = await client.post(url, json={"saiu": False})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "failed"
    assert await claim(client, account) == []


async def test_filtered_grant_revokes_composer_and_pending_new_mail(client, account, db):
    await enable(client, account)
    accepted = await send(client, account, compose())
    assert accepted.status_code == 202
    # Administrators intentionally bypass mailbox filters. Keep ownership but
    # remove admin status before applying the limited message grant.
    account["admin"].role = UserRole.USER
    db.add(
        MailAcesso(
            mailbox_id=UUID(account["mailbox"]["id"]), user_id=account["admin"].id, modo="padrao"
        )
    )
    await db.commit()
    context = await client.get(f"{ROOT}/mailboxes/{account['mailbox']['id']}/compose-context")
    assert context.status_code == 200
    assert context.json()["can_compose"] is False
    assert context.json()["allowed_from_addresses"] == []
    rejected = await send(client, account, compose())
    assert rejected.status_code == 403
    assert await claim(client, account) == []
    job = await db.get(MailOutbox, UUID(accepted.json()["id"]))
    await db.refresh(job)
    assert job.error_code == "author_access_revoked"


async def test_combined_forwarded_and_uploaded_attachment_size(client, account, monkeypatch):
    from app.services import mail_composer

    mid = await ingest(client, account)
    source = (await client.get(f"{ROOT}/messages/{mid}")).json()
    aid = source["attachments"][0]["id"]
    await enable(client, account)
    monkeypatch.setattr(mail_composer, "MAX_ATTACHMENT_BYTES", 100)
    body = compose(
        action="forward",
        source_message_id=mid,
        source_attachment_ids=[aid],
        attachments=[file_data(b"a" * 90)],
    )
    rejected = await send(client, account, body)
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "attachments_too_large"


def test_compose_limits_and_no_header_injection():
    assert ComposeDraftIn().text == ""
    with pytest.raises(ValidationError):
        ComposeSendIn.model_validate(compose(to=[], text=""))
    assert ComposeSendIn.model_validate(compose(to=[], bcc=["hidden@example.com"]))
    for filename in ("../secret.pdf", "a\\b.pdf", "a\x01b.pdf", "  ", "a\x7fb.pdf"):
        with pytest.raises(ValidationError):
            ComposeDraftIn(attachments=[file_data(filename=filename)])
    with pytest.raises(ValidationError):
        ComposeSendIn.model_validate(compose(subject="Hello\r\nBcc: x@example.com"))
    with pytest.raises(ValidationError):
        ComposeDraftIn(attachments=[{**file_data(), "content_type": "multipart/mixed"}])
    with pytest.raises(ValidationError):
        ComposeSendIn.model_validate(compose(to=["josé@example.com"]))
    normalized = ComposeSendIn.model_validate(compose(to=["user@bücher.de"]))
    assert normalized.to == ["user@xn--bcher-kva.de"]
    normalized = ComposeSendIn.model_validate(compose(to=["user@faß.de"]))
    assert normalized.to == ["user@xn--fa-hia.de"]
    with pytest.raises(ValidationError):
        ComposeDraftIn(attachments=[file_data(b"a" * (8 * 1024 * 1024))] * 2)
    with pytest.raises(ValidationError):
        ComposeDraftIn(
            action="forward",
            source_message_id=uuid4(),
            attachments=[file_data()] * 10,
            source_attachment_ids=[uuid4()],
        )
