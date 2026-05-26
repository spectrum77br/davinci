from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Margens
from app.routers import margens as margens_router

pytestmark = pytest.mark.asyncio


class FakeBlingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def update_order_situacao(self, bling_order_id: int, situacao_id: int) -> None:
        self.calls.append((bling_order_id, situacao_id))


async def _create_margem_with_order(
    db: AsyncSession,
    *,
    situacao: str = "15",
) -> tuple[Margens, BlingOrder]:
    margem = Margens(
        pedido_bling=123456,
        sku="sku-1",
        produtos="Produto teste",
        status="Pendente",
    )
    order = BlingOrder(
        bling_id=987654,
        numero="123456",
        item_codigo="sku-1",
        item_index=0,
        situacao=situacao,
    )
    db.add_all([margem, order])
    await db.commit()
    await db.refresh(margem)
    await db.refresh(order)
    return margem, order


def _margem_permissions() -> dict:
    return {"margem": {"view": True, "edit": True, "delete": False}}


async def test_list_margens_requires_view_permission(client, make_user, auth_as):
    user = await make_user(permissions={})
    auth_as(user)

    response = await client.get("/api/margens")

    assert response.status_code == 403


async def test_patch_margem_requires_edit_permission(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    user = await make_user(permissions={"margem": {"view": True}})
    auth_as(user)
    margem, _order = await _create_margem_with_order(db)

    response = await client.patch(
        f"/api/margens/{margem.id}",
        json={"status": "Aprovado"},
    )

    assert response.status_code == 403


async def test_patch_margem_uses_global_bling_client_for_authorized_user(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
    monkeypatch,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    margem, order = await _create_margem_with_order(db)
    fake_client = FakeBlingClient()

    async def fake_global_bling_client(session):
        return fake_client

    monkeypatch.setattr(
        margens_router,
        "_global_bling_client",
        fake_global_bling_client,
    )

    response = await client.patch(
        f"/api/margens/{margem.id}",
        json={"status": "Aprovado"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "Aprovado"
    assert fake_client.calls == [
        (987654, margens_router.SITUACAO_ATENDIDO),
        (987654, margens_router.SITUACAO_APROVADO),
    ]

    await db.refresh(order)
    assert order.status == "Aprovado"
    assert order.aprovado_por == user.id
    assert order.situacao == str(margens_router.SITUACAO_APROVADO)
    assert order.verificado is True


async def test_patch_margem_aprovado_from_atendido_skips_atendido_step(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
    monkeypatch,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    margem, order = await _create_margem_with_order(
        db,
        situacao=str(margens_router.SITUACAO_ATENDIDO),
    )
    fake_client = FakeBlingClient()

    async def fake_global_bling_client(session):
        return fake_client

    monkeypatch.setattr(
        margens_router,
        "_global_bling_client",
        fake_global_bling_client,
    )

    response = await client.patch(
        f"/api/margens/{margem.id}",
        json={"status": "Aprovado"},
    )

    assert response.status_code == 200
    assert fake_client.calls == [(987654, margens_router.SITUACAO_APROVADO)]
    await db.refresh(order)
    assert order.situacao == str(margens_router.SITUACAO_APROVADO)


async def test_patch_margem_reprovado_when_situacao_not_em_aberto_skips_bling(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
    monkeypatch,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    margem, order = await _create_margem_with_order(db, situacao="15")

    async def fail_if_called(session):
        raise AssertionError("Bling client should not be needed")

    monkeypatch.setattr(margens_router, "_global_bling_client", fail_if_called)

    response = await client.patch(
        f"/api/margens/{margem.id}",
        json={"status": "Reprovado"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "Reprovado"

    await db.refresh(order)
    assert order.status == "Reprovado"
    assert order.aprovado_por == user.id
    assert order.situacao == "15"
    assert order.verificado is True


async def test_patch_margem_reprovado_from_em_aberto_patches_bling(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
    monkeypatch,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    margem, order = await _create_margem_with_order(
        db,
        situacao=str(margens_router.SITUACAO_APROVADO),
    )
    fake_client = FakeBlingClient()

    async def fake_global_bling_client(session):
        return fake_client

    monkeypatch.setattr(
        margens_router,
        "_global_bling_client",
        fake_global_bling_client,
    )

    response = await client.patch(
        f"/api/margens/{margem.id}",
        json={"status": "Reprovado"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "Reprovado"
    assert fake_client.calls == [(987654, margens_router.SITUACAO_REPROVADO)]

    await db.refresh(order)
    assert order.status == "Reprovado"
    assert order.aprovado_por == user.id
    assert order.situacao == str(margens_router.SITUACAO_REPROVADO)
    assert order.verificado is True


async def test_patch_margem_skips_bling_when_order_is_already_target_situacao(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
    monkeypatch,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    margem, order = await _create_margem_with_order(
        db,
        situacao=str(margens_router.SITUACAO_APROVADO),
    )

    async def fail_if_called(session):
        raise AssertionError("Bling client should not be needed")

    monkeypatch.setattr(margens_router, "_global_bling_client", fail_if_called)

    response = await client.patch(
        f"/api/margens/{margem.id}",
        json={"status": "Aprovado"},
    )

    assert response.status_code == 200
    await db.refresh(order)
    assert order.status == "Aprovado"
    assert order.aprovado_por == user.id
    assert order.verificado is True


async def test_patch_margem_local_only_marks_order_verified_without_changing_situacao(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
    monkeypatch,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    margem, order = await _create_margem_with_order(db, situacao="12")

    async def fail_if_called(session):
        raise AssertionError("Bling client should not be needed")

    monkeypatch.setattr(margens_router, "_global_bling_client", fail_if_called)

    response = await client.patch(
        f"/api/margens/{margem.id}",
        json={"status": "Aprovado", "local_only": True},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "Aprovado"
    await db.refresh(order)
    assert order.status == "Aprovado"
    assert order.aprovado_por == user.id
    assert order.situacao == "12"
    assert order.verificado is True


async def test_marketplace_status_updates_snapshot_without_view_refresh(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    order = BlingOrder(
        bling_id=987654,
        numero="123456",
        item_codigo="sku-1",
        item_index=0,
        situacao="15",
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                bling_status_margem, verificado
            )
            VALUES (:id, '123456', 987654, 'sku-1', NULL, false)
            """
        ),
        {"id": str(order.id)},
    )
    await db.commit()

    response = await client.patch(
        "/api/margens/marketplace/status/123456",
        json={"status": "Aprovado", "sku": "sku-1", "local_only": True},
    )

    assert response.status_code == 200
    snapshot = (
        await db.execute(
            text(
                """
                SELECT bling_status_margem, aprovado_por::text AS aprovado_por, verificado
                FROM verificar_margem
                WHERE bling_order_item_id = CAST(:id AS uuid)
                """
            ),
            {"id": str(order.id)},
        )
    ).mappings().one()
    assert snapshot["bling_status_margem"] == "Aprovado"
    assert snapshot["aprovado_por"] == str(user.id)
    assert snapshot["verificado"] is True


async def test_sync_from_marketplace_updates_snapshot_financials_without_view_refresh(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    order = BlingOrder(
        bling_id=987654,
        numero="123456",
        item_codigo="sku-1",
        item_index=0,
        situacao="15",
        valorbase=80,
        taxacomissao=20,
        custofrete=10,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                plataforma_bling, item_proportion,
                marketplace_valor_bruto_item, marketplace_taxas_item,
                marketplace_frete_real_cobrado_item,
                bling_valorbase_item, bling_taxacomissao_item,
                bling_custofrete_item, bling_custo_produtos
            )
            VALUES (
                :id, '123456', 987654, 'sku-1',
                'ml', 1,
                120, 10,
                5,
                80, 20,
                10, 50
            )
            """
        ),
        {"id": str(order.id)},
    )
    await db.commit()

    response = await client.post(f"/api/margens/marketplace/{order.id}/sync-from-marketplace")

    assert response.status_code == 200
    await db.refresh(order)
    assert float(order.valorbase) == 120.0
    assert float(order.taxacomissao) == 10.0
    assert float(order.custofrete) == 5.0
    snapshot = (
        await db.execute(
            text(
                """
                SELECT
                    bling_valorbase_item,
                    bling_taxacomissao_item,
                    bling_custofrete_item,
                    bling_lucro_calculado,
                    bling_margem_calculado
                FROM verificar_margem
                WHERE bling_order_item_id = CAST(:id AS uuid)
                """
            ),
            {"id": str(order.id)},
        )
    ).mappings().one()
    assert float(snapshot["bling_valorbase_item"]) == 120.0
    assert float(snapshot["bling_taxacomissao_item"]) == 10.0
    assert float(snapshot["bling_custofrete_item"]) == 5.0
    assert float(snapshot["bling_lucro_calculado"]) == 55.0
    assert float(snapshot["bling_margem_calculado"]) == 1.1
