from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Margens
from app.routers import margens as margens_router

pytestmark = pytest.mark.asyncio


class FakeBlingClient:
    def __init__(
        self,
        *,
        order_situacao: int | str | None = margens_router.SITUACAO_VERIFICAR_MARGEM,
        order_situacao_nome: str | None = margens_router.SITUACAO_VERIFICAR_MARGEM_NOME,
    ) -> None:
        self.calls: list[tuple[int, int]] = []
        self.get_order_calls: list[int] = []
        self.order_situacao = order_situacao
        self.order_situacao_nome = order_situacao_nome

    async def get_order(self, bling_order_id: int) -> dict:
        self.get_order_calls.append(bling_order_id)
        if self.order_situacao is None:
            return {"id": bling_order_id, "situacao": None}
        return {
            "id": bling_order_id,
            "situacao": {
                "id": self.order_situacao,
                "nome": self.order_situacao_nome,
            },
        }

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


async def test_patch_margem_reprovado_requires_current_bling_verificar_margem(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
    monkeypatch,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    margem, order = await _create_margem_with_order(db)
    fake_client = FakeBlingClient(
        order_situacao=margens_router.SITUACAO_APROVADO,
        order_situacao_nome="Em aberto",
    )

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

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "bling_situacao_not_verificar_margem"
    assert fake_client.get_order_calls == [987654]
    assert fake_client.calls == []

    await db.refresh(margem)
    await db.refresh(order)
    assert margem.status == "Pendente"
    assert order.status is None
    assert order.aprovado_por is None
    assert order.situacao == "15"
    assert order.verificado is False


async def test_patch_margem_reprovado_from_verificar_margem_updates_bling(
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
        json={"status": "Reprovado"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "Reprovado"
    assert fake_client.get_order_calls == [987654]
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
