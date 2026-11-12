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
from app.models.mail_admin import MailAdminEvento, MailLixeira
from app.models.mail_atendimento import MailMailboxSettings, MailMessageMeta
from app.routers import atendimento_email_lixeira as router
from app.security.cipher import encrypt_json
from app.services.atendimento import gravar
from tests.lojas_da_pessoa import dar_lojas
from tests.test_mail_ponte import AGORA, _config, cena, entrar
from tests.test_mail_venda_rotas import _caso, _chat

LOTE = "/api/atendimento/email/lixeira-lote"


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
    response = await client.post(
        LOTE,
        json={
            "tipo": "emails",
            "itens": [{"id": str(meta.message_id)}],
        },
    )
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
    response = await client.post(
        LOTE,
        json={
            "tipo": "emails",
            "itens": [{"id": str(meta.message_id)}],
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "atendimento_permission_required"
    assert await _excluidos(db) == set()


@pytest.mark.parametrize("restricao", ["nenhuma", "loja", "caixa"])
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


async def test_lote_conversas_preserva_atualizada_pendente_e_inexistente(
    db,
    cena,
    client,
    auth_as,
):
    bom = await _caso(db, cena, caso="11111111")
    atualizado = await _caso(db, cena, caso="22222222")
    pendente = await _caso(db, cena, caso="33333333")
    itens = [
        {"id": str(meta.conversa_id), **await _snapshot(db, meta.conversa_id)}
        for meta in (bom, atualizado, pendente)
    ]
    novo = await _caso(db, cena, caso="22222222", recebido_em=AGORA - timedelta(minutes=20))
    db.add(
        MailOutbox(
            mailbox_id=cena.geral.id,
            message_id=pendente.message_id,
            author_user_id=cena.dono.id,
            request_id=uuid4(),
            content_enc=encrypt_json({}),
            status="queued",
        )
    )
    await db.commit()
    desconhecido = str(uuid4())
    itens.append({"id": desconhecido, "ate": AGORA.isoformat()})
    auth_as(cena.dono)
    response = await client.post(LOTE, json={"tipo": "conversas", "itens": itens})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "excluidos": [str(bom.conversa_id)],
        "falhas": [
            {"id": str(atualizado.conversa_id), "code": "email_conversa_mudou"},
            {"id": str(pendente.conversa_id), "code": "email_envio_pendente"},
            {"id": desconhecido, "code": "conversa_nao_encontrada"},
        ],
    }
    assert await _excluidos(db) == {bom.message_id}
    assert await db.get(MailMessage, novo.message_id) is not None
    assert await _na_fila(client, atualizado.conversa_id)
    assert (await client.post(f"/api/mail/messages/{bom.message_id}/restore")).status_code == 200
    assert await _na_fila(client, bom.conversa_id)


@pytest.mark.parametrize("estado", ["queued", "leased", "uncertain"])
async def test_lote_emails_nao_inclui_novas_chegadas_e_pode_restaurar(
    db,
    cena,
    client,
    auth_as,
    estado,
):
    bom, pendente = await entrar(db, cena.geral), await entrar(db, cena.geral)
    original = bytes((await db.get(MailMessage, bom)).content_enc)
    itens = [{"id": str(bom)}, {"id": str(pendente)}]
    novo = await entrar(db, cena.geral)
    job = MailOutbox(
        mailbox_id=cena.geral.id,
        message_id=pendente,
        author_user_id=cena.dono.id,
        request_id=uuid4(),
        content_enc=encrypt_json({}),
        status=estado,
    )
    db.add(job)
    await db.commit()
    auth_as(cena.dono)
    for _ in range(2):
        response = await client.post(LOTE, json={"tipo": "emails", "itens": itens})
        assert response.status_code == 200, response.text
        assert response.json() == {
            "excluidos": [str(bom)],
            "falhas": [{"id": str(pendente), "code": "email_envio_pendente"}],
        }
    assert await _excluidos(db) == {bom}
    assert await db.get(MailMessage, novo) is not None
    await db.refresh(job)
    assert job.status == estado
    assert (await client.post(f"/api/mail/messages/{bom}/restore")).status_code == 200
    assert bytes((await db.get(MailMessage, bom)).content_enc) == original
    assert await _excluidos(db) == set()


@pytest.mark.parametrize("tipo", ["conversas", "emails"])
@pytest.mark.parametrize("restricao", ["nenhuma", "caixa", "loja"])
async def test_lote_mantem_permissao_e_escopo_da_operacao_individual(
    db,
    cena,
    client,
    auth_as,
    make_user,
    tipo,
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
    await db.commit()
    auth_as(pessoa)
    ident = meta.conversa_id if tipo == "conversas" else meta.message_id
    item = {"id": str(ident), **await _snapshot(db, meta.conversa_id)}
    response = await client.post(LOTE, json={"tipo": tipo, "itens": [item]})
    assert response.status_code == 200, response.text
    # E-mail original segue o mesmo grant de sua Caixa inteira; conversa
    # segue também o recorte por lojas do Atendimento.
    permitido = restricao == "nenhuma" or (tipo == "emails" and restricao == "loja")
    assert response.json()["excluidos"] == ([str(ident)] if permitido else [])
    assert len(response.json()["falhas"]) == (0 if permitido else 1)
    assert await _excluidos(db) == ({meta.message_id} if permitido else set())


@pytest.mark.parametrize(
    "invalido",
    [
        "vazio",
        "limite",
        "duplicado",
        "duplicado_caixa",
        "sem_ate",
        "ate_sem_fuso",
        "extra",
        "extra_item",
        "tipo",
        "id",
    ],
)
async def test_lote_valida_corpo_inteiro_antes_de_qualquer_exclusao(
    db,
    cena,
    client,
    auth_as,
    invalido,
):
    mid = await entrar(db, cena.geral)
    item = {"id": str(mid)}
    body = {"tipo": "emails", "itens": [item]}
    if invalido == "vazio":
        body["itens"] = []
    elif invalido == "limite":
        body["itens"] = [item, *({"id": str(uuid4())} for _ in range(100))]
    elif invalido in ("duplicado", "duplicado_caixa"):
        body["itens"].append(
            {"id": str(mid).upper() if invalido == "duplicado_caixa" else str(mid)}
        )
    elif invalido in ("sem_ate", "ate_sem_fuso"):
        body["tipo"] = "conversas"
        if invalido == "ate_sem_fuso":
            item["ate"] = "2026-11-10T00:00:00"
    elif invalido == "extra":
        body["todos"] = True
    elif invalido == "extra_item":
        item["todos"] = True
    elif invalido == "tipo":
        body["tipo"] = "todos"
    else:
        body["itens"].append({"id": "nao-e-uuid"})
    auth_as(cena.dono)
    response = await client.post(LOTE, json=body)
    assert response.status_code == 422, response.text
    assert await _excluidos(db) == set()


async def test_lote_falha_no_meio_da_conversa_desfaz_item_mas_preserva_outro(
    db,
    cena,
    client,
    auth_as,
    monkeypatch,
):
    primeiro = await _caso(db, cena, caso="11111111")
    segundo = await _caso(db, cena, caso="11111111")
    bom = await _caso(db, cena, caso="22222222")
    ids_falhos = sorted([primeiro.message_id, segundo.message_id])
    original = router.administracao.mudar_lixeira

    async def falhar_no_segundo(session, message_id, *args, **kwargs):
        if message_id == ids_falhos[1]:
            raise HTTPException(
                409,
                detail={
                    "code": "email_envio_pendente",
                    "detail": "corpo privado que não deve sair",
                },
            )
        return await original(session, message_id, *args, **kwargs)

    monkeypatch.setattr(router.administracao, "mudar_lixeira", falhar_no_segundo)
    auth_as(cena.dono)
    response = await client.post(
        LOTE,
        json={
            "tipo": "conversas",
            "itens": [
                {"id": str(meta.conversa_id), **await _snapshot(db, meta.conversa_id)}
                for meta in (primeiro, bom)
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "excluidos": [str(bom.conversa_id)],
        "falhas": [{"id": str(primeiro.conversa_id), "code": "email_envio_pendente"}],
    }
    assert "corpo privado" not in response.text
    assert await _excluidos(db) == {bom.message_id}
    events = list(await db.scalars(select(MailAdminEvento)))
    assert len(events) == 1 and events[0].dados == {"message_id": str(bom.message_id)}


async def test_lote_erro_inesperado_desfaz_todos_os_itens(db, cena, client, auth_as, monkeypatch):
    ids = sorted([await entrar(db, cena.geral), await entrar(db, cena.geral)])
    original = router.administracao.mudar_lixeira

    async def falhar_no_segundo(session, message_id, *args, **kwargs):
        if message_id == ids[1]:
            raise RuntimeError("falha simulada")
        return await original(session, message_id, *args, **kwargs)

    monkeypatch.setattr(router.administracao, "mudar_lixeira", falhar_no_segundo)
    auth_as(cena.dono)
    with pytest.raises(RuntimeError, match="falha simulada"):
        await client.post(LOTE, json={"tipo": "emails", "itens": [{"id": str(mid)} for mid in ids]})
    assert await _excluidos(db) == set()
    assert await db.scalar(select(func.count()).select_from(MailAdminEvento)) == 0
