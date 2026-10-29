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
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.models import UserRole, UserStatus
from app.models.mail import MailOutbox
from tests.test_mail_central import ROOT, account, claim, enable, ingest  # noqa: F401
from tests.test_mail_composer import company, compose, send
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


async def test_com_editar_responde_e_o_mac_envia(client, account, make_user, auth_as, db):  # noqa: F811
    message_id, marrocos = await _cena(client, account, make_user, permissoes=RESPONDE)
    auth_as(marrocos)
    itens = (await client.get(f"{ROOT}/mailboxes")).json()["items"]
    assert itens[0]["so_leitura"] is True, "continua só leitura para o resto"
    assert itens[0]["pode_responder"] is True
    r = await _responde(client, message_id)
    assert r.status_code == 202, r.text
    job = await db.get(MailOutbox, UUID(r.json()["id"]))
    assert job.author_user_id == marrocos.id
    jobs = await claim(client, account)
    assert [j["id"] for j in jobs] == [str(job.id)], "o Mac pega a resposta (autor ainda pode)"


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


async def test_perdeu_a_permissao_o_mac_nao_envia(client, account, make_user, auth_as, db):  # noqa: F811
    message_id, marrocos = await _cena(client, account, make_user, permissoes=RESPONDE)
    auth_as(marrocos)
    r = await _responde(client, message_id)
    assert r.status_code == 202, r.text
    marrocos.permissions = SO_VE
    await db.commit()
    assert await claim(client, account) == []
    job = await db.get(MailOutbox, UUID(r.json()["id"]))
    await db.refresh(job)
    assert (job.status, job.error_code) == ("failed", "author_access_revoked")


async def test_suspenso_o_mac_nao_envia(client, account, make_user, auth_as, db):  # noqa: F811
    message_id, marrocos = await _cena(client, account, make_user, permissoes=RESPONDE)
    auth_as(marrocos)
    r = await _responde(client, message_id)
    assert r.status_code == 202, r.text
    marrocos.status = UserStatus.SUSPENDED
    await db.commit()
    assert await claim(client, account) == []
    job = await db.get(MailOutbox, UUID(r.json()["id"]))
    await db.refresh(job)
    assert job.error_code == "author_access_revoked"


async def test_caixa_da_empresa(client, account, make_user, auth_as, db, monkeypatch):  # noqa: F811
    await company(db, account, monkeypatch)
    message_id, marrocos = await _cena(client, account, make_user, permissoes=RESPONDE)
    so_ve = await make_user(role=UserRole.USER, permissions=SO_VE)
    assert (await _por_leitores(client, account["mailbox"]["id"], [marrocos.id, so_ve.id])).status_code == 200
    auth_as(so_ve)
    assert (await _responde(client, message_id, "do leitor")).status_code == 404
    auth_as(marrocos)
    r = await _responde(client, message_id)
    assert r.status_code == 202, r.text


async def test_saiu_nao_saiu_so_da_propria(client, account, make_user, auth_as, db):  # noqa: F811
    message_id, marrocos = await _cena(client, account, make_user, permissoes=RESPONDE)
    # a resposta do admin (dono) e a do marrocos, as duas incertas
    r_admin = await _responde(client, message_id, "do dono")
    assert r_admin.status_code == 202, r_admin.text
    await claim(client, account)
    auth_as(marrocos)
    message_2 = await ingest(client, account, source_id="original-tuta-message-2", message_id="<two@example.com>")
    r_marrocos = await _responde(client, message_2)
    assert r_marrocos.status_code == 202, r_marrocos.text
    await claim(client, account)
    for job_id in (r_admin.json()["id"], r_marrocos.json()["id"]):
        job = await db.get(MailOutbox, UUID(job_id))
        job.leased_at = datetime.now(UTC) - timedelta(minutes=16)
    await db.commit()
    await claim(client, account)  # o lease vencido vira "uncertain"
    # a tela só mostra "Saiu/Não saiu" onde a pessoa pode conferir
    flags = {
        j["id"]: j["pode_conferir"]
        for m in (message_id, message_2)
        for j in (await client.get(f"{ROOT}/messages/{m}")).json()["outbox"]
    }
    assert flags == {r_admin.json()["id"]: False, r_marrocos.json()["id"]: True}
    resolve = f"{ROOT}/outbox/{{}}/resolve"
    assert (await client.post(resolve.format(r_admin.json()["id"]), json={"saiu": True})).status_code == 404
    r = await client.post(resolve.format(r_marrocos.json()["id"]), json={"saiu": True})
    assert r.status_code == 200, r.text


async def test_escreve_novo_mas_nao_configura(client, account, make_user, auth_as, db):  # noqa: F811
    _, marrocos = await _cena(client, account, make_user, permissoes=RESPONDE)
    box = account["mailbox"]["id"]
    auth_as(marrocos)
    itens = (await client.get(f"{ROOT}/mailboxes")).json()["items"]
    assert (itens[0]["so_leitura"], itens[0]["pode_compor"]) == (True, True)
    contexto = (await client.get(f"{ROOT}/mailboxes/{box}/compose-context")).json()
    assert contexto["can_compose"] is True and "support@example.com" in contexto["allowed_from_addresses"]
    rascunho = await client.post(f"{ROOT}/mailboxes/{box}/drafts", json={"subject": "rascunho"})
    assert rascunho.status_code == 201, rascunho.text
    r = await send(client, account, compose(text="Mensagem nova do marrocos"))
    assert r.status_code == 202, r.text
    meus = (await client.get(f"{ROOT}/mailboxes/{box}/outbox")).json()["items"]
    assert [j["id"] for j in meus] == [r.json()["id"]]
    jobs = await claim(client, account)
    assert [j["id"] for j in jobs] == [r.json()["id"]], "o Mac pega o e-mail novo (autor ainda pode)"
    # configurar a caixa continua do dono/admin
    assert (await client.patch(f"{ROOT}/mailboxes/{box}", json={"label": "x"})).status_code == 403
    assert (await client.post(f"{ROOT}/mailboxes/{box}/token")).status_code == 403


async def test_so_ver_nao_escreve_novo(client, account, make_user, auth_as, db):  # noqa: F811
    _, pessoa = await _cena(client, account, make_user, permissoes=SO_VE)
    box = account["mailbox"]["id"]
    auth_as(pessoa)
    assert (await client.get(f"{ROOT}/mailboxes")).json()["items"][0]["pode_compor"] is False
    assert (await client.get(f"{ROOT}/mailboxes/{box}/compose-context")).json()["can_compose"] is False
    assert (await send(client, account, compose())).status_code == 403
    assert (await client.post(f"{ROOT}/mailboxes/{box}/drafts", json={})).status_code == 403
    assert await db.scalar(select(MailOutbox.id)) is None


async def test_sem_ver_a_caixa_nao_escreve_novo(client, account, make_user, auth_as, db):  # noqa: F811
    _, pessoa = await _cena(client, account, make_user, permissoes=RESPONDE, leitor=False)
    auth_as(pessoa)
    assert (await send(client, account, compose())).status_code == 404
    assert await db.scalar(select(MailOutbox.id)) is None


async def test_perdeu_a_permissao_o_mac_nao_envia_o_novo(client, account, make_user, auth_as, db):  # noqa: F811
    _, marrocos = await _cena(client, account, make_user, permissoes=RESPONDE)
    auth_as(marrocos)
    r = await send(client, account, compose())
    assert r.status_code == 202, r.text
    marrocos.permissions = SO_VE
    await db.commit()
    assert await claim(client, account) == []
    job = await db.get(MailOutbox, UUID(r.json()["id"]))
    await db.refresh(job)
    assert (job.status, job.error_code) == ("failed", "author_access_revoked")


async def test_caixa_da_empresa_escreve_novo(client, account, make_user, auth_as, db, monkeypatch):  # noqa: F811
    await company(db, account, monkeypatch)
    _, marrocos = await _cena(client, account, make_user, permissoes=RESPONDE)
    so_ve = await make_user(role=UserRole.USER, permissions=SO_VE)
    assert (await _por_leitores(client, account["mailbox"]["id"], [marrocos.id, so_ve.id])).status_code == 200
    auth_as(so_ve)
    assert (await send(client, account, compose())).status_code == 403
    auth_as(marrocos)
    r = await send(client, account, compose())
    assert r.status_code == 202, r.text


async def test_saiu_nao_saiu_do_proprio_e_mail_novo(client, account, make_user, auth_as, db):  # noqa: F811
    _, marrocos = await _cena(client, account, make_user, permissoes=RESPONDE)
    r_admin = await send(client, account, compose(text="do dono"))
    assert r_admin.status_code == 202, r_admin.text
    auth_as(marrocos)
    r_marrocos = await send(client, account, compose(text="do marrocos"))
    assert r_marrocos.status_code == 202, r_marrocos.text
    for job in await claim(client, account):
        receipt = await client.post(
            f"{account['agent']}/outbox/{job['id']}/receipt",
            headers=account["headers"],
            json={"lease_token": job["lease_token"], "status": "uncertain"},
        )
        assert receipt.status_code == 200, receipt.text
    resolve = f"{ROOT}/outbox/{{}}/resolve"
    assert (await client.post(resolve.format(r_admin.json()["id"]), json={"saiu": True})).status_code == 404
    r = await client.post(resolve.format(r_marrocos.json()["id"]), json={"saiu": True})
    assert r.status_code == 200, r.text


# ── concessão FILTRADA por loja (revisão de 28/10/2026) ──────────────────────
from app.models.mail_atendimento import MailMailboxSettings  # noqa: E402
from tests.test_mail_admin import _salvar  # noqa: E402
from tests.test_mail_ponte import GERAL, cena, rodar  # noqa: E402, F401
from tests.test_mail_venda_rotas import X, _caso  # noqa: E402


async def test_concessao_filtrada_so_responde_com_o_remetente_que_ve(
    db, cena, client, make_user, auth_as  # noqa: F811
):
    """Liberado só para a loja ML Barbosa: responder com o e-mail de OUTRA loja
    da caixa, um inexistente ou o principal dá o MESMO 409 (sem pista de quais
    existem) e nada vai para a fila; o remetente que ele vê passa da trava."""
    x = await _caso(db, cena, "12345678", X)
    await rodar(db)
    pessoa = await make_user(role=UserRole.USER, permissions=RESPONDE)
    auth_as(cena.dono)
    await _salvar(client, cena, pessoa, modo="selecionadas", lojas_permitidas=[f"i:{cena.ml.id}"])
    for estrito in (False, True):
        cfg = await db.get(MailMailboxSettings, cena.geral.id)
        cfg.remetente_estrito = estrito
        await db.commit()
        auth_as(pessoa)
        caixas = (await client.get(f"{ROOT}/mailboxes")).json()["items"]
        assert caixas[0]["pode_responder"] is True and caixas[0]["aliases"] == []
        visiveis = (await client.get(f"{ROOT}/messages/{x.message_id}")).json()["reply"][
            "allowed_from_addresses"
        ]
        for remetente in ("sac@uranyx.com.br", "nao-existe@tuta.com", GERAL):
            r = await client.post(
                f"{ROOT}/messages/{x.message_id}/reply",
                json={"request_id": str(uuid4()), "text": "oi", "from_address": remetente},
            )
            assert (r.status_code, r.json()) == (
                409,
                {"detail": {"code": "sender_not_authorized"}},
            ), (estrito, remetente)
        assert await db.scalar(select(MailOutbox.id)) is None
        for remetente in visiveis:
            r = await client.post(
                f"{ROOT}/messages/{x.message_id}/reply",
                json={"request_id": str(uuid4()), "text": "oi", "from_address": remetente},
            )
            assert r.json().get("detail", {}).get("code") != "sender_not_authorized", r.text
