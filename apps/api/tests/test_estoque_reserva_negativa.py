"""Reserva negativa no Controle de Estoque (23/09/2026).

Pedido 298725 em aberto no Bling com -500 de a001.sp (transferência de lote):
o Bling SOMA esses 500 no saldo virtual (661) com 163 na prateleira. A reserva
(físico - virtual) dá -498; o piso em zero fazia a coluna "Saldo atual"
(virtual + reserva) mostrar 661 em vez de 163.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, User, UserRole

PERM = {"controle_estoque": {"view": True, "edit": True, "delete": False}}


def _evento_estoque(*, bling_id: int, sku: str, virtual: int, fisico: int) -> bytes:
    return json.dumps({
        "event": "estoque.alterado",
        "dados": {
            "produto": {"id": bling_id},
            "codigo": sku,
            "operacao": "E",
            "quantidade": 1,
            "saldoVirtualTotal": virtual,
            "saldoFisicoTotal": fisico,
        },
    }).encode()


@pytest.mark.asyncio
async def test_webhook_guarda_reserva_negativa_e_saldo_atual_bate_com_o_bling(
    db: AsyncSession, client: AsyncClient,
    make_user, auth_as: Callable[[User | None], None],
) -> None:
    admin = await make_user(role=UserRole.ADMIN, permissions=PERM)
    p = Product(
        user_id=admin.id, sku="a001.sp", name="Fone com fio Uranyx UFF001",
        stock=661, reserved_stock=0, situacao="A", formato="S",
        bling_product_id=16001,
    )
    db.add(p)
    await db.commit()

    body = _evento_estoque(bling_id=16001, sku="a001.sp", virtual=661, fisico=163)
    with (
        patch("app.routers.webhooks._verify_bling_signature", AsyncMock()),
        patch("app.routers.webhooks._claim_delivery", AsyncMock(return_value=True)),
    ):
        r = await client.post(
            "/api/webhooks/bling", content=body,
            headers={"Content-Type": "application/json"},
        )
    assert r.status_code == 200, r.text

    await db.refresh(p)
    assert p.stock == 661
    assert p.reserved_stock == -498

    auth_as(admin)
    r = await client.get("/api/estoque/produtos")
    assert r.status_code == 200, r.text
    linha = next(x for x in r.json()["data"] if x["sku"] == "a001.sp")
    assert linha["saldo_fisico"] == 163
    assert linha["reserva"] == -498
    assert linha["saldo_virtual"] == 661
