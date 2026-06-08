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


async def test_cliente_reembolso_is_clamped_to_non_positive(client, make_user, auth_as):
    user = await make_user(permissions=_refund_permissions())
    auth_as(user)

    # Create with Cliente + positive reembolso → auto-negated.
    response = await client.post(
        "/api/refunds",
        json={
            "pedido_bling": "C-1",
            "conta": "Loja X",
            "tipo": "Cliente",
            "reembolso": 12.5,
        },
    )
    assert response.status_code == 201
    created = response.json()
    assert created["tipo"] == "Cliente"
    assert created["reembolso"] == -12.5

    # Patch a Logistica row to tipo=Cliente without resending reembolso →
    # existing positive reembolso must still get clamped.
    logistica = await client.post(
        "/api/refunds",
        json={
            "pedido_bling": "C-2",
            "conta": "Loja X",
            "tipo": "Logistica",
            "reembolso": 8.0,
        },
    )
    assert logistica.status_code == 201
    assert logistica.json()["reembolso"] == 8.0  # not Cliente → untouched

    flip = await client.patch(
        f"/api/refunds/{logistica.json()['id']}",
        json={"tipo": "Cliente"},
    )
    assert flip.status_code == 200
    assert flip.json()["reembolso"] == -8.0

    # Patch only reembolso on an already-Cliente row with a positive value →
    # also clamped.
    bump = await client.patch(
        f"/api/refunds/{created['id']}",
        json={"reembolso": 30.0},
    )
    assert bump.status_code == 200
    assert bump.json()["reembolso"] == -30.0

    # Other tipos accept positive reembolso unchanged.
    other = await client.post(
        "/api/refunds",
        json={
            "pedido_bling": "C-3",
            "conta": "Loja X",
            "tipo": "Extraviado",
            "reembolso": 5.0,
        },
    )
    assert other.status_code == 201
    assert other.json()["reembolso"] == 5.0


async def test_lookup_refund_order_reads_recent_conciliation_view(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    user = await make_user(permissions=_refund_permissions(edit=False, delete=False))
    auth_as(user)
    schema = get_settings().database_schema
    await db.execute(
        text(f'DROP VIEW IF EXISTS "{schema}".vw_conciliacao_margens_marketplace')
    )
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_conciliacao_margens_marketplace AS
            SELECT * FROM (VALUES
                ('2026-05-20T12:00:00+00:00'::timestamptz, '123456'::text, 'MLB999'::text,
                 'ml'::text, NULL::text, 'Conta View'::text, 12.50::numeric),
                ('2026-05-20T12:00:00+00:00'::timestamptz, '123456'::text, 'MLB999'::text,
                 'ml'::text, NULL::text, 'Conta View'::text, 7.25::numeric)
            ) AS t(data, pedido_bling, pedido_marketplace, plataforma_bling,
                   plataforma_financeiro, loja_nome, bling_custo_produtos)
            """  # noqa: S608
        )
    )
    await db.commit()

    response = await client.get("/api/refunds/order-lookup?pedido=MLB999")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "data": "2026-05-20T12:00:00Z",
                "pedido_bling": "123456",
                "pedido_marketplace": "MLB999",
                "plataforma": "ml",
                "conta": "Conta View",
                "custo_produto": 19.75,
                "custo_manutencao": None,
            }
        ],
        "historico_disponivel": False,
    }


async def test_lookup_refund_order_surfaces_history_cta_when_recent_view_misses(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    user = await make_user(permissions=_refund_permissions(edit=False, delete=False))
    auth_as(user)
    schema = get_settings().database_schema
    await db.execute(
        text(f'DROP VIEW IF EXISTS "{schema}".vw_conciliacao_margens_marketplace')
    )
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_conciliacao_margens_marketplace AS
            SELECT * FROM (VALUES
                ('2026-05-20T12:00:00+00:00'::timestamptz, '999999'::text, 'MLB999'::text,
                 'ml'::text, NULL::text, 'Conta View'::text, 12.50::numeric)
            ) AS t(data, pedido_bling, pedido_marketplace, plataforma_bling,
                   plataforma_financeiro, loja_nome, bling_custo_produtos)
            WHERE false
            """  # noqa: S608
        )
    )
    await db.execute(
        text(
            """
            INSERT INTO bling_orders (numero, numeroloja)
            VALUES ('123456', 'OLD999')
            """
        )
    )
    await db.commit()

    response = await client.get("/api/refunds/order-lookup?pedido=OLD999")

    assert response.status_code == 200
    assert response.json() == {"items": [], "historico_disponivel": True}


async def test_lookup_refund_order_reads_full_view_when_history_requested(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    user = await make_user(permissions=_refund_permissions(edit=False, delete=False))
    auth_as(user)
    schema = get_settings().database_schema
    await db.execute(
        text(f'DROP VIEW IF EXISTS "{schema}".vw_conciliacao_margens_marketplace')
    )
    await db.execute(
        text(f'DROP VIEW IF EXISTS "{schema}".vw_conciliacao_margens_marketplace_all')
    )
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_conciliacao_margens_marketplace AS
            SELECT * FROM (VALUES
                ('2026-05-20T12:00:00+00:00'::timestamptz, '999999'::text, 'MLB999'::text,
                 'ml'::text, NULL::text, 'Conta View'::text, 12.50::numeric)
            ) AS t(data, pedido_bling, pedido_marketplace, plataforma_bling,
                   plataforma_financeiro, loja_nome, bling_custo_produtos)
            WHERE false
            """  # noqa: S608
        )
    )
    # force_refresh now reads vw_bling_pedidos directly (predicate pushdown),
    # not the heavy vw_conciliacao_margens_marketplace_all view.
    await db.execute(
        text(f'DROP VIEW IF EXISTS "{schema}".vw_bling_pedidos')
    )
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_bling_pedidos AS
            SELECT * FROM (VALUES
                ('2026-04-20T12:00:00+00:00'::timestamptz, '123456'::text, 'OLD999'::text,
                 'shopee'::text, 'Conta Historico'::text, 777::bigint,
                 42.00::numeric, 1::numeric)
            ) AS t(data, numero, numeroloja, marketplace, loja_nome, bling_id,
                   preco_custo, item_quantidade)
            """  # noqa: S608
        )
    )
    await db.execute(
        text(
            """
            INSERT INTO bling_orders (numero, numeroloja)
            VALUES ('123456', 'OLD999')
            """
        )
    )
    await db.commit()

    response = await client.get(
        "/api/refunds/order-lookup?pedido=OLD999&force_refresh=true"
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "data": "2026-04-20T12:00:00Z",
                "pedido_bling": "123456",
                "pedido_marketplace": "OLD999",
                "plataforma": "shopee",
                "conta": "Conta Historico",
                "custo_produto": 42.0,
                "custo_manutencao": None,
            }
        ],
        "historico_disponivel": False,
    }


async def test_order_cost_sums_bling_custo_produtos(
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
            SELECT * FROM (VALUES
                ('123456'::text, 'Conta View'::text, 12.50::numeric),
                ('123456'::text, 'Conta View'::text, 7.25::numeric),
                ('123456'::text, 'Outra Conta'::text, 99.00::numeric),
                ('999999'::text, 'Conta View'::text, 50.00::numeric)
            ) AS t(pedido_bling, loja_nome, bling_custo_produtos)
            """  # noqa: S608
        )
    )
    await db.commit()

    response = await client.get(
        "/api/refunds/order-cost",
        params={"pedido_bling": "123456", "conta": "Conta View"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "pedido_bling": "123456",
        "conta": "Conta View",
        "custo_produto": 19.75,
    }

    empty = await client.get(
        "/api/refunds/order-cost",
        params={"pedido_bling": "no-match", "conta": "Conta View"},
    )
    assert empty.status_code == 200
    assert empty.json()["custo_produto"] is None
