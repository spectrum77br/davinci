"""Quem RESPONDE e-mail pela aba E-mail sem ser dono nem admin (28/10/2026).

Eduardo: "passe para o usuario marrocos acesso para ele conseguir responder os
emails de dentro do davinci, hoje so adm pode". A regra:

  • a permissão `atendimento_email` com ver E editar (`acesso.responde_email`)
    deixa RESPONDER o e-mail recebido nas caixas que a pessoa vê (leitor da
    caixa) — também na caixa da empresa, que antes exigia quem mexe;
  • só ver (editar desmarcado) continua só lendo: 404 como antes;
  • quem não vê a caixa não responde (404), mesmo com a permissão;
  • o Mac confere de novo na hora de enviar: tirou a permissão, a resposta
    na fila falha com `author_access_revoked`;
  • "Saiu/Não saiu" só da PRÓPRIA resposta; configurar a caixa continua do
    dono/admin.

29/10/2026 ("libere para o usuario marrocos poder enviar a mensagem"): quem
responde e vê a caixa INTEIRA também escreve e-mail novo (Nova mensagem,
rascunhos, encaminhar, anexos — `mail_composer.can_compose`); concessão
filtrada por loja continua sem o compositor.

30/10/2026, com as lojas do atendimento separadas por pessoa: "o email ninguém
pode ver a não ser admin né, já no atendimento eles vão ver só das caixas
dele". A aba E-mail volta a ser só de admin e `acesso.responde_email` não
concede mais nada: com ver+editar, quem não é dono/admin NÃO responde nem
escreve e-mail novo (os testes de antes, do marrocos respondendo, saíram).
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from app.models import UserRole
from app.models.mail import MailOutbox
from tests.test_mail_central import ROOT, account, claim, enable, ingest  # noqa: F401
from tests.test_mail_composer import compose, send
from tests.test_mail_leitores import _por_leitores

RESPONDE = {"atendimento_email": {"view": True, "edit": True, "delete": False}}
SO_VE = {"atendimento_email": {"view": True, "edit": False, "delete": False}}


async def _cena(client, account, make_user, *, permissoes, leitor=True):  # noqa: F811
    message_id = await ingest(client, account)
    await enable(client, account)
    pessoa = await make_user(role=UserRole.USER, permissions=permissoes)
    if leitor:
        r = await _por_leitores(client, account["mailbox"]["id"], [pessoa.id])
        assert r.status_code == 200, r.text
    return message_id, pessoa


async def _responde(client, message_id, texto="Resposta do marrocos"):
    return await client.post(
        f"{ROOT}/messages/{message_id}/reply",
        json={"request_id": str(uuid4()), "text": texto},
    )


async def test_so_ver_continua_sem_responder(client, account, make_user, auth_as, db):  # noqa: F811
    message_id, pessoa = await _cena(client, account, make_user, permissoes=SO_VE)
    auth_as(pessoa)
    itens = (await client.get(f"{ROOT}/mailboxes")).json()["items"]
    assert itens[0]["pode_responder"] is False
    assert (await _responde(client, message_id)).status_code == 404
    assert await db.scalar(select(MailOutbox.id)) is None


async def test_sem_ver_a_caixa_nao_responde(client, account, make_user, auth_as, db):  # noqa: F811
    message_id, pessoa = await _cena(client, account, make_user, permissoes=RESPONDE, leitor=False)
    auth_as(pessoa)
    assert (await client.get(f"{ROOT}/mailboxes")).json()["items"] == []
    assert (await _responde(client, message_id)).status_code == 404
    assert await db.scalar(select(MailOutbox.id)) is None


async def test_so_ver_nao_escreve_novo(client, account, make_user, auth_as, db):  # noqa: F811
    _, pessoa = await _cena(client, account, make_user, permissoes=SO_VE)
    box = account["mailbox"]["id"]
    auth_as(pessoa)
    assert (await client.get(f"{ROOT}/mailboxes")).json()["items"][0]["pode_compor"] is False
    contexto = (await client.get(f"{ROOT}/mailboxes/{box}/compose-context")).json()
    assert contexto["can_compose"] is False
    assert (await send(client, account, compose())).status_code == 403
    assert (await client.post(f"{ROOT}/mailboxes/{box}/drafts", json={})).status_code == 403
    assert await db.scalar(select(MailOutbox.id)) is None


async def test_sem_ver_a_caixa_nao_escreve_novo(client, account, make_user, auth_as, db):  # noqa: F811
    _, pessoa = await _cena(client, account, make_user, permissoes=RESPONDE, leitor=False)
    auth_as(pessoa)
    assert (await send(client, account, compose())).status_code == 404
    assert await db.scalar(select(MailOutbox.id)) is None


async def test_desde_30_10_a_permissao_nao_responde_nem_escreve(
    client,
    account,  # noqa: F811
    make_user,
    auth_as,
    db,
):
    message_id, marrocos = await _cena(client, account, make_user, permissoes=RESPONDE)
    auth_as(marrocos)
    itens = (await client.get(f"{ROOT}/mailboxes")).json()["items"]
    assert itens[0]["pode_responder"] is False
    assert (await _responde(client, message_id)).status_code == 404
    box = account["mailbox"]["id"]
    assert (await client.get(f"{ROOT}/mailboxes/{box}/compose-context")).json()[
        "can_compose"
    ] is False
    assert (
        await send(client, account, compose(text="Mensagem nova do marrocos"))
    ).status_code == 403
    assert await db.scalar(select(MailOutbox.id)) is None
