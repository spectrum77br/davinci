# ruff: noqa: F401, F811 — fixtures reais compartilhadas da Central.
"""Operação explícita de e-mail, limitada às caixas já autorizadas.

PostgreSQL local e HTTP em processo; o conector é simulado, sem transporte externo.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models.mail import MailDraft, MailMailbox, MailOutbox
from app.models.mail_admin import MailAcesso, MailLixeira
from app.models.mail_atendimento import MailMailboxSettings
from tests.test_mail_central import ROOT, account, claim, enable, ingest
from tests.test_mail_composer import compose, file_data


def grants(*, edit=True, delete=True):
    return {"atendimento_email_operacao": {"view": True, "edit": edit, "delete": delete}}


async def operator(db, account, make_user, *, owner=False, permissions=None, company=False):
    user = await make_user(permissions=grants() if permissions is None else permissions)
    mailbox = await db.get(MailMailbox, UUID(account["mailbox"]["id"]))
    if owner:
        mailbox.owner_user_id = user.id
    db.add(
        MailMailboxSettings(
            mailbox_id=mailbox.id,
            leitores=[] if owner else [str(user.id)],
            visibilidade="empresa" if company else "privada",
            envio_modo="real",
        )
    )
    await db.commit()
    return user


@pytest.mark.parametrize("company", [False, True])
async def test_operador_responde_compoe_encaminha_e_anexa_sem_ser_admin(
    client,
    account,
    db,
    make_user,
    auth_as,
    monkeypatch,
    company,
):
    mid = await ingest(client, account)
    await enable(client, account)
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)
    user = await operator(db, account, make_user, company=company)
    auth_as(user)
    box = account["mailbox"]["id"]
    listed = (await client.get(f"{ROOT}/mailboxes")).json()["items"]
    assert len(listed) == 1 and listed[0]["id"] == box
    assert listed[0]["so_leitura"] is True
    assert listed[0]["pode_operar"] is True
    assert listed[0]["pode_responder"] is listed[0]["pode_compor"] is True
    detail = (await client.get(f"{ROOT}/messages/{mid}")).json()
    assert detail["reply"]["can_reply"] is True
    aid = detail["attachments"][0]["id"]
    assert (await client.get(f"{ROOT}/attachments/{aid}")).content == b"private attachment"
    reply = await client.post(
        f"{ROOT}/messages/{mid}/reply",
        json={
            "request_id": str(uuid4()),
            "text": "Resposta humana autorizada",
            "from_address": "support@example.com",
        },
    )
    assert reply.status_code == 202, reply.text
    [leased] = await claim(client, account)
    assert leased["id"] == reply.json()["id"]
    receipt = await client.post(
        f"{account['agent']}/outbox/{leased['id']}/receipt",
        headers=account["headers"],
        json={"lease_token": leased["lease_token"], "status": "sent"},
    )
    assert receipt.status_code == 200, receipt.text
    for action in ("new", "reply", "reply_all", "forward"):
        source = None if action == "new" else mid
        context = await client.get(
            f"{ROOT}/mailboxes/{box}/compose-context",
            params={
                "action": action,
                **({"source_message_id": source} if source else {}),
            },
        )
        assert context.status_code == 200, context.text
        assert context.json()["can_compose"] is True
        body = compose(
            action=action,
            source_message_id=source,
            cc=["copy@example.com"],
            bcc=["hidden@example.com"],
            attachments=[file_data()],
            source_attachment_ids=[aid] if action == "forward" else [],
        )
        sent = await client.post(f"{ROOT}/mailboxes/{box}/send", json=body)
        assert sent.status_code == 202, (action, sent.text)
        [queued] = await claim(client, account)
        assert queued["id"] == sent.json()["id"]
        assert queued["attachments"] and queued["cc"] == body["cc"]
        assert queued["bcc"] == body["bcc"]
        receipt = await client.post(
            f"{account['agent']}/outbox/{queued['id']}/receipt",
            headers=account["headers"],
            json={"lease_token": queued["lease_token"], "status": "sent"},
        )
        assert receipt.status_code == 200, receipt.text


async def test_rascunhos_historico_e_conferencia_continuam_pessoais(
    client,
    account,
    db,
    make_user,
    auth_as,
):
    await enable(client, account)
    box = account["mailbox"]["id"]
    admin_draft = await client.post(f"{ROOT}/mailboxes/{box}/drafts", json={"text": "Admin"})
    admin_send = await client.post(f"{ROOT}/mailboxes/{box}/send", json=compose())
    assert admin_draft.status_code == 201 and admin_send.status_code == 202
    admin_job = await db.get(MailOutbox, UUID(admin_send.json()["id"]))
    admin_job.status = "uncertain"
    await db.commit()
    user = await operator(db, account, make_user)
    auth_as(user)
    drafts_url = f"{ROOT}/mailboxes/{box}/drafts"
    assert (await client.get(drafts_url)).json()["items"] == []
    assert (await client.get(f"{ROOT}/mailboxes/{box}/outbox")).json()["items"] == []
    for method in ("get", "patch", "delete"):
        response = await getattr(client, method)(
            f"{ROOT}/drafts/{admin_draft.json()['id']}",
            **({"json": {"text": "Não autorizado"}} if method == "patch" else {}),
        )
        assert response.status_code == 404, response.text
    denied = await client.post(f"{ROOT}/outbox/{admin_job.id}/resolve", json={"saiu": False})
    assert denied.status_code == 404, denied.text
    created = await client.post(drafts_url, json={"subject": "Meu rascunho"})
    assert created.status_code == 201, created.text
    draft_id = created.json()["id"]
    assert (await client.get(f"{ROOT}/drafts/{draft_id}")).status_code == 200
    updated = await client.patch(
        f"{ROOT}/drafts/{draft_id}",
        json={
            "text": "Meu texto",
            "attachments": [file_data()],
        },
    )
    assert updated.status_code == 200 and updated.json()["text"] == "Meu texto"
    assert (await client.delete(f"{ROOT}/drafts/{draft_id}")).status_code == 204
    sent = await client.post(f"{ROOT}/mailboxes/{box}/send", json=compose())
    assert sent.status_code == 202, sent.text
    [leased] = await claim(client, account)
    receipt = await client.post(
        f"{account['agent']}/outbox/{leased['id']}/receipt",
        headers=account["headers"],
        json={"lease_token": leased["lease_token"], "status": "uncertain"},
    )
    assert receipt.status_code == 200, receipt.text
    resolved = await client.post(f"{ROOT}/outbox/{leased['id']}/resolve", json={"saiu": False})
    assert resolved.status_code == 200 and resolved.json()["status"] == "failed"
    assert [
        j["id"] for j in (await client.get(f"{ROOT}/mailboxes/{box}/outbox")).json()["items"]
    ] == [sent.json()["id"]]


async def test_operacao_nao_concede_outras_caixas_nem_configuracao(
    client,
    account,
    db,
    make_user,
    auth_as,
):
    mid = await ingest(client, account)
    other = await client.post(
        f"{ROOT}/mailboxes",
        json={
            "label": "Outra caixa",
            "address": "restricted@example.com",
        },
    )
    assert other.status_code == 201
    other_id = other.json()["id"]
    user = await operator(db, account, make_user)
    auth_as(user)
    box = account["mailbox"]["id"]
    for suffix in ("messages", "compose-context", "outbox", "drafts", "trash"):
        response = await client.get(f"{ROOT}/mailboxes/{other_id}/{suffix}")
        assert response.status_code == 404, (suffix, response.text)
    assert (
        await client.post(f"{ROOT}/mailboxes/{other_id}/send", json=compose())
    ).status_code == 404
    for method, path, body in (
        ("post", f"{ROOT}/mailboxes", {"label": "Nova", "address": "new@example.com"}),
        ("patch", f"{ROOT}/mailboxes/{box}", {"send_enabled": True}),
        ("post", f"{ROOT}/mailboxes/{box}/token", {}),
        ("get", f"{ROOT}/mailboxes/{box}/settings", None),
        ("patch", f"{ROOT}/mailboxes/{box}/settings", {"ponte_ligada": True}),
        ("get", f"{ROOT}/mailboxes/{box}/leitores", None),
        ("put", f"{ROOT}/mailboxes/{box}/leitores", {"leitores": []}),
        ("get", "/api/atendimento/email/admin", None),
        ("get", "/api/atendimento/email/admin/lixeira", None),
        ("post", f"/api/atendimento/email/admin/emails/{mid}/excluir", {}),
    ):
        response = await getattr(client, method)(
            path, **({"json": body} if body is not None else {})
        )
        assert response.status_code == 403, (method, path, response.text)


@pytest.mark.parametrize(
    "permissions",
    [
        {"atendimento_email": {"view": True, "edit": True, "delete": True}},
        {"atendimento_email_leitura": {"view": True, "edit": True, "delete": True}},
        {"atendimento_email_aba": {"view": True, "edit": True, "delete": True}},
        grants(edit=False, delete=False),
    ],
)
async def test_concessoes_de_leitura_e_antigas_nao_autorizam_operacao(
    client,
    account,
    db,
    make_user,
    auth_as,
    permissions,
):
    mid = await ingest(client, account)
    user = await operator(db, account, make_user, permissions=permissions)
    auth_as(user)
    box = account["mailbox"]["id"]
    for method, path, body in (
        ("get", f"{ROOT}/mailboxes/{box}/compose-context", None),
        ("get", f"{ROOT}/mailboxes/{box}/outbox", None),
        ("get", f"{ROOT}/mailboxes/{box}/drafts", None),
        ("post", f"{ROOT}/mailboxes/{box}/drafts", {"text": "Não autorizado"}),
        ("post", f"{ROOT}/mailboxes/{box}/send", compose()),
        ("post", f"{ROOT}/messages/{mid}/reply", {"request_id": str(uuid4()), "text": "Não"}),
        ("post", f"{ROOT}/messages/{mid}/trash", {}),
        ("post", f"{ROOT}/messages/{mid}/restore", {}),
        ("get", f"{ROOT}/mailboxes/{box}/trash", None),
    ):
        response = await getattr(client, method)(
            path, **({"json": body} if body is not None else {})
        )
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["detail"]["code"] == "admin_only"
    assert await db.scalar(select(func.count()).select_from(MailOutbox)) == 0
    assert await db.scalar(select(func.count()).select_from(MailDraft)) == 0


@pytest.mark.parametrize("owner", [False, True])
@pytest.mark.parametrize("kind", ["reply", "compose"])
async def test_revogar_operacao_impede_conector_de_enviar_fila_pendente(
    client,
    account,
    db,
    make_user,
    auth_as,
    owner,
    kind,
):
    mid = await ingest(client, account)
    await enable(client, account)
    user = await operator(db, account, make_user, owner=owner)
    auth_as(user)
    if kind == "reply":
        queued = await client.post(
            f"{ROOT}/messages/{mid}/reply",
            json={
                "request_id": str(uuid4()),
                "text": "Acesso será retirado",
            },
        )
    else:
        queued = await client.post(
            f"{ROOT}/mailboxes/{account['mailbox']['id']}/send", json=compose()
        )
    assert queued.status_code == 202, queued.text
    user.permissions = {"atendimento_email_leitura": {"view": True}}
    await db.commit()
    assert await claim(client, account) == []
    job = await db.get(MailOutbox, UUID(queued.json()["id"]))
    await db.refresh(job)
    assert job.status == "failed" and job.error_code == "author_access_revoked"


async def test_bloqueio_da_caixa_vence_operacao_e_leitor_existente(
    client,
    account,
    db,
    make_user,
    auth_as,
):
    mid = await ingest(client, account)
    await enable(client, account)
    user = await operator(db, account, make_user)
    box = UUID(account["mailbox"]["id"])
    db.add(MailAcesso(mailbox_id=box, user_id=user.id, modo="bloqueado"))
    await db.commit()
    auth_as(user)
    assert (await client.get(f"{ROOT}/mailboxes")).json()["items"] == []
    for path in (f"mailboxes/{box}/compose-context", f"messages/{mid}", f"mailboxes/{box}/trash"):
        assert (await client.get(f"{ROOT}/{path}")).status_code == 404
    for path, body in (
        (f"messages/{mid}/reply", {"request_id": str(uuid4()), "text": "Não"}),
        (f"mailboxes/{box}/send", compose()),
        (f"messages/{mid}/trash", {}),
        (f"messages/{mid}/restore", {}),
    ):
        response = await client.post(f"{ROOT}/{path}", json=body)
        assert response.status_code == 404, (path, response.text)


async def test_politica_filtrada_nao_libera_compositor_nem_envio_pendente(
    client,
    account,
    db,
    make_user,
    auth_as,
):
    await enable(client, account)
    user = await operator(db, account, make_user)
    box = UUID(account["mailbox"]["id"])
    auth_as(user)
    queued = await client.post(f"{ROOT}/mailboxes/{box}/send", json=compose())
    assert queued.status_code == 202, queued.text
    db.add(
        MailAcesso(
            mailbox_id=box,
            user_id=user.id,
            modo="padrao",
            tipos_permitidos=["informativo", "chamado", "venda"],
        )
    )
    await db.commit()
    context = await client.get(f"{ROOT}/mailboxes/{box}/compose-context")
    assert context.status_code == 200, context.text
    assert context.json()["can_compose"] is False
    assert context.json()["allowed_from_addresses"] == []
    assert (await client.post(f"{ROOT}/mailboxes/{box}/send", json=compose())).status_code == 403
    assert await claim(client, account) == []
    job = await db.get(MailOutbox, UUID(queued.json()["id"]))
    await db.refresh(job)
    assert job.error_code == "author_access_revoked"


async def test_lixeira_tem_permissao_propria_escopo_e_restauracao(
    client,
    account,
    db,
    make_user,
    auth_as,
):
    mid = await ingest(client, account)
    user = await operator(db, account, make_user, permissions=grants(delete=False))
    box = account["mailbox"]["id"]
    auth_as(user)
    assert (await client.post(f"{ROOT}/messages/{mid}/trash", json={})).status_code == 403
    assert (await client.get(f"{ROOT}/mailboxes/{box}/trash")).status_code == 403
    user.permissions = grants()
    await db.commit()
    deleted = await client.post(f"{ROOT}/messages/{mid}/trash", json={"motivo": "Teste local"})
    assert deleted.status_code == 200, deleted.text
    assert (await client.get(f"{ROOT}/messages/{mid}")).status_code == 404
    item = await db.get(MailLixeira, UUID(mid))
    assert item.excluido_por == user.id and item.excluido_em is not None
    listing = await client.get(f"{ROOT}/mailboxes/{box}/trash")
    assert listing.status_code == 200 and len(listing.json()["items"]) == 1
    restored = await client.post(f"{ROOT}/messages/{mid}/restore", json={})
    assert restored.status_code == 200, restored.text
    assert (await client.get(f"{ROOT}/messages/{mid}")).status_code == 200
    assert (await client.get(f"{ROOT}/mailboxes/{box}/trash")).json()["items"] == []
    await db.refresh(item)
    assert item.restaurado_por == user.id and item.excluido_em is None


async def test_dono_nao_admin_filtrado_so_responde_com_remetente_visivel(
    client,
    account,
    db,
    make_user,
    auth_as,
):
    mid = await ingest(client, account)
    await enable(client, account)
    user = await operator(db, account, make_user, owner=True)
    box = UUID(account["mailbox"]["id"])
    db.add(
        MailAcesso(
            mailbox_id=box,
            user_id=user.id,
            modo="padrao",
            tipos_permitidos=["informativo", "chamado", "venda"],
        )
    )
    await db.commit()
    auth_as(user)
    detail = await client.get(f"{ROOT}/messages/{mid}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["reply"]["allowed_from_addresses"] == ["support@example.com"]
    denied = await client.post(
        f"{ROOT}/messages/{mid}/reply",
        json={
            "request_id": str(uuid4()),
            "text": "Remetente fora da concessão",
            "from_address": "owner@example.com",
        },
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["detail"]["code"] == "sender_not_authorized"
    accepted = await client.post(
        f"{ROOT}/messages/{mid}/reply",
        json={
            "request_id": str(uuid4()),
            "text": "Remetente que recebeu a mensagem",
            "from_address": "support@example.com",
        },
    )
    assert accepted.status_code == 202, accepted.text


async def test_lixeira_filtrada_nao_lista_nem_restaura_mensagem_fora_da_politica(
    client,
    account,
    db,
    make_user,
    auth_as,
):
    mid = await ingest(client, account)
    user = await operator(db, account, make_user)
    box = UUID(account["mailbox"]["id"])
    auth_as(user)
    deleted = await client.post(f"{ROOT}/messages/{mid}/trash", json={})
    assert deleted.status_code == 200, deleted.text
    policy = MailAcesso(
        mailbox_id=box,
        user_id=user.id,
        modo="padrao",
        tipos_permitidos=["chamado"],
    )
    db.add(policy)
    await db.commit()
    listed = await client.get(f"{ROOT}/mailboxes/{box}/trash")
    assert listed.status_code == 200 and listed.json()["items"] == []
    restored = await client.post(f"{ROOT}/messages/{mid}/restore")
    assert restored.status_code == 404, restored.text
    item = await db.get(MailLixeira, UUID(mid))
    await db.refresh(item)
    assert item.excluido_em is not None and item.restaurado_por is None
    await db.delete(policy)
    await db.commit()
    assert (await client.post(f"{ROOT}/messages/{mid}/restore")).status_code == 200


async def test_lixeira_preserva_restricao_de_admin_que_nao_mexe(
    client,
    account,
    monkeypatch,
):
    mid = await ingest(client, account)
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "outro@example.com")
    box = account["mailbox"]["id"]
    for method, path in (
        ("get", f"{ROOT}/mailboxes/{box}/trash"),
        ("post", f"{ROOT}/messages/{mid}/trash"),
        ("post", f"{ROOT}/messages/{mid}/restore"),
    ):
        result = await getattr(client, method)(path, **({"json": {}} if method == "post" else {}))
        assert result.status_code == 403, (method, path, result.text)
