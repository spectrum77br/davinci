from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

pytestmark = pytest.mark.asyncio


def _refund_permissions(*, view: bool = True, edit: bool = True, delete: bool = True) -> dict:
    return {"reembolso": {"view": view, "edit": edit, "delete": delete}}


async def test_list_refunds_requires_view_permission(client, make_user, auth_as):
    user = await make_user(permissions={})
    auth_as(user)

    response = await client.get("/api/refunds")

    assert response.status_code == 403


async def test_create_refund_starts_unchecked_and_can_be_patched(client, make_user, auth_as):
    user = await make_user(permissions=_refund_permissions())
    auth_as(user)

    response = await client.post(
        "/api/refunds",
        json={
            "data": "2026-05-20T12:00:00-03:00",
            "pedido_bling": "123456",
            "pedido_marketplace": "MLB999",
            "plataforma": "ml",
            "conta": "Loja Teste",
            "tipo": "Cliente",
            "prejuizo": 10.5,
            "reembolso": 7.25,
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created["conferido"] is False
    assert created["tipo"] == "Cliente"
    assert created["conta"] == "Loja Teste"

    patch = await client.patch(
        f"/api/refunds/{created['id']}",
        json={"conferido": True, "tipo": "Logistica", "chamado": "CH-1"},
    )

    assert patch.status_code == 200
    updated = patch.json()
    assert updated["conferido"] is True
    assert updated["tipo"] == "Logistica"
    assert updated["chamado"] == "CH-1"


async def test_lookup_refund_order_reads_conciliation_view(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    user = await make_user(permissions=_refund_permissions(edit=False, delete=False))
    auth_as(user)
    schema = get_settings().database_schema
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_conciliacao_margens_marketplace'))
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_conciliacao_margens_marketplace AS
            SELECT
                '2026-05-20T12:00:00+00:00'::timestamptz AS data,
                '123456'::text AS pedido_bling,
                'MLB999'::text AS pedido_marketplace,
                'ml'::text AS plataforma_bling,
                NULL::text AS plataforma_financeiro,
                'Conta View'::text AS loja_nome
            """  # noqa: S608
        )
    )
    await db.commit()

    response = await client.get("/api/refunds/order-lookup?pedido=MLB999")

    assert response.status_code == 200
    assert response.json() == [
        {
            "data": "2026-05-20T12:00:00Z",
            "pedido_bling": "123456",
            "pedido_marketplace": "MLB999",
            "plataforma": "ml",
            "conta": "Conta View",
        }
    ]
