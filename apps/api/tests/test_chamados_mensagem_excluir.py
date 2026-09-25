# ruff: noqa: E501
"""Lixeirinha no histórico do chamado — Vinicius, 25/09/2026 (chamado 260914016HQB8XN,
pedido 294571: "quero excluir uma mensagem do histórico").

Excluir ESCONDE: a mensagem some da tela e da leitura da IA de Chamado, mas a linha fica
— a varredura só grava a fala da plataforma que ainda não está no histórico (apagada, ela
voltaria na passada seguinte) e as marcas de sistema seguram respostas automáticas. A
abertura não tem lixeira; mensagem nossa ainda na fila também não. Só quem pode excluir
chamado."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Chamado, ChamadoMensagem
from app.routers.chamados import _item_do_cerebro
from app.services import chamados as svc
from app.services import chamados_devolucao_sync as sync

pytestmark = pytest.mark.asyncio

PERMS = {"chamados": {"view": True, "edit": True, "delete": True}}
QUANDO = datetime(2026, 9, 25, 16, 16, tzinfo=UTC)
FALA_SHOPEE = "A Shopee pede evidência até 26/09/2026 (2ª disputa)."


def _chamado(**kw) -> Chamado:
    base = {"data": date(2026, 9, 25), "pedido_bling": "294571", "pedido_marketplace": "2609059AXNCBD9",
            "plataforma": "shopee", "conta": "Shopee ATV", "origem": "devolucao",
            "origem_ref": str(uuid4()), "chamado": "260914016HQB8XN", "canal": "api"}
    base.update(kw)
    return Chamado(**base)


def _msg(ch: Chamado, texto: str, *, direcao: str, tipo: str, status: str = "registrada",
         quando: datetime = QUANDO, autor: str | None = None) -> ChamadoMensagem:
    m = svc.nova_mensagem(ch, texto=texto, tipo=tipo, direcao=direcao, status=status,
                          autor_nome=autor or ("Shopee" if direcao == "recebida" else "Cairo"))
    m.canal = "api"
    m.created_at = quando
    return m


async def _seed(db, *extra) -> tuple[Chamado, dict[str, ChamadoMensagem]]:
    ch = _chamado()
    db.add(ch)
    await db.flush()
    msgs = {
        "abertura": _msg(ch, "Contestamos a devolução", direcao="enviada", tipo="abertura",
                         status="enviada", quando=QUANDO - timedelta(days=1)),
        "fala": _msg(ch, FALA_SHOPEE, direcao="recebida", tipo="resposta"),
        "instrucao": _msg(ch, "enviar evidência se precisar", direcao="sistema", tipo="instrucao",
                          quando=QUANDO + timedelta(minutes=2)),
        "pendente": _msg(ch, "Segue a evidência", direcao="enviada", tipo="replica", status="pendente",
                         quando=QUANDO + timedelta(minutes=3)),
    }
    db.add_all(msgs.values())
    await db.commit()
    return ch, msgs


async def _textos_na_tela(client, ch) -> list[str]:
    r = await client.get(f"/api/chamados/{ch.id}/mensagens")
    assert r.status_code == 200, r.text
    return [m["texto"] for m in r.json()]


async def test_lixeirinha_esconde_da_tela_e_da_ia_mas_a_fala_nao_volta(client, make_user, auth_as, db):
    auth_as(await make_user(email="vinicius@davinci-test.com", permissions=PERMS))
    ch, msgs = await _seed(db)
    assert FALA_SHOPEE in await _textos_na_tela(client, ch)

    r = await client.delete(f"/api/chamados/mensagens/{msgs['fala'].id}")

    assert r.status_code == 204, r.text
    assert FALA_SHOPEE not in await _textos_na_tela(client, ch)
    # a linha fica, com quem e quando
    await db.refresh(msgs["fala"])
    assert msgs["fala"].excluida_at is not None and msgs["fala"].excluida_por == "vinicius@davinci-test.com"
    # a IA de Chamado também não lê
    item = await _item_do_cerebro(db, ch)
    assert FALA_SHOPEE not in [m.texto for m in item.mensagens]
    assert "Contestamos a devolução" in [m.texto for m in item.mensagens]
    # a varredura das :25 NÃO reescreve a fala escondida (o dedupe ainda a vê)
    assert await sync.registrar_recebida(db, ch, "shopee", FALA_SHOPEE) is False
    # de novo: já escondida → 404
    assert (await client.delete(f"/api/chamados/mensagens/{msgs['fala'].id}")).status_code == 404


async def test_instrucao_escondida_some_da_ia(client, make_user, auth_as, db):
    auth_as(await make_user(permissions=PERMS))
    ch, msgs = await _seed(db)
    assert (await _item_do_cerebro(db, ch)).instrucao is not None

    assert (await client.delete(f"/api/chamados/mensagens/{msgs['instrucao'].id}")).status_code == 204

    assert (await _item_do_cerebro(db, ch)).instrucao is None


async def test_abertura_e_mensagem_na_fila_nao_tem_lixeira(client, make_user, auth_as, db):
    auth_as(await make_user(permissions=PERMS))
    ch, msgs = await _seed(db)

    r = await client.delete(f"/api/chamados/mensagens/{msgs['abertura'].id}")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "chamado_mensagem_abertura", r.text
    r = await client.delete(f"/api/chamados/mensagens/{msgs['pendente'].id}")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "chamado_mensagem_na_fila", r.text
    textos = await _textos_na_tela(client, ch)
    assert "Contestamos a devolução" in textos and "Segue a evidência" in textos


async def test_so_quem_pode_excluir_chamado(client, make_user, auth_as, db):
    auth_as(await make_user(permissions={"chamados": {"view": True, "edit": True, "delete": False}}))
    ch, msgs = await _seed(db)

    r = await client.delete(f"/api/chamados/mensagens/{msgs['fala'].id}")

    assert r.status_code == 403, r.text
    assert FALA_SHOPEE in await _textos_na_tela(client, ch)


async def test_copia_da_mensagem_na_linha_irma_do_caso_some_junto(client, make_user, auth_as, db):
    """A mesma consulta ligada a dois pedidos (linhas irmãs): o histórico junta as duas e
    mostra a fala uma vez — esconder tem que levar as duas cópias, senão a outra aparece."""
    auth_as(await make_user(permissions=PERMS))
    a = _chamado(chamado="478538390", pedido_bling="290397", plataforma="ml", conta="dream")
    b = _chamado(chamado="478538390", pedido_bling="290398", plataforma="ml", conta="dream")
    db.add_all([a, b])
    await db.flush()
    fa = _msg(a, "O mediador pediu o valor da peça", direcao="recebida", tipo="resposta", autor="Mercado Livre")
    fb = _msg(b, "O mediador pediu o valor da peça", direcao="recebida", tipo="resposta", autor="Mercado Livre",
              quando=QUANDO + timedelta(seconds=40))
    outra = _msg(b, "Outra fala", direcao="recebida", tipo="resposta", autor="Mercado Livre",
                 quando=QUANDO + timedelta(minutes=10))
    db.add_all([fa, fb, outra])
    await db.commit()

    assert (await client.delete(f"/api/chamados/mensagens/{fa.id}")).status_code == 204

    for ch in (a, b):
        textos = await _textos_na_tela(client, ch)
        assert "O mediador pediu o valor da peça" not in textos and "Outra fala" in textos, textos
    escondidas = (await db.execute(
        select(ChamadoMensagem.id).where(ChamadoMensagem.excluida_at.is_not(None)))).scalars().all()
    assert set(escondidas) == {fa.id, fb.id}
