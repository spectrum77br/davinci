"""Apply authoritative bridge inventories without removing stored email content.

The existing mailbox lock serializes inventory and ingestion. Only a complete,
newer inventory may rename folders or mark previously observed mail as missing.
Folder names remain inside existing encrypted content; the side table stores
mailbox-scoped message hashes, opaque folder IDs and local message UUIDs.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail import MailMailbox, MailMessage
from app.models.mail_atendimento import MailCaixaIndice, MailFolder, MailMessageMeta
from app.models.mail_bridge import MailBridgeIdentity
from app.schemas.mail_bridge_sync import MESSAGE_ID_PATTERN, BridgeSnapshot
from app.security.cipher import decrypt_json, encrypt_json
from app.services.mail_atendimento import indice, pastas

CATALOG_KEY = "bridge_folder_catalog"
KINDS = {
    "custom": "0",
    "inbox": "1",
    "sent": "2",
    "trash": "3",
    "archive": "4",
    "spam": "5",
    "drafts": "6",
}
HEADER = re.compile(MESSAGE_ID_PATTERN)
LEGACY_SOURCE = re.compile(r"^imap(?::folder:[a-f0-9]{64})?:[0-9]+:[0-9]+$")


def identity_digest(message_id: object) -> str | None:
    if not isinstance(message_id, str) or not HEADER.fullmatch(message_id):
        return None
    return hashlib.sha256(message_id.encode("ascii")).hexdigest()


def source_digest(source_id: str, message_id: object) -> str | None:
    digest = identity_digest(message_id)
    if digest and (source_id == "tuta-bridge:" + digest or LEGACY_SOURCE.fullmatch(source_id)):
        return digest
    return None


def catalog(mailbox: MailMailbox) -> dict:
    value = decrypt_json(mailbox.config_enc).get(CATALOG_KEY)
    return value if isinstance(value, dict) else {}


def folder_content(folder: dict) -> dict:
    name = folder["name"]
    kind = folder["kind"]
    if kind == "custom":
        # A deleted subfolder keeps its MailSet kind; its ancestor defines the
        # special-use behavior. Never classify arbitrary names containing trash.
        if name.startswith("Trash/"):
            kind = "trash"
        elif name.startswith("Spam/"):
            kind = "spam"
    return {
        "folder": name,
        "tuta": {
            "folder_key": name,
            "folder_path": name,
            "folder_kind": KINDS[kind],
            "deleted": kind == "trash",
        },
    }


def decorate_ingest_content(mailbox: MailMailbox, source_id: str, content: dict) -> dict:
    if not source_digest(source_id, content.get("message_id")):
        return content
    for folder in catalog(mailbox).get("folders", []):
        if folder["name"] == content.get("folder"):
            parts = folder_content(folder)
            content["tuta"] = {**content.get("tuta", {}), **parts["tuta"]}
            break
    return content


async def canonical_message(session: AsyncSession, mailbox_id, source_id: str, message_id: str):
    digest = source_digest(source_id, message_id)
    if not digest:
        return None
    return await session.scalar(
        select(MailMessage)
        .join(MailBridgeIdentity, MailBridgeIdentity.canonical_message_id == MailMessage.id)
        .where(
            MailBridgeIdentity.mailbox_id == mailbox_id,
            MailBridgeIdentity.digest == digest,
            MailMessage.mailbox_id == mailbox_id,
        )
    )


async def remember_message(
    session: AsyncSession, message: MailMessage, message_id: str, mailbox: MailMailbox
):
    digest = source_digest(message.source_id, message_id)
    if digest:
        content = decrypt_json(message.content_enc)
        folder_id = next(
            (
                f["id"]
                for f in catalog(mailbox).get("folders", [])
                if f["name"] == content.get("folder")
            ),
            None,
        )
        await session.execute(
            pg_insert(MailBridgeIdentity)
            .values(
                mailbox_id=message.mailbox_id,
                digest=digest,
                canonical_message_id=message.id,
                message_ids=[str(message.id)],
                folder_id=folder_id,
            )
            .on_conflict_do_nothing(index_elements=["mailbox_id", "digest"])
        )


async def reconcile(session: AsyncSession, mailbox: MailMailbox, body: BridgeSnapshot) -> dict:
    from app.services.mail_central import MailError

    now = datetime.now(UTC)
    observed = body.observed_at.astimezone(UTC)
    if observed > now + timedelta(minutes=5) or observed < now - timedelta(minutes=15):
        raise MailError("bridge_snapshot_time_invalid", 409)
    config = decrypt_json(mailbox.config_enc)
    previous = config.get(CATALOG_KEY) or {}
    previous_time = previous.get("observed_at")
    if previous_time and observed <= datetime.fromisoformat(previous_time):
        raise MailError("bridge_snapshot_stale", 409)
    if not await indice.pegar_trava(session, mailbox.id):
        raise MailError("bridge_index_busy", 409)

    folders = [f.model_dump() for f in body.folders]
    by_id = {f["id"]: f for f in folders}
    by_name = {f["name"]: f for f in folders}
    by_kind = {f["kind"]: f for f in folders if f["kind"] != "custom"}
    placements = {identity_digest(m.message_id): by_id[m.folder_id] for m in body.messages}
    old_by_id = {f["id"]: f for f in previous.get("folders", [])}
    identities = (
        await session.scalars(
            select(MailBridgeIdentity).where(MailBridgeIdentity.mailbox_id == mailbox.id)
        )
    ).all()
    identity_values = {
        i.digest: {
            "mailbox_id": mailbox.id,
            "digest": i.digest,
            "canonical_message_id": i.canonical_message_id,
            "message_ids": list(i.message_ids),
            "folder_id": i.folder_id,
            "last_seen_at": i.last_seen_at,
            "missing_at": i.missing_at,
        }
        for i in identities
    }

    def identity_state(value):
        return (
            value["last_seen_at"],
            value["missing_at"],
            value["folder_id"],
            tuple(value["message_ids"]),
        )

    previous_identity_states = {
        digest: identity_state(value) for digest, value in identity_values.items()
    }
    metas = {
        m.message_id: m
        for m in (
            await session.scalars(
                select(MailMessageMeta).where(
                    MailMessageMeta.mailbox_id == mailbox.id, MailMessageMeta.folder_id.is_not(None)
                )
            )
        ).all()
    }
    public_folders = {
        f.chave: f
        for f in (
            await session.scalars(select(MailFolder).where(MailFolder.mailbox_id == mailbox.id))
        ).all()
    }
    for old in old_by_id.values():
        target = by_id.get(old["id"])
        public = public_folders.get(old["name"])
        if (
            target
            and target["name"] != old["name"]
            and public
            and target["name"] not in public_folders
        ):
            del public_folders[old["name"]]
            public.chave = target["name"]
            public.nome = target["name"].rsplit("/", 1)[-1]
            public.caminho = target["name"]
            public.tipo_tuta = folder_content(target)["tuta"]["folder_kind"]
            public_folders[target["name"]] = public

    bootstrap = not previous.get("inventory_ready")
    targets = {}
    candidate_ids = set()
    for digest, identity in identity_values.items():
        target, reason = placements.get(digest), None
        must_move = False
        if target is None and identity["last_seen_at"] is not None:
            target, reason = by_kind["trash"], "missing_from_source"
        elif target is None and identity["folder_id"]:
            target = by_id.get(identity["folder_id"])
            if target is None:
                target, reason = by_kind["archive"], "source_folder_removed"
        if target is not None:
            old_folder = old_by_id.get(identity["folder_id"])
            must_move = (
                target["id"] != identity["folder_id"]
                or (old_folder is not None and old_folder != target)
                or (digest in placements and identity["missing_at"] is not None)
            )
            if must_move:
                targets[digest] = (target, reason)
                candidate_ids.update(UUID(x) for x in identity["message_ids"])
            identity["folder_id"] = target["id"]
        if digest in placements:
            # This is the presence/placement transition checkpoint. The catalog's
            # observed_at records the latest full inventory, including unchanged
            # mail, so an idle mailbox does not rewrite thousands of rows.
            if identity["last_seen_at"] is None or identity["missing_at"] is not None or must_move:
                identity["last_seen_at"] = observed
            identity["missing_at"] = None
        elif identity["last_seen_at"] is not None:
            identity["missing_at"] = identity["missing_at"] or observed

    # A bootstrap scans encrypted rows in bounded pages ONCE. Subsequent passes
    # fetch only changed identities. Never load the whole mailbox's bodies.
    last_id = None
    remaining = sorted(candidate_ids)
    updated = 0
    base = None
    while bootstrap or remaining:
        query = select(MailMessage).where(MailMessage.mailbox_id == mailbox.id)
        if bootstrap:
            if last_id is not None:
                query = query.where(MailMessage.id > last_id)
            query = query.order_by(MailMessage.id).limit(250)
        else:
            query = query.where(MailMessage.id.in_(remaining[:250]))
            remaining = remaining[250:]
        rows = (await session.scalars(query)).all()
        if not rows:
            break
        last_id = rows[-1].id
        changed = []
        for message in rows:
            content = decrypt_json(message.content_enc)
            digest = source_digest(message.source_id, content.get("message_id"))
            if not digest:
                continue
            if bootstrap:
                identity = identity_values.get(digest)
                if identity is None:
                    current = by_name.get(content.get("folder"))
                    identity = identity_values[digest] = {
                        "mailbox_id": mailbox.id,
                        "digest": digest,
                        "canonical_message_id": message.id,
                        "message_ids": [],
                        "folder_id": current["id"] if current else None,
                        "last_seen_at": observed if digest in placements else None,
                        "missing_at": None,
                    }
                if identity["folder_id"] is None:
                    current = by_name.get(content.get("folder"))
                    identity["folder_id"] = current["id"] if current else None
                if str(message.id) not in identity["message_ids"]:
                    identity["message_ids"].append(str(message.id))
                target, reason = placements.get(digest), None
                if target is not None:
                    identity["folder_id"] = target["id"]
            else:
                target, reason = targets.get(digest, (None, None))
            if target is None:
                continue
            parts = folder_content(target)
            current_kind = pastas.do_conteudo(content).tipo_tuta
            if (
                content.get("folder") == parts["folder"]
                and current_kind == parts["tuta"]["folder_kind"]
                and not (digest in placements and "bridge_location_reason" in content)
            ):
                continue
            before = dict(content)
            old_tuta = content.get("tuta") if isinstance(content.get("tuta"), dict) else {}
            content["tuta"] = {**old_tuta, **parts["tuta"]}
            content["folder"] = parts["folder"]
            if reason and "bridge_previous_folder" not in content:
                content["bridge_previous_folder"] = before.get("folder")
                content["bridge_location_reason"] = reason
            elif digest in placements:
                content.pop("bridge_previous_folder", None)
                content.pop("bridge_location_reason", None)
            message.content_enc = encrypt_json(content)
            message.updated_at = now
            changed.append(message)
            meta = metas.get(message.id)
            if meta is not None:
                public = public_folders.get(target["name"])
                if public is None:
                    public, _ = await pastas.garantir(
                        session, mailbox.id, pastas.do_conteudo(content), agora=now
                    )
                    public_folders[target["name"]] = public
                meta.folder_id = public.id
        if changed:
            await session.flush()
            base = await indice.base_da_caixa(session, mailbox)
            index_rows = [indice.calcular(m, base) for m in changed]
            stmt = pg_insert(MailCaixaIndice).values(index_rows)
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["message_id"],
                    set_={
                        **{k: getattr(stmt.excluded, k) for k in indice._CAMPOS},
                        "indexado_em": func.now(),
                    },
                )
            )
            updated += len(changed)
        for row in rows:
            session.expunge(row)
        await asyncio.sleep(0)
        if bootstrap and len(rows) < 250:
            break

    for name, public in public_folders.items():
        if name in by_name:
            public.sumiu_em = None
            public.vista_em = now
            public.tipo_tuta = folder_content(by_name[name])["tuta"]["folder_kind"]
        elif public.sumiu_em is None:
            public.sumiu_em = now
    values = [
        value
        for digest, value in identity_values.items()
        if previous_identity_states.get(digest) != identity_state(value)
    ]
    identities_changed = 0
    identity_fields = ("last_seen_at", "missing_at", "folder_id", "message_ids")
    for start in range(0, len(values), 500):
        stmt = pg_insert(MailBridgeIdentity).values(values[start : start + 500])
        result = await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["mailbox_id", "digest"],
                set_={k: getattr(stmt.excluded, k) for k in identity_fields},
                where=or_(
                    *(
                        getattr(MailBridgeIdentity, k).is_distinct_from(getattr(stmt.excluded, k))
                        for k in identity_fields
                    )
                ),
            )
        )
        identities_changed += result.rowcount
    config[CATALOG_KEY] = {
        "version": 1,
        "observed_at": observed.isoformat(),
        "folders": folders,
        "inventory_ready": True,
    }
    mailbox.config_enc = encrypt_json(config)
    mailbox.last_sync_at = now
    await session.flush()
    return {
        "complete": True,
        "folders": len(folders),
        "messages": len(body.messages),
        "updated": updated,
        "identities_changed": identities_changed,
    }
