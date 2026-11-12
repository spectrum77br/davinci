# ruff: noqa: F401, F811 — fixtures da ponte compartilhadas
"""A lixeira local protege também as cópias dos e-mails no Atendimento.

(12/11/2026: a regra por pessoa × caixa × loja saiu com a administração antiga;
ficam aqui a leitura da equipe e a lixeira.)

Estes cenários usam mensagens cifradas reais da Central e a ponte de verdade;
não substituem os filtros de autorização nem inferem acesso pelo comprador.
"""

import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    MailAttachment,
    MailMessage,
    MailOutbox,
    StoreInfo,
    UserRole,
)
from app.models.mail_atendimento import MailFolder, MailOutboxMeta
from app.security.cipher import encrypt_json
from app.services.mail_atendimento import saude
from tests.test_atendimento_separa_email import (
    _ids_pela_barra,
    _lista_inteira_recusada,
    _na_barra,
    _tipo_email,
)
from tests.test_mail_ponte import _config, cena, entrar, meta_de, pedido_existe, rodar
from tests.test_mail_venda_rotas import X, Y, _caso, _chat

ATENDIMENTO = "/api/atendimento"
EMAIL = f"{ATENDIMENTO}/email"


async def _tipo(db, meta, tipo):
    mensagem = await db.get(AtendimentoMensagem, meta.mensagem_id)
    mensagem.payload = {
        **mensagem.payload,
        "mail": {**mensagem.payload["mail"], "tipo_email": tipo},
    }
    c = await db.get(AtendimentoConversa, meta.conversa_id)
    c.dados = {**c.dados, "mail": {**c.dados["mail"], "tipo_email": tipo}}
    await db.commit()


async def _saude_de(db, client, user) -> dict:
    """A saúde das caixas como a PESSOA veria. Desde 27/10/2026 a rota (só da
    aba E-mail) é só de admin — 403 para ela; a régua por pessoa (a política
    de acesso) continua no serviço, que é o que se confere aqui."""
    r = await client.get(f"{EMAIL}/saude")
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "aba_email_so_admin"
    caixas = await saude.caixas_visiveis(db, user)
    return {"caixas": [await saude.linha_da_caixa(db, c, user=user) for c in caixas]}


async def _id_anexo(db, mid):
    return await db.scalar(select(MailAttachment.id).where(MailAttachment.message_id == mid))


async def _negado_na_conversa(client, cid):
    for path in (
        f"{ATENDIMENTO}/conversas/{cid}",
        f"{ATENDIMENTO}/conversas/{cid}/abas",
        f"{EMAIL}/conversas/{cid}/emails",
        f"{EMAIL}/conversas/{cid}/emails-da-venda",
        f"{EMAIL}/conversas/{cid}/previa",
    ):
        r = await client.get(path)
        assert r.status_code == 404, (path, r.status_code, r.text)


async def test_sem_politica_mantem_leitura_do_atendimento_sem_liberar_caixa(
    db, cena, client, make_user, auth_as
):
    meta = await _caso(db, cena)
    leitor = await make_user(role=UserRole.USER)
    auth_as(leitor)

    cards = await client.get(f"{EMAIL}/conversas/{meta.conversa_id}/emails")
    assert cards.status_code == 200, cards.text
    assert [c["id"] for c in cards.json()["emails"]] == [str(meta.message_id)]
    assert (await client.get(f"{ATENDIMENTO}/conversas/{meta.conversa_id}")).status_code == 200
    assert (await client.get(f"/api/mail/messages/{meta.message_id}")).status_code == 403


async def test_lixeira_local_tira_copias_anexos_e_contagens_e_restaura_sem_apagar(
    db, cena, client, make_user, auth_as
):
    await pedido_existe(db, cena.ml, X)
    meta = await _caso(
        db, cena, corpo="CORPO A RESTAURAR", anexos=[("nota.txt", "text/plain", b"NOTA")]
    )
    chat = await _chat(db, cena)
    msg = await db.get(MailMessage, meta.message_id)
    copia = await db.get(AtendimentoMensagem, meta.mensagem_id)
    original, texto_copiado, estado = msg.content_enc, copia.texto, meta.estado
    aid = await _id_anexo(db, meta.message_id)
    auth_as(cena.dono)
    excluded = await client.post(
        f"/api/mail/messages/{meta.message_id}/trash", json={"motivo": "Email duplicado de teste"}
    )
    assert excluded.status_code == 200, excluded.text

    # Admin também não vê o conteúdo apagado pelas rotas normais.
    for user in (cena.dono, await make_user(role=UserRole.USER)):
        auth_as(user)
        await _negado_na_conversa(client, meta.conversa_id)
        assert (await client.get(f"{EMAIL}/anexos/{aid}")).status_code == 404
        status = 404 if user.role == UserRole.ADMIN else 403
        assert (await client.get(f"/api/mail/messages/{meta.message_id}")).status_code == status
        sale = await client.get(f"{EMAIL}/venda/{chat.id}")
        assert sale.status_code == 200 and sale.json()["total"] == 0
        counts = (await client.get(f"{EMAIL}/contagens")).json()
        assert counts["por_tipo_email"] == {"informativo": 0, "chamado": 0, "venda": 0}

    auth_as(cena.dono)
    trash = await client.get(f"/api/mail/mailboxes/{cena.geral.id}/trash")
    assert trash.status_code == 200, trash.text
    assert [i["id"] for i in trash.json()["items"]] == [str(meta.message_id)]
    assert "CORPO A RESTAURAR" not in trash.text
    # Lixeira é lateral e reversível; conector, ponte e cópia não foram destruídos.
    await db.refresh(msg)
    await db.refresh(copia)
    await db.refresh(meta)
    assert (msg.content_enc, copia.texto, meta.estado) == (original, texto_copiado, estado)
    restored = await client.post(f"/api/mail/messages/{meta.message_id}/restore")
    assert restored.status_code == 200, restored.text
    cards = await client.get(f"{EMAIL}/conversas/{meta.conversa_id}/emails")
    assert cards.status_code == 200 and "CORPO A RESTAURAR" in cards.text
    assert (await client.get(f"{EMAIL}/anexos/{aid}")).content == b"NOTA"
    assert (await client.get(f"{EMAIL}/venda/{chat.id}")).json()["total"] == 1


@pytest.mark.parametrize("apos_reprocessar", ["ignorado", "sem_meta"])
async def test_lixeira_nao_reaparece_por_copia_apos_meta_ser_reprocessada(
    db, cena, client, auth_as, apos_reprocessar
):
    meta = await _caso(db, cena, corpo="COPIA QUE NAO PODE REAPARECER")
    cid, mid = meta.conversa_id, meta.message_id
    auth_as(cena.dono)
    r = await client.post(f"/api/mail/messages/{mid}/trash", json={})
    assert r.status_code == 200, r.text
    if apos_reprocessar == "sem_meta":
        await db.delete(meta)
    else:
        meta.estado = "ignorado"
    await db.commit()

    # Mesmo sem a ligação auxiliar, payload.mail da cópia aponta ao original.
    await _negado_na_conversa(client, cid)
    r = await client.get(f"{ATENDIMENTO}/conversas", params={"caixa": "email"})
    assert r.status_code == 200, r.text
    assert str(cid) not in r.text and "COPIA QUE NAO PODE REAPARECER" not in r.text
