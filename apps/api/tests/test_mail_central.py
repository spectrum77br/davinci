"""Real local PostgreSQL + in-process HTTP; never a Tuta account or mail transport."""

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models import UserRole, UserStatus
from app.models.mail import MailAttachment, MailMailbox, MailMessage, MailOutbox
from app.security.cipher import decrypt_json

ROOT = "/api/mail"


@pytest.fixture
async def account(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    result = await client.post(
        f"{ROOT}/mailboxes",
        json={
            "label": "Private test inbox",
            "address": "owner@example.com",
            "aliases": ["support@example.com"],
        },
    )
    assert result.status_code == 201, result.text
    mailbox = result.json()
    return {
        "admin": admin,
        "mailbox": mailbox,
        "agent": f"{ROOT}/agent/{mailbox['id']}",
        "headers": {"Authorization": f"Bearer {mailbox['agent_token']}"},
    }


def message(**updates):
    result = {
        "source_id": "original-tuta-message-1",
        "folder": "INBOX",
        "direction": "inbound",
        "received_at": datetime.now(UTC).isoformat(),
        "subject": "Private customer subject",
        "from_address": "customer@example.com",
        "from_name": "Customer",
        "to": ["support@example.com"],
        "cc": [],
        "reply_to": "reply@example.com",
        "message_id": "<original@example.com>",
        "in_reply_to": None,
        "references": [],
        "text": "Private body <script>alert('not HTML')</script>",
        "attachments": [
            {
                "filename": "../../private.html",
                "content_type": "text/html",
                "data_base64": base64.b64encode(b"private attachment").decode(),
            }
        ],
    }
    return {**result, **updates}


async def ingest(client, account, **updates):
    result = await client.post(
        f"{account['agent']}/ingest",
        headers=account["headers"],
        json={"messages": [message(**updates)]},
    )
    assert result.status_code == 200, result.text
    listing = await client.get(f"{ROOT}/mailboxes/{account['mailbox']['id']}/messages")
    return listing.json()["items"][0]["id"]


async def enable(client, account):
    result = await client.patch(
        f"{ROOT}/mailboxes/{account['mailbox']['id']}", json={"send_enabled": True}
    )
    assert result.status_code == 200
    result = await client.post(
        f"{account['agent']}/heartbeat",
        headers=account["headers"],
        json={"state": "online", "can_send": True},
    )
    assert result.status_code == 200


async def queued(client, account):
    message_id = await ingest(client, account)
    await enable(client, account)
    body = {"request_id": str(uuid4()), "text": "A human reply"}
    result = await client.post(f"{ROOT}/messages/{message_id}/reply", json=body)
    assert result.status_code == 202, result.text
    return message_id, body, result.json()


async def claim(client, account):
    result = await client.post(
        f"{account['agent']}/outbox/lease", json={}, headers=account["headers"]
    )
    assert result.status_code == 200, result.text
    return result.json()["jobs"]


async def test_owner_and_admin_only_all_detail_paths(client, account, make_user, auth_as):
    message_id = await ingest(client, account)
    detail = (await client.get(f"{ROOT}/messages/{message_id}")).json()
    attachment_id = detail["attachments"][0]["id"]
    other = await make_user()
    auth_as(other)
    assert (await client.get(f"{ROOT}/mailboxes")).json() == {"items": []}
    for path in [
        f"/mailboxes/{account['mailbox']['id']}/messages",
        f"/messages/{message_id}",
        f"/attachments/{attachment_id}",
    ]:
        assert (await client.get(ROOT + path)).status_code == 404
    assert (
        await client.post(
            f"{ROOT}/messages/{message_id}/reply",
            json={
                "request_id": str(uuid4()),
                "text": "forbidden",
            },
        )
    ).status_code == 404
    assert (
        await client.post(
            f"{ROOT}/mailboxes",
            json={
                "label": "No",
                "address": "other@example.com",
            },
        )
    ).status_code == 403
    auth_as(None)
    assert (await client.get(f"{ROOT}/mailboxes")).status_code == 401


async def test_assigned_owner_can_read_without_team_wide_access(
    client,
    account,
    make_user,
    auth_as,
    db,
):
    owner = await make_user()
    mailbox = await db.get(MailMailbox, UUID(account["mailbox"]["id"]))
    mailbox.owner_user_id = owner.id
    await db.commit()
    auth_as(owner)
    assert len((await client.get(f"{ROOT}/mailboxes")).json()["items"]) == 1


async def test_encrypted_at_rest_dedup_and_safe_attachment(client, account, db):
    message_id = await ingest(client, account)
    await ingest(client, account)
    assert await db.scalar(select(func.count()).select_from(MailMessage)) == 1
    stored = await db.get(MailMessage, UUID(message_id))
    assert b"Private body" not in stored.content_enc
    assert b"customer@example.com" not in stored.content_enc
    assert decrypt_json(stored.content_enc)["subject"] == "Private customer subject"
    mailbox = await db.get(MailMailbox, UUID(account["mailbox"]["id"]))
    assert b"owner@example.com" not in mailbox.config_enc
    assert mailbox.agent_token_hash != account["mailbox"]["agent_token"]
    attachment = await db.scalar(select(MailAttachment))
    assert b"private attachment" not in attachment.content_enc
    result = await client.get(f"{ROOT}/attachments/{attachment.id}")
    assert result.content == b"private attachment"
    assert result.headers["content-type"] == "application/octet-stream"
    assert result.headers["content-disposition"] == "attachment; filename*=UTF-8''private.html"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert result.headers["cache-control"] == "no-store"


async def test_agent_token_scoped_and_rotatable(client, account):
    path = f"{account['agent']}/ingest"
    assert (await client.post(path, json={"messages": []})).status_code == 401
    wrong_box = f"{ROOT}/agent/{uuid4()}/ingest"
    assert (
        await client.post(wrong_box, headers=account["headers"], json={"messages": []})
    ).status_code == 401
    rotated = await client.post(f"{ROOT}/mailboxes/{account['mailbox']['id']}/token")
    assert rotated.status_code == 200
    assert (
        await client.post(path, headers=account["headers"], json={"messages": []})
    ).status_code == 401
    new_headers = {"Authorization": f"Bearer {rotated.json()['agent_token']}"}
    assert (await client.post(path, headers=new_headers, json={"messages": []})).status_code == 200


async def test_agent_limits_validate_before_echoing_sensitive_input(client, account):
    url = f"{account['agent']}/ingest"
    result = await client.post(
        url, headers={**account["headers"], "Content-Length": "30000000"}, content=b"{}"
    )
    assert result.status_code == 413
    for changes in [
        {"html": "SECRET HTML"},
        {"subject": "SECRET\r\nBcc:bad@example.com"},
        {"attachments": [{"filename": "secret", "data_base64": "SECRET_BAD"}]},
    ]:
        result = await client.post(
            url, headers=account["headers"], json={"messages": [message(**changes)]}
        )
        assert result.status_code == 422, result.text
        assert "SECRET" not in result.text


async def test_reply_is_explicit_and_requires_both_send_gates(client, account):
    message_id = await ingest(client, account)
    data = {"request_id": str(uuid4()), "text": "Human reply"}
    url = f"{ROOT}/messages/{message_id}/reply"
    assert (await client.post(url, json=data)).json()["detail"]["code"] == "sending_unavailable"
    await client.post(
        f"{account['agent']}/heartbeat",
        headers=account["headers"],
        json={"state": "online", "can_send": True},
    )
    assert (await client.post(url, json=data)).status_code == 409
    assert await claim(client, account) == []
    await enable(client, account)
    result = await client.post(url, json={**data, "from_address": "outsider@example.com"})
    assert result.json()["detail"]["code"] == "sender_not_authorized"


async def test_reply_idempotent_and_leased_once_with_reviewable_recipient(client, account, db):
    message_id, body, job = await queued(client, account)
    repeated = await client.post(f"{ROOT}/messages/{message_id}/reply", json=body)
    assert repeated.json()["id"] == job["id"]
    assert repeated.json()["to"] == "reply@example.com"
    assert repeated.json()["from_address"] == "support@example.com"
    changed = await client.post(
        f"{ROOT}/messages/{message_id}/reply", json={**body, "text": "changed"}
    )
    assert changed.json()["detail"]["code"] == "request_id_conflict"
    second = await client.post(
        f"{ROOT}/messages/{message_id}/reply", json={**body, "request_id": str(uuid4())}
    )
    assert second.json()["detail"]["code"] == "reply_already_pending"
    jobs = await claim(client, account)
    assert len(jobs) == 1
    assert jobs[0]["id"] == job["id"]
    assert jobs[0]["in_reply_to"] == "<original@example.com>"
    assert await claim(client, account) == []
    stored = await db.get(MailOutbox, UUID(job["id"]))
    assert b"A human reply" not in stored.content_enc


async def test_expired_lease_never_redispatches_and_late_receipt_can_confirm(client, account, db):
    _, _, job = await queued(client, account)
    lease = (await claim(client, account))[0]
    stored = await db.get(MailOutbox, UUID(job["id"]))
    stored.leased_at = datetime.now(UTC) - timedelta(minutes=16)
    await db.commit()
    assert await claim(client, account) == []
    await db.refresh(stored)
    assert stored.status == "uncertain"
    url = f"{account['agent']}/outbox/{job['id']}/receipt"
    receipt = {"lease_token": lease["lease_token"], "status": "sent", "message_id": "<sent>"}
    assert (await client.post(url, headers=account["headers"], json=receipt)).status_code == 200
    assert (await client.post(url, headers=account["headers"], json=receipt)).status_code == 200
    assert (
        await client.post(url, headers=account["headers"], json={**receipt, "status": "failed"})
    ).status_code == 409


async def test_receipt_cannot_confirm_other_lease_or_mailbox(client, account):
    _, _, job = await queued(client, account)
    lease = (await claim(client, account))[0]
    url = f"{account['agent']}/outbox/{job['id']}/receipt"
    receipt = {"lease_token": "wrong-token-of-at-least-20-characters", "status": "sent"}
    assert (await client.post(url, headers=account["headers"], json=receipt)).status_code == 401
    receipt["lease_token"] = lease["lease_token"]
    assert (
        await client.post(
            url.replace(job["id"], str(uuid4())), headers=account["headers"], json=receipt
        )
    ).status_code == 404


async def test_revoked_author_and_disabled_or_stale_worker_do_not_send(client, account, db):
    _, _, job = await queued(client, account)
    mailbox = await db.get(MailMailbox, UUID(account["mailbox"]["id"]))
    mailbox.last_seen_at = datetime.now(UTC) - timedelta(minutes=4)
    await db.commit()
    assert await claim(client, account) == []
    await enable(client, account)
    account["admin"].status = UserStatus.SUSPENDED
    await db.commit()
    assert await claim(client, account) == []
    stored = await db.get(MailOutbox, UUID(job["id"]))
    assert stored.status == "failed"
    assert stored.error_code == "author_access_revoked"


async def test_sent_mail_cannot_trigger_a_reply(client, account):
    message_id = await ingest(client, account, direction="sent")
    await enable(client, account)
    result = await client.post(
        f"{ROOT}/messages/{message_id}/reply",
        json={
            "request_id": str(uuid4()),
            "text": "No",
        },
    )
    assert result.json()["detail"]["code"] == "message_not_replyable"


async def test_concurrent_replays_and_reply_clicks_create_single_rows(client, account, db):
    url = f"{account['agent']}/ingest"
    payload = {"messages": [message(to=["SUPPORT@example.com"])]}
    responses = await asyncio.gather(
        *[client.post(url, headers=account["headers"], json=payload) for _ in range(3)]
    )
    assert [response.status_code for response in responses] == [200, 200, 200]
    assert sum(response.json()["accepted"] for response in responses) == 1
    listing = (await client.get(f"{ROOT}/mailboxes/{account['mailbox']['id']}/messages")).json()
    message_id = listing["items"][0]["id"]
    detail = (await client.get(f"{ROOT}/messages/{message_id}")).json()
    assert detail["reply"]["from_address"] == "support@example.com"
    await enable(client, account)
    payload = {"request_id": str(uuid4()), "text": "One click replayed"}
    responses = await asyncio.gather(
        *[client.post(f"{ROOT}/messages/{message_id}/reply", json=payload) for _ in range(3)]
    )
    assert [response.status_code for response in responses] == [202, 202, 202]
    assert len({response.json()["id"] for response in responses}) == 1
    assert await db.scalar(select(func.count()).select_from(MailOutbox)) == 1


async def test_chunked_agent_body_is_bounded_without_content_length(client, account):
    async def stream():
        yield b"x" * 4096
        yield b"x"

    result = await client.post(
        f"{account['agent']}/heartbeat", headers=account["headers"], content=stream()
    )
    assert result.status_code == 413
