# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte
"""A lista de usuários do e-mail (12/11/2026) e a regra de quem vê qual caixa.

O Eduardo: a administração antiga "está complexa e sem uso"; no lugar, uma
linha por usuário e, no Editar, as caixas em checkbox. A tela grava no que já
decide o acesso (a permissão `atendimento_email_leitura` e a lista de leitores
de cada caixa). Admin vê tudo; quem não é admin vê só as caixas marcadas; o
e-mail privado do dono e o de segurança nunca aparecem para quem não é admin
nem dono. Nenhuma conta externa é consultada.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.config import get_settings
from app.models import UserRole, UserStatus
from app.models.mail import MailMessage
from app.models.mail_admin import MailAdminEvento, MailLeitorPastas
from app.models.mail_atendimento import MailCaixaIndice, MailMailboxSettings
from app.services.mail_atendimento import administracao, indice
from tests.test_mail_ponte import AGORA, _config, cena, entrar

ADMIN = "/api/atendimento/email/admin/usuarios"
LEITURA = {"atendimento_email_leitura": {"view": True, "edit": False, "delete": False}}


def _min(n: int):
    return AGORA - timedelta(minutes=n)


async def de_loja(db, mid) -> None:
    """Indexa de verdade e marca um e-mail de teste como da equipe ("sem loja"),
    para os testes de operação lerem como leitor; o privado/segurança é testado
    aqui."""
    await indice.rodar(db)
    await db.execute(
        update(MailCaixaIndice)
        .where(MailCaixaIndice.message_id == mid)
        .values(selo=indice.SELO_SEM_LOJA, seguranca=False)
    )
    await db.commit()


async def _put(client, pessoa, *, ligado=True, caixas=(), pastas=None):
    """`caixas`: as contas marcadas inteiras; `pastas`: {conta: [pasta_chave]}."""
    corpo = [{"id": str(c.id)} for c in caixas]
    corpo += [{"id": str(c.id), "pastas": lista} for c, lista in (pastas or {}).items()]
    return await client.put(f"{ADMIN}/{pessoa.id}", json={"ligado": ligado, "caixas": corpo})


async def _linha(client, pessoa) -> dict:
    r = await client.get(ADMIN)
    assert r.status_code == 200, r.text
    return next(u for u in r.json()["usuarios"] if u["id"] == str(pessoa.id))


async def _leitores(db, caixa) -> list:
    return await db.scalar(
        select(MailMailboxSettings.leitores)
        .where(MailMailboxSettings.mailbox_id == caixa.id)
        .execution_options(populate_existing=True)
    )


async def _caixas_que_abre(client) -> list[str] | int:
    r = await client.get("/api/mail/mailboxes")
    return r.status_code if r.status_code != 200 else [c["id"] for c in r.json()["items"]]


async def test_so_admin_que_mexe_ve_e_grava(client, cena, make_user, auth_as, monkeypatch):
    pessoa = await make_user(role=UserRole.USER, permissions=LEITURA)
    for quem in (pessoa, await make_user(role=UserRole.USER)):
        auth_as(quem)
        assert (await client.get(ADMIN)).status_code == 403
        assert (await _put(client, pessoa, caixas=[cena.geral])).status_code == 403
    # Admin fora da lista de quem mexe no /atendimento também não.
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "outro@example.com")
    auth_as(cena.dono)
    assert (await client.get(ADMIN)).status_code == 403


async def test_lista_mostra_admin_com_todas_e_usuario_com_as_dele(
    db, client, cena, make_user, auth_as
):
    pessoa = await make_user(role=UserRole.USER, permissions=LEITURA)
    cfg = await db.get(MailMailboxSettings, cena.geral.id)
    cfg.leitores = [str(pessoa.id)]
    await db.commit()
    auth_as(cena.dono)
    r = await client.get(ADMIN)
    assert r.status_code == 200, r.text
    assert {c["id"] for c in r.json()["caixas"]} == {str(cena.geral.id), str(cena.goslin.id)}
    dono = next(u for u in r.json()["usuarios"] if u["id"] == str(cena.dono.id))
    assert dono["admin"] and dono["ligado"] and dono["todas"]
    linha = await _linha(client, pessoa)
    assert linha["login"] == pessoa.email and not linha["admin"]
    assert linha["ligado"] is True and linha["todas"] is False
    assert linha["caixas"] == [str(cena.geral.id)]
    assert linha["criado_em"] and "ultimo_acesso" in linha


async def test_marcar_e_desmarcar_caixas_e_ligar_desligar(db, client, cena, make_user, auth_as):
    pessoa = await make_user(role=UserRole.USER)
    auth_as(pessoa)
    assert await _caixas_que_abre(client) == 403, "sem ligar, as Caixas não abrem"

    auth_as(cena.dono)
    r = await _put(client, pessoa, caixas=[cena.geral])
    assert r.status_code == 200, r.text
    assert r.json()["ligado"] is True and r.json()["caixas"] == [str(cena.geral.id)]
    await db.refresh(pessoa)
    assert pessoa.permissions["atendimento_email_leitura"]["view"] is True
    assert await _leitores(db, cena.geral) == [str(pessoa.id)]
    eventos = (
        await db.scalars(select(MailAdminEvento).where(MailAdminEvento.acao.like("leitor_%")))
    ).all()
    assert [(e.mailbox_id, e.acao, e.dados) for e in eventos] == [
        (cena.geral.id, "leitor_incluido", {"user_id": str(pessoa.id)})
    ]
    auth_as(pessoa)
    assert await _caixas_que_abre(client) == [str(cena.geral.id)], "só a caixa marcada"
    assert (await client.get(f"/api/mail/mailboxes/{cena.goslin.id}/messages")).status_code == 404

    auth_as(cena.dono)
    r = await _put(client, pessoa, caixas=[cena.geral, cena.goslin])
    assert r.json()["todas"] is True
    r = await _put(client, pessoa, caixas=[cena.goslin])
    assert r.json()["caixas"] == [str(cena.goslin.id)]
    assert await _leitores(db, cena.geral) == []
    auth_as(pessoa)
    assert await _caixas_que_abre(client) == [str(cena.goslin.id)]

    auth_as(cena.dono)
    r = await _put(client, pessoa, ligado=False, caixas=[cena.goslin])
    assert r.status_code == 200 and r.json()["ligado"] is False
    await db.refresh(pessoa)
    auth_as(pessoa)
    assert await _caixas_que_abre(client) == 403, "desligado: a aba não abre as Caixas"


async def test_desligar_tira_tambem_a_operacao(db, client, cena, make_user, auth_as):
    pessoa = await make_user(
        role=UserRole.USER,
        permissions={
            **LEITURA,
            "atendimento_email_operacao": {"view": True, "edit": True, "delete": True},
            "atendimento_email_aba": {"view": True, "edit": False, "delete": False},
        },
    )
    auth_as(cena.dono)
    assert (await _put(client, pessoa, ligado=False)).status_code == 200
    await db.refresh(pessoa)
    assert pessoa.permissions["atendimento_email_leitura"]["view"] is False
    assert pessoa.permissions["atendimento_email_operacao"]["view"] is False
    # As Filas das lojas (outra permissão, a do atendimento) não mudam.
    assert pessoa.permissions["atendimento_email_aba"]["view"] is True


async def test_recusas(db, client, cena, make_user, auth_as):
    auth_as(cena.dono)
    outro_admin = await make_user(role=UserRole.ADMIN)
    r = await _put(client, outro_admin, caixas=[cena.geral])
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "admin_ve_todas")
    parado = await make_user(role=UserRole.USER)
    parado.status = UserStatus.SUSPENDED
    await db.commit()
    r = await _put(client, parado, caixas=[cena.geral])
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "usuario_inativo")
    assert (await _put(client, parado, ligado=False)).status_code == 200
    pessoa = await make_user(role=UserRole.USER)
    r = await client.put(
        f"{ADMIN}/{pessoa.id}", json={"ligado": True, "caixas": [{"id": str(uuid4())}]}
    )
    assert (r.status_code, r.json()["detail"]["code"]) == (404, "caixa_nao_encontrada")
    r = await client.put(f"{ADMIN}/{uuid4()}", json={"ligado": True, "caixas": []})
    assert r.status_code == 404
    assert await _leitores(db, cena.geral) == []


async def test_dono_da_caixa_nao_entra_na_lista_de_leitores(db, client, cena, make_user, auth_as):
    pessoa = await make_user(role=UserRole.USER)
    cena.goslin.owner_user_id = pessoa.id
    await db.commit()
    auth_as(cena.dono)
    r = await _put(client, pessoa, caixas=[cena.goslin])
    assert r.status_code == 200 and r.json()["caixas"] == [str(cena.goslin.id)]
    assert await _leitores(db, cena.goslin) == []


async def test_email_privado_e_de_seguranca_so_para_admin_e_dono(
    db, client, cena, make_user, auth_as
):
    """A caixa privada do dono (Goslin: "só aliases de loja"): quem foi marcado
    nela vê só o e-mail das lojas — nunca o privado nem o código de acesso."""
    privado = await entrar(
        db, cena.goslin, para=["goslin@tuta.com"], assunto="Extrato do banco", recebido_em=_min(1)
    )
    loja = await entrar(
        db, cena.goslin, pasta="problema ml", para=["mia30@tuta.com"], recebido_em=_min(2)
    )
    seguranca = await entrar(
        db,
        cena.goslin,
        pasta="problema ml",
        para=["mia30@tuta.com"],
        assunto="Código de verificação 731604: acesse sua conta",
        corpo="Use o código 731604 para entrar.",
        recebido_em=_min(3),
    )
    sem_indice = await entrar(
        db, cena.goslin, para=["mia30@tuta.com"], assunto="Chegou agora", recebido_em=_min(0)
    )
    await indice.rodar(db)
    await db.delete(await db.get(MailCaixaIndice, sem_indice))
    await db.commit()

    pessoa = await make_user(role=UserRole.USER, permissions=LEITURA)
    auth_as(cena.dono)
    assert (await _put(client, pessoa, caixas=[cena.goslin])).status_code == 200

    async def visiveis(user) -> set:
        return set(
            await db.scalars(
                select(MailMessage.id).where(
                    MailMessage.mailbox_id == cena.goslin.id,
                    administracao.filtro_da_caixa(user),
                )
            )
        )

    todos = {privado, loja, seguranca, sem_indice}
    assert await visiveis(pessoa) == {loja}, "só o e-mail da loja; sem índice = fechado"
    assert await visiveis(cena.dono) == todos, "admin vê tudo"
    auth_as(pessoa)
    r = await client.get(f"/api/mail/mailboxes/{cena.goslin.id}/messages")
    assert r.status_code == 200, r.text
    assert [i["id"] for i in r.json()["items"]] == [str(loja)]
    assert "Extrato do banco" not in r.text and "731604" not in r.text
    for mid in (privado, seguranca, sem_indice):
        assert (await client.get(f"/api/mail/messages/{mid}")).status_code == 404
    assert (await client.get(f"/api/mail/messages/{loja}")).status_code == 200
    lista = await client.get(f"/api/mail/mailboxes/{cena.goslin.id}/lista")
    assert lista.status_code == 200, lista.text
    # A lista indexa na hora o que faltava: o recém-chegado (alias de loja) vira
    # "sem loja" e aparece; o privado e o de segurança, nunca.
    assert {i["id"] for i in lista.json()["itens"]} == {str(loja), str(sem_indice)}
    assert "Extrato do banco" not in lista.text and "731604" not in lista.text

    # O dono (não admin) da caixa vê o privado dele.
    dono = await make_user(role=UserRole.USER, permissions=LEITURA)
    cena.goslin.owner_user_id = dono.id
    await db.commit()
    assert {privado, loja} <= await visiveis(dono)
    assert await visiveis(pessoa) == {loja, sem_indice}


async def test_pastas_marcadas_de_uma_conta(db, client, cena, make_user, auth_as):
    """O Eduardo (12/11/2026): os checkboxes são também as PASTAS (informativo,
    SAC dos sites, as pastas das lojas). Marcar só "problema ml" = só ela."""
    ml = await entrar(
        db, cena.goslin, pasta="problema ml", para=["mia30@tuta.com"], recebido_em=_min(1)
    )
    shopee = await entrar(
        db, cena.goslin, pasta="problema shopee", para=["mia30@tuta.com"], recebido_em=_min(2)
    )
    await indice.rodar(db)
    chave_ml = await db.scalar(
        select(MailCaixaIndice.pasta_chave).where(MailCaixaIndice.message_id == ml)
    )
    pessoa = await make_user(role=UserRole.USER, permissions=LEITURA)
    auth_as(cena.dono)
    lista = (await client.get(ADMIN)).json()
    goslin = next(c for c in lista["caixas"] if c["id"] == str(cena.goslin.id))
    assert {p["nome"] for p in goslin["pastas"]} >= {"problema ml", "problema shopee"}
    assert chave_ml in {p["id"] for p in goslin["pastas"]}

    r = await _put(client, pessoa, pastas={cena.goslin: [chave_ml]})
    assert r.status_code == 200, r.text
    assert r.json()["caixas"] == [str(cena.goslin.id)]
    assert r.json()["pastas"] == {str(cena.goslin.id): [chave_ml]}
    assert r.json()["todas"] is False
    auth_as(pessoa)
    itens = (await client.get(f"/api/mail/mailboxes/{cena.goslin.id}/messages")).json()["items"]
    assert [i["id"] for i in itens] == [str(ml)]
    assert (await client.get(f"/api/mail/messages/{shopee}")).status_code == 404
    pastas = (await client.get(f"/api/mail/mailboxes/{cena.goslin.id}/pastas")).json()["pastas"]
    assert "problema shopee" not in {p["nome"] for p in pastas}

    # A conta inteira de novo: a linha das pastas some e ela vê as duas.
    auth_as(cena.dono)
    r = await _put(client, pessoa, caixas=[cena.goslin])
    assert r.json()["pastas"] == {}
    assert await db.scalar(select(MailLeitorPastas.user_id)) is None
    auth_as(pessoa)
    itens = (await client.get(f"/api/mail/mailboxes/{cena.goslin.id}/messages")).json()["items"]
    assert {i["id"] for i in itens} == {str(ml), str(shopee)}

    # Lista vazia ou chave fora do formato: recusa, nada muda.
    auth_as(cena.dono)
    assert (await _put(client, pessoa, pastas={cena.goslin: []})).status_code == 422
    assert (await _put(client, pessoa, pastas={cena.goslin: ["../x"]})).status_code == 422
    q = select(MailAdminEvento.acao).where(MailAdminEvento.acao == "leitor_pastas")
    eventos = (await db.scalars(q)).all()
    assert len(eventos) == 2
