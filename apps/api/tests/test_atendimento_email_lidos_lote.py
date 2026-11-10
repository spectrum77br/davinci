# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e das rotas
"""Marcar a fila inteira: paginação, filtros, escopo e chegada posterior."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text

import app.db as app_db
from app.models import AtendimentoConversa, AtendimentoMensagem, UserRole
from tests.lojas_da_pessoa import dar_lojas
from tests.test_atendimento_separa_email import URL, admin
from tests.test_mail_ponte import _config, cena

LOTE = f"{URL}/email/marcar-todos-lidos"
pytestmark = pytest.mark.lojas_por_pessoa


async def _conversa(
    db, *, plataforma="site", canal="email", integration_id=None, tipo="chamado",
    caixa="sac", situacao="aberta", aguardando=True, nome="Cliente", protocolo=None,
):
    agora = datetime.now(UTC)
    c = AtendimentoConversa(
        id=uuid4(), plataforma=plataforma, canal=canal, integration_id=integration_id,
        externo_id=uuid4().hex, comprador_nome=nome, ultima_mensagem_em=agora-timedelta(days=2),
        ultima_do_cliente_em=agora-timedelta(days=2), ultima_autor="cliente",
        aguardando_resposta=aguardando, situacao=situacao,
        dados={"fonte": "tuta", "mail": {"tipo_email": tipo, "caixa": caixa,
                                       "protocolo": protocolo}},
    )
    db.add(c)
    await db.flush()
    db.add(AtendimentoMensagem(
        conversa_id=c.id, externo_id=uuid4().hex, autor="cliente", origem="cliente",
        texto="E-mail de teste", enviada_em=agora-timedelta(days=2),
        created_at=agora-timedelta(minutes=1),
    ))
    await db.commit()
    return c


async def _marcadas(db):
    return set((await db.scalars(
        select(AtendimentoConversa.id).where(AtendimentoConversa.visto_em.is_not(None))
    )).all())


async def test_lote_passa_da_primeira_pagina_sem_resolver_e_repetir(db, cena, client, admin):
    alvos = [await _conversa(db) for _ in range(55)]
    chat = await _conversa(db, canal="chat")
    fechada = await _conversa(db, situacao="fechada")
    loja = await _conversa(db, plataforma="ml", integration_id=cena.ml.id)
    antes = (await db.execute(select(
        AtendimentoConversa.id, AtendimentoConversa.updated_at,
        AtendimentoConversa.situacao, AtendimentoConversa.aguardando_resposta,
    ))).all()
    lista = await client.get(f"{URL}/conversas", params={"caixa": "email", "plataforma": "site"})
    assert lista.status_code == 200, lista.text
    assert len(lista.json()["itens"]) == 50 and lista.json()["proximo"]
    r = await client.post(LOTE, json={"fila": "chamados"})
    assert r.status_code == 200, r.text
    assert r.json() == {"conversas": 55}
    assert await _marcadas(db) == {c.id for c in alvos}
    assert not {chat.id, fechada.id, loja.id} & await _marcadas(db)
    depois = (await db.execute(select(
        AtendimentoConversa.id, AtendimentoConversa.updated_at,
        AtendimentoConversa.situacao, AtendimentoConversa.aguardando_resposta,
    ))).all()
    assert set(antes) == set(depois)
    assert (await client.post(LOTE, json={"fila": "chamados"})).json() == {"conversas": 0}

    # O envio é antigo, mas a chegada depois do lote mantém a conversa não lida.
    nova = AtendimentoMensagem(
        conversa_id=alvos[0].id, externo_id=uuid4().hex, autor="cliente", origem="cliente",
        texto="Importação posterior", enviada_em=datetime.now(UTC)-timedelta(days=3),
    )
    db.add(nova)
    await db.commit()
    visto = await db.scalar(select(AtendimentoConversa.visto_em).where(
        AtendimentoConversa.id == alvos[0].id
    ))
    assert nova.created_at > visto
    assert (await client.post(LOTE, json={"fila": "chamados"})).json() == {"conversas": 1}


@pytest.mark.parametrize("body,params", [
    ({"fila": "chamados", "tipo_chamado": "sac", "q": "US-26-1234"},
     {"plataforma": "site", "tipo_chamado": "sac", "q": "US-26-1234"}),
    ({"fila": "todas", "tipo_email": "informativo"}, {"tipo_email": "informativo"}),
    ({"fila": "todas", "fechadas": True}, {"filtro": "fechadas"}),
    ({"fila": "aguardando"}, {"filtro": "aguardando"}),
    ({"fila": "todas", "q": "100%_literal"}, {"q": "100%_literal"}),
])
async def test_lote_marca_exatamente_o_mesmo_recorte_da_lista(
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
    assert esperados
    r = await client.post(LOTE, json=body)
    assert r.status_code == 200, r.text
    assert r.json() == {"conversas": len(esperados)}
    assert {str(i) for i in await _marcadas(db)} == esperados


async def test_lote_aceita_leitor_da_aba_so_nas_lojas_dele(
    db, cena, client, admin, make_user, auth_as
):
    minha = await _conversa(db, plataforma="ml", integration_id=cena.ml.id)
    await _conversa(db, plataforma="shopee", integration_id=cena.shopee.id)
    await _conversa(db)
    await _conversa(db, plataforma="ml", canal="chat", integration_id=cena.ml.id)
    leitor = await make_user(permissions={"atendimento_email_aba": {"view": True}})
    await dar_lojas(db, leitor, cena.ml)
    auth_as(leitor)
    r = await client.post(LOTE, json={"fila": "todas"})
    assert r.status_code == 200, r.text
    assert r.json() == {"conversas": 1}
    assert await _marcadas(db) == {minha.id}
    auth_as(await make_user(role=UserRole.USER))
    r = await client.post(LOTE, json={"fila": "todas"})
    assert r.status_code == 403, r.text
    assert await _marcadas(db) == {minha.id}


async def test_chegada_durante_o_lote_nao_e_consumida(db, cena, client, admin):
    conversa = await _conversa(db)
    cid = conversa.id
    # NO KEY UPDATE permite inserir a mensagem (FK), mas faz o lote esperar
    # depois de capturar o snapshot. Assim a corrida é real e determinística.
    async with app_db.SessionLocal() as trava, app_db.SessionLocal() as entrada:
        await trava.execute(select(AtendimentoConversa.id).where(
            AtendimentoConversa.id == cid
        ).with_for_update(key_share=True))
        pedido = asyncio.create_task(client.post(LOTE, json={"fila": "chamados"}))
        try:
            async def esperar_update():
                while not await entrada.scalar(text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE pid <> pg_backend_pid() AND wait_event_type = 'Lock' "
                    "AND query LIKE '%emails_marcados AS%')"
                )):
                    # Estatísticas do Postgres ficam em cache por transação.
                    await entrada.rollback()
                    await asyncio.sleep(0.02)

            await asyncio.wait_for(esperar_update(), timeout=5)
            nova = AtendimentoMensagem(
                conversa_id=cid, externo_id=uuid4().hex, autor="cliente", origem="cliente",
                texto="Chegou durante o lote", enviada_em=datetime.now(UTC)-timedelta(days=3),
            )
            entrada.add(nova)
            await entrada.commit()
        finally:
            await trava.rollback()
        r = await asyncio.wait_for(pedido, timeout=5)
        assert r.status_code == 200 and r.json() == {"conversas": 1}, r.text
        visto = await db.scalar(select(AtendimentoConversa.visto_em).where(
            AtendimentoConversa.id == cid
        ))
        assert visto < nova.created_at


@pytest.mark.parametrize("body", [
    {}, {"fila": "sem_loja"}, {"fila": "todas", "tipo_chamado": "sac"},
    {"fila": "chamados", "fechadas": True}, {"fila": "aguardando", "fechadas": True},
    {"fila": "todas", "tipo_email": "qualquer"}, {"fila": "todas", "q": "x"*201},
    {"fila": "todas", "integration_id": str(uuid4())},
])
async def test_lote_recusa_filtro_desconhecido_sem_alargar_a_fila(
    db, cena, client, admin, body
):
    await _conversa(db)
    r = await client.post(LOTE, json=body)
    assert r.status_code == 422, r.text
    assert await _marcadas(db) == set()
