"""Complete inventories move existing mail; incomplete inputs change nothing."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.models import UserRole
from app.models.mail import MailAttachment, MailMessage
from app.models.mail_atendimento import MailCaixaIndice, MailFolder, MailMessageMeta
from app.models.mail_bridge import MailBridgeIdentity
from app.security.cipher import decrypt_json

BASE_FOLDERS = [
    {"id": "in", "name": "INBOX", "kind": "inbox"},
    {"id": "trash", "name": "Trash", "kind": "trash"},
    {"id": "archive", "name": "Archive", "kind": "archive"},
    {"id": "info", "name": "informativo", "kind": "custom"},
]
HEADER = "<list.element@tutabridge.local>"


@pytest.fixture
async def account(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post(
        "/api/mail/mailboxes", json={"label": "Bridge", "address": "test@example.com"}
    )
    assert r.status_code == 201
    b = r.json()
    return {
        "id": UUID(b["id"]),
        "url": f"/api/mail/agent/{b['id']}",
        "headers": {"Authorization": f"Bearer {b['agent_token']}"},
    }


async def ingest(client, account, *, header=HEADER, source="imap:10:1", folder="INBOX"):
    r = await client.post(
        account["url"] + "/ingest",
        headers=account["headers"],
        json={
            "messages": [
                {
                    "source_id": source,
                    "folder": folder,
                    "received_at": datetime.now(UTC).isoformat(),
                    "from_address": "sender@example.com",
                    "to": ["test@example.com"],
                    "message_id": header,
                    "subject": "Unchanged",
                    "text": "Keep original content",
                    "attachments": [
                        {
                            "filename": "keep.txt",
                            "content_type": "text/plain",
                            "data_base64": "a2VlcA==",
                        }
                    ],
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def snapshot(*, folder="info", header=HEADER, folders=None, messages=None, seconds=0):
    return {
        "version": 1,
        "complete": True,
        "observed_at": (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(),
        "folders": folders or BASE_FOLDERS,
        "messages": messages
        if messages is not None
        else [{"message_id": header, "folder_id": folder}],
    }


async def reconcile(client, account, data):
    return await client.post(account["url"] + "/reconcile", headers=account["headers"], json=data)


async def stored(db, account):
    return (
        await db.scalars(
            select(MailMessage)
            .execution_options(populate_existing=True)
            .where(MailMessage.mailbox_id == account["id"])
            .order_by(MailMessage.created_at)
        )
    ).all()


@pytest.mark.asyncio
async def test_move_rename_delete_restore_preserve_content_and_index(client, account, db):
    await ingest(client, account)
    first = (await stored(db, account))[0]
    original_id, original_source = first.id, first.source_id
    r = await reconcile(client, account, snapshot())
    assert r.status_code == 200, r.text
    assert r.json()["complete"] is True
    assert r.json()["identities_changed"] == 1
    first = (await stored(db, account))[0]
    content = decrypt_json(first.content_enc)
    assert content["folder"] == "informativo"
    assert content["text"] == "Keep original content"
    assert first.id == original_id and first.source_id == original_source
    assert (
        await db.scalar(
            select(func.count())
            .select_from(MailAttachment)
            .where(MailAttachment.message_id == first.id)
        )
        == 1
    )
    index = await db.get(MailCaixaIndice, first.id)
    assert index.fonte_em == first.updated_at
    assert (
        await db.scalar(
            select(func.count())
            .select_from(MailFolder)
            .where(MailFolder.mailbox_id == account["id"])
        )
        == 0
    )
    renamed = [dict(f, name="avisos") if f["id"] == "info" else f for f in BASE_FOLDERS]
    assert (
        await reconcile(client, account, snapshot(folders=renamed, seconds=1))
    ).status_code == 200
    assert decrypt_json((await stored(db, account))[0].content_enc)["folder"] == "avisos"
    assert (
        await reconcile(client, account, snapshot(folders=renamed, messages=[], seconds=2))
    ).status_code == 200
    c = decrypt_json((await stored(db, account))[0].content_enc)
    assert c["folder"] == "Trash" and c["tuta"]["folder_kind"] == "3"
    assert c["bridge_previous_folder"] == "avisos"
    assert (
        await reconcile(client, account, snapshot(folders=renamed, seconds=3))
    ).status_code == 200
    c = decrypt_json((await stored(db, account))[0].content_enc)
    assert c["folder"] == "avisos" and "bridge_previous_folder" not in c


@pytest.mark.asyncio
async def test_baseline_preserves_historical_and_removed_folder_archives_it(client, account, db):
    await ingest(client, account, header="<list.historical@tutabridge.local>", folder="informativo")
    assert (await reconcile(client, account, snapshot(messages=[]))).status_code == 200
    assert decrypt_json((await stored(db, account))[0].content_enc)["folder"] == "informativo"
    renamed = [dict(f, name="avisos") if f["id"] == "info" else f for f in BASE_FOLDERS]
    assert (
        await reconcile(client, account, snapshot(folders=renamed, messages=[], seconds=1))
    ).status_code == 200
    assert decrypt_json((await stored(db, account))[0].content_enc)["folder"] == "avisos"
    assert (
        await reconcile(client, account, snapshot(folders=BASE_FOLDERS[:3], messages=[], seconds=2))
    ).status_code == 200
    c = decrypt_json((await stored(db, account))[0].content_enc)
    assert c["folder"] == "Archive" and c["bridge_previous_folder"] == "avisos"
    assert len(await stored(db, account)) == 1


@pytest.mark.asyncio
async def test_snapshot_deduplicates_new_source_but_preserves_legacy_duplicates(
    client, account, db
):
    await ingest(client, account)
    # Legacy duplicate from before identities were introduced, keep its UUID/body.
    original = (await stored(db, account))[0]
    duplicate = MailMessage(
        mailbox_id=account["id"],
        source_id="imap:10:2",
        direction="inbound",
        received_at=original.received_at,
        content_enc=original.content_enc,
    )
    db.add(duplicate)
    await db.commit()
    assert (await reconcile(client, account, snapshot())).status_code == 200
    new_source = "tuta-bridge:" + hashlib.sha256(HEADER.encode()).hexdigest()
    result = await ingest(client, account, source=new_source, folder="informativo")
    assert result["accepted"] == 0 and result["duplicates"] == 1
    rows = await stored(db, account)
    assert len(rows) == 2
    assert all(decrypt_json(r.content_enc)["folder"] == "informativo" for r in rows)
    identities = (
        await db.scalars(
            select(MailBridgeIdentity).where(MailBridgeIdentity.mailbox_id == account["id"])
        )
    ).all()
    assert len(identities) == 1


@pytest.mark.asyncio
async def test_partial_unknown_duplicate_stale_inventory_are_atomic(client, account, db):
    await ingest(client, account)
    baseline = snapshot()
    assert (await reconcile(client, account, baseline)).status_code == 200
    before = (await stored(db, account))[0].content_enc
    invalids = [
        dict(snapshot(), complete=False),
        snapshot(folder="unknown"),
        snapshot(messages=[{"message_id": HEADER, "folder_id": "in"}] * 2),
    ]
    for invalid in invalids:
        r = await reconcile(client, account, invalid)
        assert r.status_code == 422, r.text
    r = await reconcile(client, account, baseline)
    assert r.status_code == 409
    r = await reconcile(client, account, snapshot(seconds=900))
    assert r.status_code == 409
    assert (await stored(db, account))[0].content_enc == before


@pytest.mark.asyncio
async def test_new_stable_ingest_is_idempotent_and_scoped(client, account, db, make_user, auth_as):
    source = "tuta-bridge:" + hashlib.sha256(HEADER.encode()).hexdigest()
    await reconcile(client, account, snapshot(messages=[]))
    assert (await ingest(client, account, source=source, folder="informativo"))["accepted"] == 1
    assert (await ingest(client, account, source=source, folder="informativo"))["duplicates"] == 1
    row = (await stored(db, account))[0]
    assert decrypt_json(row.content_enc)["tuta"]["folder_kind"] == "0"
    other = await client.post(
        "/api/mail/mailboxes", json={"label": "Other", "address": "other@example.com"}
    )
    b = other.json()
    second = {
        "url": f"/api/mail/agent/{b['id']}",
        "headers": {"Authorization": f"Bearer {b['agent_token']}"},
    }
    assert (await ingest(client, second, source=source))["accepted"] == 1
    denied = await client.post(
        second["url"] + "/reconcile", headers=account["headers"], json=snapshot()
    )
    assert denied.status_code == 401


@pytest.mark.asyncio
async def test_recurring_unchanged_snapshot_does_not_decrypt_mail_bodies(
    client, account, db, monkeypatch
):
    from app.services import mail_bridge_sync

    await ingest(client, account)
    assert (await reconcile(client, account, snapshot())).status_code == 200
    ciphertext = (await stored(db, account))[0].content_enc
    calls = []
    original = mail_bridge_sync.decrypt_json

    def observe(value):
        assert value != ciphertext, "Unchanged mailbox body should not be decrypted again"
        calls.append(value)
        return original(value)

    monkeypatch.setattr(mail_bridge_sync, "decrypt_json", observe)
    result = await reconcile(client, account, snapshot(seconds=1))
    assert result.status_code == 200, result.text
    assert result.json()["updated"] == 0 and len(calls) == 1
    assert result.json()["identities_changed"] == 0


@pytest.mark.asyncio
async def test_public_manual_folder_rename_preserves_routing_and_private_meta(client, account, db):
    await ingest(client, account, folder="informativo")
    row = (await stored(db, account))[0]
    public = MailFolder(
        mailbox_id=account["id"],
        chave="informativo",
        nome="informativo",
        tipo_tuta="0",
        plataforma_manual="ml",
        ler="corpo",
        revisada=True,
    )
    db.add(public)
    await db.flush()
    meta = MailMessageMeta(
        message_id=row.id,
        mailbox_id=account["id"],
        folder_id=public.id,
        estado="ignorado",
        motivo="original",
    )
    db.add(meta)
    await db.commit()
    folder_uuid = public.id
    assert (await reconcile(client, account, snapshot())).status_code == 200
    renamed = [dict(f, name="avisos") if f["id"] == "info" else f for f in BASE_FOLDERS]
    assert (
        await reconcile(client, account, snapshot(folders=renamed, seconds=1))
    ).status_code == 200
    await db.refresh(public)
    await db.refresh(meta)
    assert public.id == folder_uuid and public.chave == "avisos"
    assert public.plataforma_manual == "ml" and public.revisada
    assert meta.folder_id == folder_uuid and meta.estado == "ignorado" and meta.motivo == "original"


def test_trash_subfolder_kind_is_authoritative():
    from app.services.mail_bridge_sync import folder_content

    assert (
        folder_content({"id": "x", "name": "Trash/antiga", "kind": "custom"})["tuta"]["folder_kind"]
        == "3"
    )
    assert (
        folder_content({"id": "x", "name": "Trash news", "kind": "custom"})["tuta"]["folder_kind"]
        == "0"
    )
