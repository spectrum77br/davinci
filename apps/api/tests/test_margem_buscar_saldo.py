"""Botão "buscar saldo" da aba Margem (Vinicius, 28/09).

A chave da Shopee Barbosa venceu em 25/09 e ~40 pedidos ficaram "aguardando
saldo da plataforma". Trocada a chave, o financeiro só voltava sozinho 12 h
depois de cada recusa. O POST /buscar-saldo põe na fila de financeiro, na hora,
os pedidos em triagem que estão aguardando o saldo — só eles, uma vez por
pedido, respeitando plataforma e conta da tela.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers import margens as margens_router

pytestmark = pytest.mark.asyncio


def _margem_permissions() -> dict:
    return {"margem": {"view": True, "edit": True, "delete": False}}


class _FakePool:
    """Imita o arq: devolve None quando o job_id já existe (duplicado)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._ids: set[str] = set()

    async def enqueue_job(self, fn, *args, _job_id=None, **kw):
        if _job_id in self._ids:
            return None
        self._ids.add(_job_id)
        self.calls.append((fn, args, _job_id))
        return object()


@pytest.fixture
def fake_pool(monkeypatch) -> _FakePool:
    pool = _FakePool()

    async def _get_pool():
        return pool

    monkeypatch.setattr(margens_router, "get_arq_financials_pool", _get_pool)
    return pool


async def _seed_linha(
    db: AsyncSession,
    *,
    pedido: str,
    bling_id: int,
    plataforma: str = "shopee",
    loja: str = "barbosa",
    liquido: float | None = None,
    situacao: str = "6",
) -> None:
    """Uma linha-item no snapshot verificar_margem. Default = aguardando saldo:
    plataforma confiável (shopee) com líquido real NULL."""
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                situacao, situacao_nome, plataforma_bling, loja_nome,
                item_proportion, margem_minima,
                bling_valorbase_item, bling_custo_produtos,
                marketplace_liquido_base_margem_item, data
            ) VALUES (
                :id, :pedido, :bling_id, :sku,
                :situacao, 'Em aberto', :plataforma, :loja,
                1, 0.10,
                200, 100,
                :liquido, :data
            )
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "pedido": pedido,
            "bling_id": bling_id,
            "sku": f"sku-{uuid.uuid4().hex[:6]}",
            "situacao": situacao,
            "plataforma": plataforma,
            "loja": loja,
            "liquido": liquido,
            "data": datetime.now(UTC),
        },
    )
    await db.commit()


def _bling_ids(pool: _FakePool) -> list[int]:
    return [args[0] for _fn, args, _jid in pool.calls]


async def test_enfileira_so_quem_aguarda_saldo(client, db, make_user, auth_as, fake_pool):
    auth_as(await make_user(permissions=_margem_permissions()))
    await _seed_linha(db, pedido="910001", bling_id=910001)  # aguardando
    await _seed_linha(db, pedido="910002", bling_id=910002, liquido=150.0)  # já tem saldo
    await _seed_linha(db, pedido="910003", bling_id=910003, plataforma="amazon")  # não confiável
    await _seed_linha(db, pedido="910004", bling_id=910004, situacao="9")  # fora da triagem

    res = await client.post("/api/margens/marketplace/buscar-saldo")

    assert res.status_code == 200, res.json()
    assert res.json() == {"pedidos": 1, "enfileirados": 1, "ja_na_fila": 0, "falhas": 0}
    assert len(fake_pool.calls) == 1
    fn, args, job_id = fake_pool.calls[0]
    assert fn == "sync_marketplace_financials_for_order_run"
    # trigger próprio: não o "manual", que refresca o snapshot pedido a pedido
    assert args == (910001, "botao_margem")
    assert job_id.startswith("buscar-saldo:910001:")


async def test_pedido_com_varios_itens_entra_uma_vez(
    client, db, make_user, auth_as, fake_pool
):
    auth_as(await make_user(permissions=_margem_permissions()))
    await _seed_linha(db, pedido="910010", bling_id=910010)
    await _seed_linha(db, pedido="910010", bling_id=910010)

    res = await client.post("/api/margens/marketplace/buscar-saldo")

    assert res.json()["pedidos"] == 1
    assert _bling_ids(fake_pool) == [910010]


async def test_respeita_conta_e_plataforma_da_tela(
    client, db, make_user, auth_as, fake_pool
):
    auth_as(await make_user(permissions=_margem_permissions()))
    await _seed_linha(db, pedido="910020", bling_id=910020, loja="barbosa")
    await _seed_linha(db, pedido="910021", bling_id=910021, loja="outra")
    await _seed_linha(db, pedido="910022", bling_id=910022, loja="barbosa", plataforma="ml")

    res = await client.post(
        "/api/margens/marketplace/buscar-saldo",
        params={"conta": "barbosa", "platform": "shopee"},
    )

    assert res.json()["pedidos"] == 1
    assert _bling_ids(fake_pool) == [910020]


async def test_clique_repetido_nao_dobra_a_fila(client, db, make_user, auth_as, fake_pool):
    auth_as(await make_user(permissions=_margem_permissions()))
    await _seed_linha(db, pedido="910030", bling_id=910030)

    primeiro = await client.post("/api/margens/marketplace/buscar-saldo")
    segundo = await client.post("/api/margens/marketplace/buscar-saldo")

    assert primeiro.json()["enfileirados"] == 1
    assert segundo.json() == {"pedidos": 1, "enfileirados": 0, "ja_na_fila": 1, "falhas": 0}
    assert _bling_ids(fake_pool) == [910030]


async def test_sem_pedido_aguardando_nao_abre_a_fila(
    client, db, make_user, auth_as, monkeypatch
):
    auth_as(await make_user(permissions=_margem_permissions()))

    async def _nao_pode(*_a, **_k):
        raise AssertionError("não deveria abrir a fila sem pedido")

    monkeypatch.setattr(margens_router, "get_arq_financials_pool", _nao_pode)

    res = await client.post("/api/margens/marketplace/buscar-saldo")

    assert res.status_code == 200
    assert res.json()["pedidos"] == 0


async def test_exige_permissao_de_edicao(client, db, make_user, auth_as, fake_pool):
    auth_as(await make_user(permissions={"margem": {"view": True}}))
    await _seed_linha(db, pedido="910040", bling_id=910040)

    res = await client.post("/api/margens/marketplace/buscar-saldo")

    assert res.status_code == 403
    assert fake_pool.calls == []
