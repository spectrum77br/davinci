# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e das rotas
"""Seleção integral: empates, mesmos filtros da lista e nenhuma mudança no e-mail."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from app.models import AtendimentoConversa, AtendimentoMensagem, MailMessage, UserRole
from app.models.mail_admin import MailLixeira
from app.models.mail_atendimento import MailMessageMeta
from app.routers import atendimento_email_selecao as router
from app.security.cipher import encrypt_json
from tests.lojas_da_pessoa import dar_lojas
from tests.test_atendimento_email_lidos_lote import _conversa
from tests.test_atendimento_separa_email import URL, admin
from tests.test_mail_ponte import _config, cena

SELECAO = f"{URL}/email/lixeira-selecao"
pytestmark = pytest.mark.lojas_por_pessoa
AGORA = datetime.now(UTC) - timedelta(minutes=5)


async def _raw(db, cena, estado="sem_loja", *, integration_id=None, suspeito=False,
               finalidade=None, ilegivel=False):
    ident = uuid4()
    db.add(MailMessage(
        id=ident, mailbox_id=cena.geral.id, source_id=f"tuta:{ident}", direction="inbound",
        received_at=AGORA,
        content_enc=b"conteudo-ilegivel" if ilegivel else encrypt_json({
            "subject": "Aviso", "text": "Texto de teste", "from_address": "a@b.test", "to": []
        }),
    ))
    db.add(MailMessageMeta(
        message_id=ident, mailbox_id=cena.geral.id, estado=estado,
        integration_id=integration_id, suspeito=suspeito, finalidade=finalidade,
    ))
    await db.commit()
    return ident


@pytest.mark.parametrize("tipo", ["conversas", "emails"])
async def test_selecao_inclui_todos_os_empates_sem_ler_corpo_ou_alterar(
    db, cena, client, admin, tipo
):
    if tipo == "conversas":
        ids = {(await _conversa(db)).id for _ in range(55)}
        await db.execute(update(AtendimentoConversa).where(AtendimentoConversa.id.in_(ids)).values(
            ultima_mensagem_em=AGORA,
        ))
        await db.execute(update(AtendimentoMensagem).where(
            AtendimentoMensagem.conversa_id.in_(ids)
        ).values(created_at=AGORA))
        await db.commit()
        body = {"fila": "chamados"}
    else:
        # A seleção não precisa nem decifrar um e-mail ilegível.
        ids = {await _raw(db, cena, ilegivel=True) for _ in range(55)}
        body = {"fila": "sem_loja"}
    antes = (await db.execute(select(
        AtendimentoConversa.id, AtendimentoConversa.visto_em, AtendimentoConversa.updated_at,
    ))).all()
    r = await client.post(SELECAO, json=body)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["tipo"] == tipo
    assert {i["id"] for i in r.json()["itens"]} == {str(i) for i in ids}
    assert len(r.json()["itens"]) == 55
    for item in r.json()["itens"]:
        assert set(item) == ({"id", "ate"} if tipo == "conversas" else {"id"})
        if tipo == "conversas":
            assert datetime.fromisoformat(item["ate"].replace("Z", "+00:00")) == AGORA
    assert (await db.execute(select(
        AtendimentoConversa.id, AtendimentoConversa.visto_em, AtendimentoConversa.updated_at,
    ))).all() == antes
    assert await db.scalar(select(func.count()).select_from(MailLixeira)) == 0


@pytest.mark.parametrize("body,params", [
    ({"fila": "chamados", "tipo_chamado": "sac", "q": "US-26-1234"},
     {"plataforma": "site", "tipo_chamado": "sac", "q": "US-26-1234"}),
    ({"fila": "chamados", "tipo_chamado": "atacado"},
     {"plataforma": "site", "tipo_chamado": "atacado"}),
    ({"fila": "todas", "tipo_email": "informativo"}, {"tipo_email": "informativo"}),
    ({"fila": "todas", "fechadas": True}, {"filtro": "fechadas"}),
    ({"fila": "aguardando"}, {"filtro": "aguardando"}),
    ({"fila": "todas", "q": "100%_literal"}, {"q": "100%_literal"}),
])
async def test_selecao_conversas_tem_o_mesmo_recorte_da_lista(
    db, cena, client, admin, body, params
):
    await _conversa(db, protocolo="US-26-1234")
    await _conversa(db, caixa="atacado", protocolo="UA-26-1234")
    await _conversa(db, tipo="informativo", nome="100%_literal")
    await _conversa(db, situacao="fechada", aguardando=False)
    await _conversa(db, aguardando=False)
    await _conversa(db, plataforma="ml", integration_id=cena.ml.id, nome="100XXliteral")
    await _conversa(db, canal="chat", protocolo="US-26-1234")
    lista = await client.get(f"{URL}/conversas", params={"caixa": "email", **params})
    assert lista.status_code == 200, lista.text
    esperados = {i["id"] for i in lista.json()["itens"]}
    r = await client.post(SELECAO, json=body)
    assert r.status_code == 200, r.text
    assert {i["id"] for i in r.json()["itens"]} == esperados


@pytest.mark.parametrize("fila,tipo", [
    ("sem_loja", None), ("sem_vinculo", "chamado"), ("suspeito", None),
    ("resumo", "informativo"), ("erro", None),
])
async def test_selecao_emails_tem_o_mesmo_recorte_da_lista(db, cena, client, admin, fila, tipo):
    await _raw(db, cena)
    await _raw(db, cena, estado="sem_vinculo", finalidade="mensagens")
    await _raw(db, cena, estado="gravado", suspeito=True, finalidade="problema")
    await _raw(db, cena, estado="resumo")
    await _raw(db, cena, estado="erro")
    body = {"fila": fila, **({"tipo_email": tipo} if tipo else {})}
    lista = await client.get(f"{URL}/email/emails", params=body)
    assert lista.status_code == 200, lista.text
    esperados = {i["id"] for i in lista.json()["itens"]}
    assert esperados
    r = await client.post(SELECAO, json=body)
    assert r.status_code == 200, r.text
    assert {i["id"] for i in r.json()["itens"]} == esperados


async def test_selecao_respeita_lojas_acesso_da_caixa_e_lixeira(
    db, cena, client, make_user, auth_as
):
    grant = {"atendimento_email_operacao": {"view": True, "edit": True, "delete": True},
             "atendimento_email_aba": {"view": True}}
    user = await make_user(permissions=grant)
    await dar_lojas(db, user, cena.ml)
    minha = await _conversa(db, plataforma="ml", integration_id=cena.ml.id)
    await _conversa(db, plataforma="shopee", integration_id=cena.shopee.id)
    await _conversa(db)
    await _conversa(db, plataforma="ml", canal="chat", integration_id=cena.ml.id)
    meu = await _raw(db, cena, integration_id=cena.ml.id)
    await _raw(db, cena, integration_id=cena.shopee.id)
    apagado = await _raw(db, cena, integration_id=cena.ml.id)
    db.add(MailLixeira(message_id=apagado, excluido_em=AGORA))
    await db.commit()
    auth_as(user)
    r = await client.post(SELECAO, json={"fila": "todas"})
    assert r.status_code == 200, r.text
    assert {i["id"] for i in r.json()["itens"]} == {str(minha.id)}
    r = await client.post(SELECAO, json={"fila": "sem_loja"})
    assert r.status_code == 200, r.text
    assert r.json()["itens"] == [{"id": str(meu)}]


@pytest.mark.parametrize("permissions", [{}, {"atendimento_email_aba": {"view": True}},
    {"atendimento_email_operacao": {"view": True, "edit": True}}])
async def test_selecao_exige_permissao_de_excluir(client, make_user, auth_as, permissions):
    auth_as(await make_user(role=UserRole.USER, permissions=permissions))
    r = await client.post(SELECAO, json={"fila": "todas"})
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("tipo", ["conversas", "emails"])
async def test_limite_recusa_a_selecao_inteira(db, cena, client, admin, monkeypatch, tipo):
    monkeypatch.setattr(router, "MAX_SELECAO", 2)
    for _ in range(3):
        await (_conversa(db) if tipo == "conversas" else _raw(db, cena))
    r = await client.post(SELECAO, json={"fila": "todas" if tipo == "conversas" else "sem_loja"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "email_selecao_grande", r.text
    assert "itens" not in r.json()


@pytest.mark.parametrize("body", [
    {}, {"fila": "revisar_envio"}, {"fila": "sem_loja", "q": "cliente"},
    {"fila": "erro", "tipo_chamado": "sac"}, {"fila": "chamados", "fechadas": True},
    {"fila": "todas", "tipo_email": "outro"}, {"fila": "todas", "q": "x"*201},
    {"fila": "todas", "limite": 100},
])
async def test_filtros_invalidos_nao_alargam_selecao(client, admin, body):
    r = await client.post(SELECAO, json=body)
    assert r.status_code == 422, r.text
