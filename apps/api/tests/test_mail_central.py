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
        {"html": {"SECRET": "invalid type"}},
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


async def test_html_is_encrypted_private_and_returned_as_inert_json(
    client, account, db, make_user, auth_as
):
    html = '<p>Olá <img src="cid:Logo@Tuta"></p><script>never-execute()</script>'
    part = {
        "filename": "logo.png",
        "content_type": "image/png",
        "data_base64": base64.b64encode(b"private image").decode(),
        "content_id": " <Logo@Tuta> ",
        "disposition": "inline",
    }
    mid = await ingest(client, account, html=html, attachments=[part])
    result = await client.get(f"{ROOT}/messages/{mid}")
    assert result.headers["content-type"].startswith("application/json")
    detail = result.json()
    assert detail["html"] == html
    assert detail["text"] == message()["text"]
    assert detail["attachments"][0]["content_id"] == "Logo@Tuta"
    assert detail["attachments"][0]["disposition"] == "inline"
    stored = await db.get(MailMessage, UUID(mid))
    assert html.encode() not in stored.content_enc
    assert decrypt_json(stored.content_enc)["html"] == html
    attachment = await db.scalar(select(MailAttachment))
    assert b"Logo@Tuta" not in attachment.metadata_enc
    listing = await client.get(f"{ROOT}/mailboxes/{account['mailbox']['id']}/messages")
    assert "html" not in listing.json()["items"][0]
    other = await make_user()
    auth_as(other)
    assert (await client.get(f"{ROOT}/messages/{mid}")).status_code == 404
    assert (await client.get(f"{ROOT}/attachments/{attachment.id}")).status_code == 404


async def test_presentation_repair_is_idempotent_keeps_ids_and_reply_state(
    client, account, db, monkeypatch
):
    from unittest.mock import AsyncMock

    from app.services.mail_atendimento import ponte

    process = AsyncMock()
    monkeypatch.setattr(ponte, "processar", process)
    mid, _, _ = await queued(client, account)
    before = (await client.get(f"{ROOT}/messages/{mid}")).json()
    old_part = {**message()["attachments"][0], "content_id": "old-part", "disposition": "inline"}
    new_part = {
        "filename": "Logo.PNG",
        "content_type": "image/png",
        "data_base64": base64.b64encode(b"new private image").decode(),
        "content_id": "new-part",
        "disposition": "inline",
    }
    repaired = message(
        html='<p><img src="cid:new-part"></p>',
        text="must not overwrite text",
        folder="MOVED",
        direction="sent",
        received_at=(datetime.now(UTC) - timedelta(days=365)).isoformat(),
        to=["unrelated@example.com"],
        attachments=[old_part, new_part],
    )
    body = {"messages": [repaired], "enrich_existing_only": True}
    url = f"{account['agent']}/ingest"
    result = await client.post(url, headers=account["headers"], json=body)
    assert result.status_code == 200, result.text
    assert result.json() == {"accepted": 0, "duplicates": 1, "updated": 1}
    after = (await client.get(f"{ROOT}/messages/{mid}")).json()
    for key in before.keys() - {"html", "attachments", "has_attachments"}:
        assert after[key] == before[key], key
    assert len(after["attachments"]) == 2
    original = next(a for a in after["attachments"] if a["content_id"] == "old-part")
    assert original["id"] == before["attachments"][0]["id"]
    again = await client.post(url, headers=account["headers"], json=body)
    assert again.json() == {"accepted": 0, "duplicates": 1}
    detail = (await client.get(f"{ROOT}/messages/{mid}")).json()
    assert {a["id"] for a in detail["attachments"]} == {a["id"] for a in after["attachments"]}
    assert await db.scalar(select(func.count()).select_from(MailMessage)) == 1
    assert await db.scalar(select(func.count()).select_from(MailOutbox)) == 1
    process.assert_not_awaited()


async def test_repair_missing_source_never_creates_and_batch_is_atomic(client, account, db):
    mid = await ingest(client, account)
    before = (await client.get(f"{ROOT}/messages/{mid}")).json()
    result = await client.post(
        f"{account['agent']}/ingest",
        headers=account["headers"],
        json={
            "enrich_existing_only": True,
            "messages": [
                message(html="<p>Valid repair</p>"),
                message(source_id="missing-message", html="<p>Never insert</p>"),
            ],
        },
    )
    assert result.status_code == 404, result.text
    assert result.json()["detail"]["code"] == "mail_enrichment_source_not_found"
    assert (await client.get(f"{ROOT}/messages/{mid}")).json() == before
    assert await db.scalar(select(func.count()).select_from(MailMessage)) == 1


@pytest.mark.parametrize(
    "changed",
    [
        {"from_address": "someoneelse@example.com"},
        {"subject": "different message"},
        {"message_id": "<different@example.com>"},
    ],
)
async def test_repair_rejects_identity_conflict(client, account, changed):
    mid = await ingest(client, account)
    before = (await client.get(f"{ROOT}/messages/{mid}")).json()
    result = await client.post(
        f"{account['agent']}/ingest",
        headers=account["headers"],
        json={"enrich_existing_only": True, "messages": [message(html="<p>repair</p>", **changed)]},
    )
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "mail_enrichment_identity_conflict"
    assert (await client.get(f"{ROOT}/messages/{mid}")).json() == before


async def test_html_byte_limit_and_invalid_cid_do_not_echo_input(client, account):
    url = f"{account['agent']}/ingest"
    part = message()["attachments"][0]
    changes = [
        {"html": "á" * (1024 * 1024 + 1)},
        {"attachments": [{**part, "disposition": "SECRET_invalid"}]},
        {"attachments": [{**part, "content_id": "same"}, {**part, "content_id": "same"}]},
    ]
    for cid in ["SECRET space", "SECRET\n", "<SECRET>trailing", "é", "x" * 256, ""]:
        changes.append({"attachments": [{**part, "content_id": cid}]})
    for change in changes:
        result = await client.post(
            url, headers=account["headers"], json={"messages": [message(**change)]}
        )
        assert result.status_code == 422, (change.keys(), result.text)
        assert "SECRET" not in result.text
    result = await client.post(
        url,
        headers=account["headers"],
        json={"messages": [message(html="á" * (1024 * 1024), attachments=[])]},
    )
    assert result.status_code == 200, result.text


async def test_repair_never_replaces_html_or_reassigns_existing_cid(client, account):
    part = {**message()["attachments"][0], "content_id": "existing", "disposition": "inline"}
    mid = await ingest(client, account, html="<p>Original</p>", attachments=[part])
    before = (await client.get(f"{ROOT}/messages/{mid}")).json()
    for changed, code in [
        ({"html": "<p>Replacement</p>", "attachments": [part]}, "mail_enrichment_html_conflict"),
        (
            {"html": "<p>Original</p>", "attachments": [{**part, "content_id": "different"}]},
            "mail_enrichment_attachment_conflict",
        ),
    ]:
        result = await client.post(
            f"{account['agent']}/ingest",
            headers=account["headers"],
            json={"messages": [message(**changed)], "enrich_existing_only": True},
        )
        assert result.status_code == 409, result.text
        assert result.json()["detail"]["code"] == code
        assert (await client.get(f"{ROOT}/messages/{mid}")).json() == before


async def test_repair_adds_only_missing_inline_parts_and_checks_content(client, account):
    mid = await ingest(client, account)
    before = (await client.get(f"{ROOT}/messages/{mid}")).json()
    changed = {
        **message()["attachments"][0],
        "content_id": "cid",
        "disposition": "inline",
        "data_base64": base64.b64encode(b"changed bytes").decode(),
    }
    for part, code in [
        (changed, "mail_enrichment_attachment_conflict"),
        (
            {**changed, "filename": "new", "disposition": "attachment"},
            "mail_enrichment_attachment_not_inline",
        ),
    ]:
        result = await client.post(
            f"{account['agent']}/ingest",
            headers=account["headers"],
            json={
                "enrich_existing_only": True,
                "messages": [message(html="<p>repair</p>", attachments=[part])],
            },
        )
        assert result.status_code == 409
        assert result.json()["detail"]["code"] == code
        assert (await client.get(f"{ROOT}/messages/{mid}")).json() == before


async def test_repair_conflict_rolls_back_entire_batch_and_valid_batch_ack(client, account):
    first = await ingest(client, account)
    second = await ingest(client, account, source_id="second")
    url = f"{account['agent']}/ingest"
    before = (await client.get(f"{ROOT}/messages/{first}")).json()
    result = await client.post(
        url,
        headers=account["headers"],
        json={
            "enrich_existing_only": True,
            "messages": [
                message(html="<p>first</p>"),
                message(source_id="second", html="<p>second</p>", subject="different"),
            ],
        },
    )
    assert result.status_code == 409
    assert (await client.get(f"{ROOT}/messages/{first}")).json() == before
    result = await client.post(
        url,
        headers=account["headers"],
        json={
            "enrich_existing_only": True,
            "messages": [
                message(html="<p>first</p>"),
                message(source_id="second", html="<p>second</p>"),
            ],
        },
    )
    assert result.json() == {"accepted": 0, "duplicates": 2, "updated": 2}
    assert (await client.get(f"{ROOT}/messages/{second}")).json()["html"] == "<p>second</p>"


async def test_repair_attachment_fingerprint_preserves_exact_filename_and_ids(client, account):
    part = {"filename": "Logo.PNG", "content_type": "image/png", "data_base64": "aW1hZ2U="}
    mid = await ingest(client, account, attachments=[part, part])
    before = (await client.get(f"{ROOT}/messages/{mid}")).json()
    repaired = [
        dict(part, content_id="One", disposition="inline"),
        dict(part, content_id="Two", disposition="inline"),
    ]
    body = {
        "messages": [message(html="<p>two parts</p>", attachments=repaired)],
        "enrich_existing_only": True,
    }
    for expected in [1, 0]:
        result = await client.post(
            f"{account['agent']}/ingest", headers=account["headers"], json=body
        )
        assert result.status_code == 200, result.text
        assert result.json().get("updated", 0) == expected
        after = (await client.get(f"{ROOT}/messages/{mid}")).json()
        assert len(after["attachments"]) == 2
        assert {a["id"] for a in after["attachments"]} == {a["id"] for a in before["attachments"]}
        assert {a["filename"] for a in after["attachments"]} == {"Logo.PNG"}
        assert {a["content_id"] for a in after["attachments"]} == {"One", "Two"}


async def test_repair_attachment_limit_includes_preexisting_parts(client, account):
    parts = [dict(message()["attachments"][0], filename=f"part-{i}") for i in range(10)]
    mid = await ingest(client, account, attachments=parts)
    before = (await client.get(f"{ROOT}/messages/{mid}")).json()
    part = dict(parts[0], filename="new", content_id="new", disposition="inline")
    result = await client.post(
        f"{account['agent']}/ingest",
        headers=account["headers"],
        json={
            "enrich_existing_only": True,
            "messages": [message(html="<p>repair</p>", attachments=[part])],
        },
    )
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "mail_enrichment_attachment_limit"
    assert (await client.get(f"{ROOT}/messages/{mid}")).json() == before
