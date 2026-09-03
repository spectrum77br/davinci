"""Excluir lançamento de devolução ESTORNA o estoque devolvido ao Bling.

Eduardo (03/09): "quando fazemos o lançamento errado, dá pra excluir e fazer de
volta, porém o estoque que foi lançado lá não tem como remover… quando eu
clicar em excluir você lança um estoque de saída 1 e remove o estoque que foi
lançado". Caso real 290294: 5 entradas em 02/09 + 5 em 03/09 (refeito) = 10
no Bling para 5 unidades.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Devolution
from app.services import devolution_stock_return as dsr

pytestmark = pytest.mark.asyncio


def _perms() -> dict:
    return {"devolucoes": {"view": True, "edit": True, "delete": True}}


class _FakeBling:
    def __init__(self, fail: Exception | None = None):
        self.calls: list[dict] = []
        self.fail = fail

    async def update_stock_by_id(self, pid: int, *, qty: int, operation: str, observacao: str = ""):
        self.calls.append({"pid": pid, "qty": qty, "op": operation, "obs": observacao})
        if self.fail:
            raise self.fail


def _wire(monkeypatch, fake: _FakeBling) -> None:
    async def _get(_session):
        return fake

    monkeypatch.setattr(dsr, "_get_bling_client", _get)


async def _seed(db, *, movimento: bool = True, revertido: bool = False) -> Devolution:
    row = Devolution(
        pedido_bling=f"29{uuid4().hex[:4]}",
        conta="Shopee Teste",
        sku="b009.18",
        produtos="Mala Lisa M2 18",
        condicao_produto="Usado",
        quantidade=1,
        devolver_estoque=movimento,
        data_devolvido_estoque=datetime.now(UTC) if movimento else None,
        estoque_mov_sku="z0319.mala" if movimento else None,
        estoque_mov_bling_id=555 if movimento else None,
        estoque_mov_action="entrada_existente" if movimento else None,
        estoque_mov_qty=1 if movimento else None,
        estoque_mov_revertido_at=datetime.now(UTC) if revertido else None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _existe(db, dev_id) -> bool:
    return (
        await db.execute(select(Devolution.id).where(Devolution.id == dev_id))
    ).scalar_one_or_none() is not None


async def test_excluir_estorna_o_estoque_no_bling(client, db, make_user, auth_as, monkeypatch):
    auth_as(await make_user(permissions=_perms()))
    fake = _FakeBling()
    _wire(monkeypatch, fake)
    row = await _seed(db)

    r = await client.delete(f"/api/devolutions/{row.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estoque_estornado"] is True
    assert "z0319.mala" in (body["mensagem"] or "")
    # BAIXA ("S") da mesma quantidade no mesmo produto do Bling.
    assert fake.calls == [
        {"pid": 555, "qty": 1, "op": "S", "obs": f"Estorno devolução pedido {row.pedido_bling}"}
    ]
    assert not await _existe(db, row.id)


async def test_bling_recusa_o_estorno_nao_exclui(client, db, make_user, auth_as, monkeypatch):
    """Sem o estorno, excluir deixaria estoque fantasma no Bling → 502 e o
    lançamento fica (o operador tenta de novo ou ajusta no Bling)."""
    auth_as(await make_user(permissions=_perms()))
    _wire(monkeypatch, _FakeBling(fail=RuntimeError("bling 500")))
    row = await _seed(db)

    r = await client.delete(f"/api/devolutions/{row.id}")
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "estoque_estorno_falhou"
    assert "NÃO foi excluído" in r.json()["detail"]["message"]
    assert await _existe(db, row.id)
    await db.refresh(row)
    assert row.estoque_mov_revertido_at is None


async def test_sem_movimento_exclui_direto(client, db, make_user, auth_as, monkeypatch):
    auth_as(await make_user(permissions=_perms()))
    fake = _FakeBling()
    _wire(monkeypatch, fake)
    row = await _seed(db, movimento=False)

    r = await client.delete(f"/api/devolutions/{row.id}")
    assert r.status_code == 200
    assert r.json()["estoque_estornado"] is False
    assert fake.calls == []
    assert not await _existe(db, row.id)


async def test_ja_estornado_nao_estorna_de_novo(client, db, make_user, auth_as, monkeypatch):
    auth_as(await make_user(permissions=_perms()))
    fake = _FakeBling()
    _wire(monkeypatch, fake)
    row = await _seed(db, revertido=True)

    r = await client.delete(f"/api/devolutions/{row.id}")
    assert r.status_code == 200
    assert r.json()["estoque_estornado"] is False
    assert fake.calls == []
    assert not await _existe(db, row.id)
