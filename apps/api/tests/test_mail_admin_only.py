# ruff: noqa: F401, F811 — fixtures reais compartilhadas da Central.
"""A caixa inteira exige admin, sem alterar o conector autenticado por caixa."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.deps.auth import require_admin
from app.main import app
from app.models.mail import MailDraft, MailMailbox, MailMessage, MailOutbox
from app.models.mail_atendimento import MailMailboxSettings
from app.routers.mail import require_mail_manager, require_mail_operator, require_mail_reader
from tests.test_mail_central import ROOT, account, enable, ingest, message


def human_routes(box, mid, aid, job, draft):
    compose = {
        "from_address": "support@example.com", "to": ["customer@example.com"],
        "subject": "Tentativa bloqueada", "text": "Não deve entrar na fila",
    }
    return [
        ("get", f"{ROOT}/mailboxes", None),
        ("post", f"{ROOT}/mailboxes", {"label": "Outra", "address": "other@example.com"}),
        ("patch", f"{ROOT}/mailboxes/{box}", {"label": "Alterada"}),
        ("post", f"{ROOT}/mailboxes/{box}/token", {}),
        ("get", f"{ROOT}/mailboxes/{box}/messages", None),
        ("get", f"{ROOT}/messages/{mid}", None),
        ("get", f"{ROOT}/attachments/{aid}", None),
        ("post", f"{ROOT}/messages/{mid}/reply", {
            "request_id": str(uuid4()), "text": "Bloqueada",
        }),
        ("post", f"{ROOT}/outbox/{job}/resolve", {"saiu": False}),
        ("get", f"{ROOT}/mailboxes/{box}/compose-context", None),
        ("post", f"{ROOT}/mailboxes/{box}/send", {**compose, "request_id": str(uuid4())}),
        ("get", f"{ROOT}/mailboxes/{box}/outbox", None),
        ("get", f"{ROOT}/mailboxes/{box}/drafts", None),
        ("post", f"{ROOT}/mailboxes/{box}/drafts", compose),
        ("get", f"{ROOT}/drafts/{draft}", None),
        ("patch", f"{ROOT}/drafts/{draft}", {"text": "Alterado"}),
        ("delete", f"{ROOT}/drafts/{draft}", None),
        ("get", f"{ROOT}/mailboxes/{box}/pastas", None),
        ("get", f"{ROOT}/mailboxes/{box}/lista", None),
        ("get", f"{ROOT}/mailboxes/{box}/settings", None),
        ("patch", f"{ROOT}/mailboxes/{box}/settings", {"ponte_ligada": True}),
        ("get", f"{ROOT}/mailboxes/{box}/leitores", None),
        ("put", f"{ROOT}/mailboxes/{box}/leitores", {"leitores": []}),
    ]


@pytest.mark.parametrize("tipo", ["comum", "dono", "leitor", "permissoes_antigas", "aba_apenas"])
async def test_central_recusa_todas_rotas_humanas_sem_admin(
    client, account, db, make_user, auth_as, tipo,
):
    box = account["mailbox"]["id"]
    mid = await ingest(client, account)
    detail = (await client.get(f"{ROOT}/messages/{mid}")).json()
    aid = detail["attachments"][0]["id"]
    await enable(client, account)
    queued = await client.post(f"{ROOT}/messages/{mid}/reply", json={
        "request_id": str(uuid4()), "text": "Resposta anterior do administrador",
    })
    assert queued.status_code == 202, queued.text
    draft_response = await client.post(f"{ROOT}/mailboxes/{box}/drafts", json={"text": "Rascunho"})
    assert draft_response.status_code == 201, draft_response.text
    permissions = {"atendimento_email": {"view": True, "edit": True, "delete": True}}
    if tipo == "aba_apenas":
        permissions = {"atendimento_email_aba": {"view": True, "edit": True}}
    user = await make_user(
        permissions=permissions if tipo in {"permissoes_antigas", "aba_apenas"} else {}
    )
    mailbox = await db.get(MailMailbox, UUID(box))
    draft = await db.get(MailDraft, UUID(draft_response.json()["id"]))
    job = await db.get(MailOutbox, UUID(queued.json()["id"]))
    if tipo == "dono":
        mailbox.owner_user_id = user.id
    if tipo in {"leitor", "permissoes_antigas", "aba_apenas"}:
        db.add(MailMailboxSettings(mailbox_id=mailbox.id, leitores=[str(user.id)]))
    # Mesmo ser autor dos objetos da caixa não substitui a função de admin.
    draft.author_user_id = user.id
    job.author_user_id = user.id
    await db.commit()
    token_before = mailbox.agent_token_hash
    draft_before = draft.content_enc
    job_before = job.status
    auth_as(user)

    for method, path, body in human_routes(box, mid, aid, job.id, draft.id):
        options = {"json": body} if body is not None else {}
        response = await getattr(client, method)(path, **options)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["detail"]["code"] == "admin_only", (method, path)
    await db.refresh(mailbox)
    await db.refresh(draft)
    await db.refresh(job)
    assert mailbox.label == "Private test inbox" and mailbox.agent_token_hash == token_before
    assert draft.content_enc == draft_before and job.status == job_before
    for model in (MailMailbox, MailMessage, MailOutbox, MailDraft):
        assert await db.scalar(select(func.count()).select_from(model)) == 1

    # Admin continua lendo a mesma caixa, inclusive se não for o dono.
    auth_as(account["admin"])
    assert (await client.get(f"{ROOT}/mailboxes")).status_code == 200
    assert (await client.get(f"{ROOT}/messages/{mid}")).json()["subject"] == detail["subject"]
    assert (await client.get(f"{ROOT}/attachments/{aid}")).content == b"private attachment"

    # O Mac não usa a sessão da pessoa: sua chave continua sincronizando.
    auth_as(None)
    heartbeat = await client.post(f"{account['agent']}/heartbeat", headers=account["headers"],
                                  json={"state": "online", "can_send": True})
    assert heartbeat.status_code == 200, heartbeat.text
    received = await client.post(f"{account['agent']}/ingest", headers=account["headers"],
                                json={"messages": [message(source_id="apos-restricao")]})
    assert received.status_code == 200, received.text
    assert await db.scalar(select(func.count()).select_from(MailMessage)) == 2
    assert (await client.get(f"{ROOT}/mailboxes")).status_code == 401


def test_novas_rotas_humanas_nao_podem_ficar_sem_trava_admin():
    def uses(dependant, dependency):
        return dependant.call is dependency or any(
            uses(d, dependency) for d in dependant.dependencies
        )

    reader_paths = {
        "/api/mail/mailboxes",
        "/api/mail/mailboxes/{mailbox_id}/messages",
        "/api/mail/messages/{message_id}",
        "/api/mail/attachments/{attachment_id}",
        "/api/mail/mailboxes/{mailbox_id}/pastas",
        "/api/mail/mailboxes/{mailbox_id}/lista",
    }

    operator_paths = {
        "/api/mail/messages/{message_id}/reply", "/api/mail/outbox/{job_id}/resolve",
        "/api/mail/mailboxes/{mailbox_id}/compose-context", "/api/mail/mailboxes/{mailbox_id}/send",
        "/api/mail/mailboxes/{mailbox_id}/outbox", "/api/mail/mailboxes/{mailbox_id}/drafts",
        "/api/mail/drafts/{draft_id}",
    }
    manager_paths = {
        "/api/mail/mailboxes/{mailbox_id}/trash", "/api/mail/messages/{message_id}/trash",
        "/api/mail/messages/{message_id}/restore",
    }
    human = [route for route in app.routes
             if getattr(route, "path", "").startswith(f"{ROOT}/")
             and not route.path.startswith(f"{ROOT}/agent/")]
    assert human
    for route in human:
        if route.path in manager_paths:
            dependency = require_mail_manager
        elif route.path in operator_paths:
            dependency = require_mail_operator
        elif route.methods == {"GET"} and route.path in reader_paths:
            dependency = require_mail_reader
        else:
            dependency = require_admin
        assert uses(route.dependant, dependency), (route.methods, route.path)
    agents = [route for route in app.routes
              if getattr(route, "path", "").startswith(f"{ROOT}/agent/")]
    assert agents
    assert all(not uses(route.dependant, require_admin) for route in agents)
    assert all(not uses(route.dependant, require_mail_reader) for route in agents)


@pytest.mark.parametrize("dono", [False, True])
async def test_excecao_nova_le_so_a_caixa_liberada_sem_poder_escrever(
    client, account, db, make_user, auth_as, dono,
):
    box = account["mailbox"]["id"]
    mid = await ingest(client, account)
    aid = (await client.get(f"{ROOT}/messages/{mid}")).json()["attachments"][0]["id"]
    await enable(client, account)
    other = await client.post(f"{ROOT}/mailboxes", json={
        "label": "Caixa não autorizada", "address": "restricted@example.com",
    })
    assert other.status_code == 201
    user = await make_user(permissions={
        "atendimento_email_leitura": {"view": True, "edit": False, "delete": False},
        "atendimento_email": {"view": True, "edit": True, "delete": False},
    })
    mailbox = await db.get(MailMailbox, UUID(box))
    if dono:
        mailbox.owner_user_id = user.id
    else:
        db.add(MailMailboxSettings(mailbox_id=mailbox.id, leitores=[str(user.id)]))
    await db.commit()
    auth_as(user)
    paths = {
        f"{ROOT}/mailboxes", f"{ROOT}/mailboxes/{box}/messages", f"{ROOT}/messages/{mid}",
        f"{ROOT}/attachments/{aid}", f"{ROOT}/mailboxes/{box}/pastas",
        f"{ROOT}/mailboxes/{box}/lista",
    }
    for method, path, body in human_routes(box, mid, aid, uuid4(), uuid4()):
        result = await getattr(client, method)(path, **({"json": body} if body is not None else {}))
        if method == "get" and path in paths:
            assert result.status_code == 200, (path, result.text)
        else:
            assert result.status_code == 403, (method, path, result.text)
    listed = (await client.get(f"{ROOT}/mailboxes")).json()["items"]
    assert len(listed) == 1 and listed[0]["id"] == box
    assert listed[0]["so_leitura"] is True
    assert listed[0]["pode_responder"] is listed[0]["pode_compor"] is False
    detail = (await client.get(f"{ROOT}/messages/{mid}")).json()
    assert detail["reply"]["can_reply"] is False
    assert (await client.get(f"{ROOT}/mailboxes/{other.json()['id']}/messages")).status_code == 404
    assert await db.scalar(select(func.count()).select_from(MailOutbox)) == 0

    # Retirar somente a nova permissão fecha inclusive links diretos antigos.
    user.permissions = {"atendimento_email": {"view": True, "edit": True}}
    await db.commit()
    for path in paths:
        assert (await client.get(path)).status_code == 403


async def test_permissao_nova_sem_concessao_de_caixa_nao_libera_conteudo(
    client, account, db, make_user, auth_as,
):
    mid = await ingest(client, account)
    user = await make_user(permissions={"atendimento_email_leitura": {"view": True}})
    auth_as(user)
    assert (await client.get(f"{ROOT}/mailboxes")).json() == {"items": []}
    assert (await client.get(f"{ROOT}/messages/{mid}")).status_code == 404
