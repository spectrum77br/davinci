# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte
"""Lixeira das filas: transação, escopo e snapshot, só no PostgreSQL de testes."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import AtendimentoConversa, AtendimentoMensagem, UserRole
from app.models.mail import MailMessage, MailOutbox
from app.models.mail_admin import MailAcesso, MailAdminEvento, MailLixeira
from app.models.mail_atendimento import MailMailboxSettings, MailMessageMeta
from app.routers import atendimento_email_lixeira as router
from app.security.cipher import encrypt_json
from app.services.atendimento import gravar
from tests.lojas_da_pessoa import dar_lojas
from tests.test_mail_ponte import AGORA, _config, cena
from tests.test_mail_venda_rotas import _caso, _chat


def _url(conversa_id):
    return f"/api/atendimento/email/conversas/{conversa_id}/lixeira"


async def _snapshot(db, conversa_id):
    ate = await db.scalar(
        select(func.max(AtendimentoMensagem.created_at)).where(
            AtendimentoMensagem.conversa_id == conversa_id
        )
    )
    return {"ate": ate.isoformat()}


async def _excluidos(db):
    return set(
        await db.scalars(select(MailLixeira.message_id).where(MailLixeira.excluido_em.is_not(None)))
    )


async def _na_fila(client, conversa_id):
    response = await client.get("/api/atendimento/conversas", params={"caixa": "email"})
    assert response.status_code == 200, response.text
    return str(conversa_id) in {item["id"] for item in response.json()["itens"]}


async def test_excluir_e_restaurar_todos_inclui_copia_sem_meta(db, cena, client, auth_as):
    primeiro = await _caso(db, cena)
    segundo = await _caso(db, cena)
    outro = await _caso(db, cena, caso="99999999")
    cid, ids = primeiro.conversa_id, {primeiro.message_id, segundo.message_id}
    assert segundo.conversa_id == cid and outro.conversa_id != cid
    originais = {mid: bytes((await db.get(MailMessage, mid)).content_enc) for mid in ids}
    # Reprocessamento pode retirar a meta; a cópia permanece na timeline.
    await db.delete(segundo)
    await db.commit()
    auth_as(cena.dono)
    assert await _na_fila(client, cid)
    response = await client.post(
        _url(cid),
        json={
            **await _snapshot(db, cid),
            "motivo": "Duplicado visual",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"emails": 2}
    assert await _excluidos(db) == ids
    assert not await _na_fila(client, cid)
    assert await _na_fila(client, outro.conversa_id)
    lixeira = await client.get(f"/api/mail/mailboxes/{cena.geral.id}/trash")
    assert {item["id"] for item in lixeira.json()["items"]} == {str(mid) for mid in ids}
    for mid in ids:
        assert (await client.post(f"/api/mail/messages/{mid}/restore")).status_code == 200
        await db.refresh(await db.get(MailMessage, mid))
        assert bytes((await db.get(MailMessage, mid)).content_enc) == originais[mid]
    assert await _na_fila(client, cid)
    assert await _excluidos(db) == set()
    await db.refresh(primeiro)
    assert primeiro.conversa_id == cid and primeiro.estado == "gravado"
    events = list(
        await db.scalars(select(MailAdminEvento).where(MailAdminEvento.acao.like("email_%")))
    )
    assert len(events) == 4
    assert all(set(event.dados) == {"message_id"} for event in events)


@pytest.mark.parametrize("estado", ["queued", "leased", "uncertain"])
async def test_envio_pendente_recusa_lote_inteiro(db, cena, client, auth_as, estado):
    primeiro, segundo = await _caso(db, cena), await _caso(db, cena)
    job = MailOutbox(
        mailbox_id=cena.geral.id,
        message_id=segundo.message_id,
        author_user_id=cena.dono.id,
        request_id=uuid4(),
        content_enc=encrypt_json({}),
        status=estado,
    )
    db.add(job)
    await db.commit()
    auth_as(cena.dono)
    response = await client.post(
        _url(primeiro.conversa_id),
        json=await _snapshot(
            db,
            primeiro.conversa_id,
        ),
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "email_envio_pendente"
    assert await _excluidos(db) == set()
    await db.refresh(job)
    assert job.status == estado


async def test_nova_chegada_com_data_de_envio_antiga_preserva_toda_conversa(
    db,
    cena,
    client,
    auth_as,
):
    primeiro = await _caso(db, cena)
    snapshot = await _snapshot(db, primeiro.conversa_id)
    segundo = await _caso(db, cena, recebido_em=AGORA - timedelta(minutes=20))
    assert primeiro.conversa_id == segundo.conversa_id
    auth_as(cena.dono)
    response = await client.post(_url(primeiro.conversa_id), json=snapshot)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "email_conversa_mudou"
    assert await _excluidos(db) == set()
    assert await _na_fila(client, primeiro.conversa_id)


@pytest.mark.parametrize(
    "permissions",
    [
        {"atendimento_email_aba": {"view": True}},
        {"atendimento_email_operacao": {"view": True, "edit": True, "delete": False}},
    ],
)
async def test_leitor_e_operador_sem_delete_nao_excluem(
    db,
    cena,
    client,
    auth_as,
    make_user,
    permissions,
):
    meta = await _caso(db, cena)
    pessoa = await make_user(role=UserRole.USER, permissions=permissions)
    auth_as(pessoa)
    response = await client.post(_url(meta.conversa_id), json=await _snapshot(db, meta.conversa_id))
    assert response.status_code == 403
    assert await _excluidos(db) == set()


async def test_admin_fora_da_lista_de_operacao_nao_exclui(
    db,
    cena,
    client,
    auth_as,
    monkeypatch,
):
    from app.config import get_settings

    meta = await _caso(db, cena)
    auth_as(cena.dono)
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "outro@teste.invalid")
    response = await client.post(_url(meta.conversa_id), json=await _snapshot(db, meta.conversa_id))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "atendimento_permission_required"
    assert await _excluidos(db) == set()


@pytest.mark.parametrize("restricao", ["nenhuma", "loja", "caixa", "politica"])
async def test_operador_explicito_mantem_escopo_e_acesso_da_caixa(
    db,
    cena,
    client,
    auth_as,
    make_user,
    restricao,
):
    meta = await _caso(db, cena)
    pessoa = await make_user(
        role=UserRole.USER,
        permissions={
            "atendimento_email_operacao": {"view": True, "edit": True, "delete": True},
        },
    )
    await dar_lojas(db, pessoa, cena.oliveira_ml if restricao == "loja" else cena.ml)
    if restricao != "caixa":
        cfg = await db.get(MailMailboxSettings, cena.geral.id)
        cfg.leitores = [str(pessoa.id)]
    if restricao == "politica":
        db.add(MailAcesso(mailbox_id=cena.geral.id, user_id=pessoa.id, modo="bloqueado"))
    await db.commit()
    auth_as(pessoa)
    response = await client.post(_url(meta.conversa_id), json=await _snapshot(db, meta.conversa_id))
    assert response.status_code == (200 if restricao == "nenhuma" else 404), response.text
    assert await _excluidos(db) == ({meta.message_id} if restricao == "nenhuma" else set())


async def test_snapshot_obrigatorio_com_fuso_e_atual(db, cena, client, auth_as):
    meta = await _caso(db, cena)
    auth_as(cena.dono)
    for body in ({}, {"ate": None}, {"ate": "2026-11-10T00:00:00"}):
        assert (await client.post(_url(meta.conversa_id), json=body)).status_code == 422
    assert (
        await client.post(
            _url(meta.conversa_id),
            json={
                "ate": (AGORA + timedelta(days=1)).isoformat(),
            },
        )
    ).status_code == 409
    chat = await _chat(db, cena)
    response = await client.post(_url(chat.id), json={"ate": AGORA.isoformat()})
    assert (
        response.status_code == 409
        and response.json()["detail"]["code"] == "email_conversa_invalida"
    )
    assert await _excluidos(db) == set()


async def test_erro_na_segunda_mensagem_desfaz_primeira_e_auditoria(
    db,
    cena,
    client,
    auth_as,
    monkeypatch,
):
    primeiro = await _caso(db, cena)
    await _caso(db, cena)
    original = router.administracao.mudar_lixeira
    chamadas = 0

    async def falhar_na_segunda(*args, **kwargs):
        nonlocal chamadas
        chamadas += 1
        if chamadas == 2:
            raise HTTPException(409, detail={"code": "email_envio_pendente"})
        return await original(*args, **kwargs)

    monkeypatch.setattr(router.administracao, "mudar_lixeira", falhar_na_segunda)
    auth_as(cena.dono)
    response = await client.post(
        _url(primeiro.conversa_id),
        json=await _snapshot(
            db,
            primeiro.conversa_id,
        ),
    )
    assert response.status_code == 409 and chamadas == 2
    assert await _excluidos(db) == set()
    assert await db.scalar(select(func.count()).select_from(MailAdminEvento)) == 0


async def test_snapshot_e_relido_depois_de_esperar_a_trava_do_sync(
    db,
    cena,
    client,
    auth_as,
    monkeypatch,
):
    meta = await _caso(db, cena)
    snapshot = await _snapshot(db, meta.conversa_id)
    conversa = await db.get(AtendimentoConversa, meta.conversa_id)
    await gravar.travar_linha(db, conversa)
    chegou_na_trava = asyncio.Event()
    original = router._travar_ou_409

    async def travar(*args, **kwargs):
        chegou_na_trava.set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(router, "_travar_ou_409", travar)
    auth_as(cena.dono)
    task = asyncio.create_task(client.post(_url(conversa.id), json=snapshot))
    try:
        await asyncio.wait_for(chegou_na_trava.wait(), timeout=5)
        await gravar.gravar_mensagem(
            db,
            conversa,
            externo_id=f"nova:{uuid4()}",
            autor="cliente",
            texto="Atualização recebida enquanto a tela estava aberta",
            enviada_em=AGORA,
        )
        await db.commit()
        response = await asyncio.wait_for(task, timeout=10)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "email_conversa_mudou"
        assert await _excluidos(db) == set()
    finally:
        if not task.done():
            task.cancel()
        await db.rollback()
