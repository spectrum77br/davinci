# ruff: noqa: F401, F811 — fixtures reais compartilhadas da Central.
"""A caixa inteira exige admin, sem alterar o conector autenticado por caixa."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.deps.auth import require_admin
from app.main import app
from app.models.mail import MailDraft, MailMailbox, MailMessage, MailOutbox
from app.models.mail_atendimento import MailMailboxSettings
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


@pytest.mark.parametrize("tipo", ["comum", "dono", "leitor", "permissoes_antigas"])
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
    user = await make_user(permissions=permissions if tipo == "permissoes_antigas" else {})
    mailbox = await db.get(MailMailbox, UUID(box))
    draft = await db.get(MailDraft, UUID(draft_response.json()["id"]))
    job = await db.get(MailOutbox, UUID(queued.json()["id"]))
    if tipo == "dono":
        mailbox.owner_user_id = user.id
    if tipo in {"leitor", "permissoes_antigas"}:
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
    def uses_admin(dependant):
        return dependant.call is require_admin or any(uses_admin(d) for d in dependant.dependencies)

    human = [route for route in app.routes
             if getattr(route, "path", "").startswith(f"{ROOT}/")
             and not route.path.startswith(f"{ROOT}/agent/")]
    assert human
    for route in human:
        assert uses_admin(route.dependant), (route.methods, route.path)
    agents = [route for route in app.routes
              if getattr(route, "path", "").startswith(f"{ROOT}/agent/")]
    assert agents
    assert all(not uses_admin(route.dependant) for route in agents)
