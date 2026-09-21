"""Lixeira do chamado — Vinicius, 21/09/2026 (caso 294263: a operadora lançou
"Não recebido" nos 2 itens e o pacote chegou): apaga o chamado E os lançamentos
de devolução do mesmo pedido que a pessoa escolher, com a nova situação do
Bling obrigatória igual ao resolver. Linha que voltou pro estoque é estornada
no Bling ao excluir (mesma regra do DELETE /api/devolutions/{id})."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Chamado, ChamadoMensagem, Devolution
from app.services import devolution_stock_return as dsr
from app.services import logistica_bling
from tests.test_chamados import _FakeBling, _seed_pedido

pytestmark = pytest.mark.asyncio

PEDIDO = "294263"


def _perms(*, devolucoes_delete: bool = True) -> dict:
    return {
        "chamados": {"view": True, "edit": True, "delete": True},
        "devolucoes": {"view": True, "edit": True, "delete": devolucoes_delete},
    }


async def _seed_devolucao(
    db,
    *,
    pedido: str = PEDIDO,
    sku: str = "uaf001m1.110",
    motivo: str = "Não recebido",
    movimento: bool = False,
) -> Devolution:
    row = Devolution(
        pedido_bling=pedido,
        conta="aguiar",
        sku=sku,
        produtos="airfryer vidro UAF001 M1 110v",
        condicao_produto="Usado" if movimento else "Extraviado",
        motivo_devolucao=motivo,
        quantidade=1,
        devolver_estoque=movimento,
        data_devolvido_estoque=datetime.now(UTC) if movimento else None,
        estoque_mov_sku="z0319.airfryer" if movimento else None,
        estoque_mov_bling_id=555 if movimento else None,
        estoque_mov_action="entrada_existente" if movimento else None,
        estoque_mov_qty=1 if movimento else None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _seed_chamado(
    db, *, pedido: str | None = PEDIDO, origem_ref: str | None = None
) -> Chamado:
    ch = Chamado(
        pedido_bling=pedido,
        conta="aguiar",
        plataforma="ml",
        origem="devolucao",
        origem_ref=origem_ref,
        canal="manual",
        status_bling="Problemas",
    )
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return ch


def _wire_bling(monkeypatch, fake: _FakeBling) -> None:
    async def _fake_bling(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_bling)


async def _existe(db, model, row_id) -> bool:
    return (
        await db.execute(select(model.id).where(model.id == row_id))
    ).scalar_one_or_none() is not None


async def test_preview_lista_as_devolucoes_do_pedido(client, make_user, auth_as, db):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero=PEDIDO)
    d1 = await _seed_devolucao(db, motivo="Não recebido")
    d2 = await _seed_devolucao(db, sku="a001", motivo="Item Incorreto")
    await _seed_devolucao(db, pedido="999999")  # outro pedido: fora da lista
    ch = await _seed_chamado(db)

    r = await client.get(f"/api/chamados/{ch.id}/exclusao")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chamado_id"] == str(ch.id)
    assert body["pedido_bling"] == PEDIDO
    assert body["plataforma"] == "ml"
    # situação VIVA do pedido (espelho bling_orders), não o snapshot da linha
    assert body["status_bling_atual"] == "Problemas"
    assert body["exige_situacao"] is True
    assert body["abertura_enviada"] is False
    assert body["pode_excluir_lancamentos"] is True
    assert [x["id"] for x in body["lancamentos"]] == [str(d1.id), str(d2.id)]
    por_id = {x["id"]: x for x in body["lancamentos"]}
    assert por_id[str(d1.id)]["marcado_padrao"] is True
    assert por_id[str(d1.id)]["estoque_estornavel"] is False
    assert por_id[str(d2.id)]["marcado_padrao"] is False
    assert por_id[str(d2.id)]["sku"] == "a001"

    # a disputa JÁ foi aberta na plataforma → o front avisa
    db.add(
        ChamadoMensagem(
            chamado_id=ch.id,
            direcao="enviada",
            tipo="abertura",
            texto="Revisão aberta",
            canal="api",
            status="enviada",
        )
    )
    await db.commit()
    assert (await client.get(f"/api/chamados/{ch.id}/exclusao")).json()["abertura_enviada"] is True

    # quem só tem chamados.delete vê a lista mas não pode levar os lançamentos
    auth_as(await make_user(permissions=_perms(devolucoes_delete=False)))
    assert (await client.get(f"/api/chamados/{ch.id}/exclusao")).json()[
        "pode_excluir_lancamentos"
    ] is False


async def test_preview_sem_pedido_bling_so_a_linha_de_origem(client, make_user, auth_as, db):
    user = await make_user(permissions=_perms())
    auth_as(user)
    dev = await _seed_devolucao(db, pedido=None)
    ch = await _seed_chamado(db, pedido=None, origem_ref=str(dev.id))

    body = (await client.get(f"/api/chamados/{ch.id}/exclusao")).json()
    assert body["exige_situacao"] is False
    assert body["status_bling_atual"] == "Problemas"
    assert [x["id"] for x in body["lancamentos"]] == [str(dev.id)]


async def test_excluir_sem_situacao_nao_apaga_nada(client, make_user, auth_as, db, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero=PEDIDO)
    fake = _FakeBling()
    _wire_bling(monkeypatch, fake)
    dev = await _seed_devolucao(db)
    ch = await _seed_chamado(db)

    r = await client.post(f"/api/chamados/{ch.id}/excluir", json={"devolucoes": [str(dev.id)]})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "chamado_situacao_obrigatoria"
    assert fake.situacao_set == []
    assert await _existe(db, Chamado, ch.id)
    assert await _existe(db, Devolution, dev.id)

    sumido = await client.post(f"/api/chamados/{uuid4()}/excluir", json={})
    assert sumido.status_code == 404


async def test_excluir_com_situacao_apaga_chamado_e_devolucoes(
    client, make_user, auth_as, db, monkeypatch
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero=PEDIDO)
    fake = _FakeBling()
    _wire_bling(monkeypatch, fake)
    d1 = await _seed_devolucao(db)
    d2 = await _seed_devolucao(db, sku="a001")
    ch = await _seed_chamado(db)
    db.add(
        ChamadoMensagem(
            chamado_id=ch.id, direcao="sistema", tipo="sistema", texto="x",
            canal="manual", status="registrada",
        )
    )
    await db.commit()

    r = await client.post(
        f"/api/chamados/{ch.id}/excluir",
        json={"devolucoes": [str(d1.id), str(d2.id)], "situacao": "Resolvido"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "ok": True,
        "lancamentos_excluidos": 2,
        "estornos": [],
        "situacao": "Resolvido",
    }
    assert fake.situacao_set == [(123456, 545902)]
    assert not await _existe(db, Chamado, ch.id)
    assert not await _existe(db, Devolution, d1.id)
    assert not await _existe(db, Devolution, d2.id)
    assert (
        await db.execute(select(ChamadoMensagem.id).where(ChamadoMensagem.chamado_id == ch.id))
    ).first() is None


async def test_bling_recusa_a_situacao_nada_e_apagado(client, make_user, auth_as, db, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero=PEDIDO)
    _wire_bling(monkeypatch, _FakeBling())
    dev = await _seed_devolucao(db)
    ch = await _seed_chamado(db)

    r = await client.post(
        f"/api/chamados/{ch.id}/excluir",
        json={"devolucoes": [str(dev.id)], "situacao": "Nada"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "chamado_status_bling_desconhecido"
    assert await _existe(db, Chamado, ch.id)
    assert await _existe(db, Devolution, dev.id)


async def test_sem_devolucoes_delete_so_apaga_o_chamado(
    client, make_user, auth_as, db, monkeypatch
):
    user = await make_user(permissions=_perms(devolucoes_delete=False))
    auth_as(user)
    await _seed_pedido(db, user, numero=PEDIDO)
    fake = _FakeBling()
    _wire_bling(monkeypatch, fake)
    dev = await _seed_devolucao(db)
    ch = await _seed_chamado(db)

    r = await client.post(
        f"/api/chamados/{ch.id}/excluir",
        json={"devolucoes": [str(dev.id)], "situacao": "Resolvido"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "devolucoes_delete_forbidden"
    assert fake.situacao_set == []
    assert await _existe(db, Chamado, ch.id)

    r = await client.post(f"/api/chamados/{ch.id}/excluir", json={"situacao": "Resolvido"})
    assert r.status_code == 200, r.text
    assert r.json()["lancamentos_excluidos"] == 0
    assert fake.situacao_set == [(123456, 545902)]
    assert not await _existe(db, Chamado, ch.id)
    assert await _existe(db, Devolution, dev.id)


async def test_devolucao_de_outro_pedido_e_recusada(client, make_user, auth_as, db, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero=PEDIDO)
    fake = _FakeBling()
    _wire_bling(monkeypatch, fake)
    outra = await _seed_devolucao(db, pedido="999999")
    ch = await _seed_chamado(db)

    for ids in ([str(outra.id)], [str(uuid4())]):
        r = await client.post(
            f"/api/chamados/{ch.id}/excluir", json={"devolucoes": ids, "situacao": "Resolvido"}
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "devolucao_fora_do_pedido"
    assert fake.situacao_set == []
    assert await _existe(db, Chamado, ch.id)
    assert await _existe(db, Devolution, outra.id)


async def test_estorno_falha_para_ali_e_o_chamado_fica(client, make_user, auth_as, db, monkeypatch):
    """Linha que voltou pro estoque: o Bling recusa a baixa → 502, a linha e o
    chamado ficam (a situação do Bling já trocou e o histórico diz isso)."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero=PEDIDO)
    fake = _FakeBling()
    _wire_bling(monkeypatch, fake)
    sem_mov = await _seed_devolucao(db, sku="a001")
    com_mov = await _seed_devolucao(db, movimento=True)
    ch = await _seed_chamado(db)

    async def _falha(session, row):
        return {"ok": False, "message": "x"}

    monkeypatch.setattr(dsr, "reverse_stock_movement", _falha)
    r = await client.post(
        f"/api/chamados/{ch.id}/excluir",
        json={"devolucoes": [str(sem_mov.id), str(com_mov.id)], "situacao": "Resolvido"},
    )
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "estoque_estorno_falhou"
    assert "NÃO foi excluído" in detail["message"] and detail["message"].endswith("x")
    # a 1ª linha (sem movimento) já tinha saído; a 2ª e o chamado ficam
    assert detail["lancamentos_excluidos"] == 1
    assert not await _existe(db, Devolution, sem_mov.id)
    assert await _existe(db, Devolution, com_mov.id)
    assert await _existe(db, Chamado, ch.id)
    assert fake.situacao_set == [(123456, 545902)]
    textos = [
        h["texto"]
        for h in (await client.get(f"/api/chamados/{ch.id}/mensagens")).json()
        if h["tipo"] == "sistema"
    ]
    assert any("Status Bling alterado para Resolvido" in t for t in textos)


async def test_estorno_ok_devolve_sku_e_qty(client, make_user, auth_as, db, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero=PEDIDO)
    _wire_bling(monkeypatch, _FakeBling())
    com_mov = await _seed_devolucao(db, movimento=True)
    ch = await _seed_chamado(db)
    assert (await client.get(f"/api/chamados/{ch.id}/exclusao")).json()["lancamentos"][0][
        "estoque_estornavel"
    ] is True

    async def _ok(session, row):
        return {"ok": True, "message": "Estoque estornado · −1 em z0319.airfryer"}

    monkeypatch.setattr(dsr, "reverse_stock_movement", _ok)
    r = await client.post(
        f"/api/chamados/{ch.id}/excluir",
        json={"devolucoes": [str(com_mov.id)], "situacao": "Resolvido"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["lancamentos_excluidos"] == 1
    assert r.json()["estornos"] == [
        {"sku": "z0319.airfryer", "qty": 1, "mensagem": "Estoque estornado · −1 em z0319.airfryer"}
    ]
    assert not await _existe(db, Devolution, com_mov.id)
    assert not await _existe(db, Chamado, ch.id)


async def test_delete_antigo_continua_so_o_chamado(client, make_user, auth_as, db):
    user = await make_user(permissions=_perms())
    auth_as(user)
    dev = await _seed_devolucao(db)
    ch = await _seed_chamado(db)

    assert (await client.delete(f"/api/chamados/{ch.id}")).status_code == 204
    assert not await _existe(db, Chamado, ch.id)
    assert await _existe(db, Devolution, dev.id)
