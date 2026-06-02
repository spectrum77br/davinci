from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

pytestmark = pytest.mark.asyncio


def _devolution_permissions(*, view: bool = True, edit: bool = True, delete: bool = True) -> dict:
    return {"devolucoes": {"view": view, "edit": edit, "delete": delete}}


async def test_order_lookup_uses_catalog_name_for_plain_sku(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    user = await make_user(permissions=_devolution_permissions(edit=False, delete=False))
    auth_as(user)
    schema = get_settings().database_schema
    product_id = UUID("22222222-2222-2222-2222-222222222222")
    order_item_id = UUID("11111111-1111-1111-1111-111111111111")

    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.execute(
        text(
            """
            INSERT INTO products (id, user_id, sku, name, cost_price, situacao)
            VALUES (
                :product_id,
                :user_id,
                'dg019.pi',
                'Uranyx Fossibot F105 12.64 - Preto',
                258.3333,
                'A'
            )
            """
        ),
        {"product_id": product_id, "user_id": user.id},
    )
    await db.execute(
        text(
            """
            INSERT INTO bling_orders (id, numero, numeroloja, preco_custo)
            VALUES (:order_item_id, '240992', '251127TFQ6QURJ', 258.3333)
            """
        ),
        {"order_item_id": order_item_id},
    )
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_devolucoes AS
            SELECT * FROM (VALUES
                (
                    '2025-11-26T03:00:00+00:00'::timestamptz,
                    '240992'::text,
                    '251127TFQ6QURJ'::text,
                    'Shopee Jlas'::text,
                    NULL::bigint,
                    'dg019.pi'::text,
                    'Uranyx Fossibot F105 12.64 - Preto + Fone U9 + Relogio'::text,
                    1::integer,
                    CAST(:order_item_id AS uuid),
                    NULL::text,
                    NULL::text,
                    NULL::text,
                    NULL::text,
                    NULL::text,
                    NULL::text,
                    NULL::text,
                    NULL::text
                )
            ) AS t(
                data,
                pedido_bling,
                pedido_marketplace,
                loja_nome,
                bling_loja_id,
                sku,
                produto,
                quantidade,
                bling_order_item_id,
                nome_destinatario,
                cep_destino,
                endereco_destino,
                numero_destino,
                complemento_destino,
                bairro_destino,
                cidade_destino,
                uf_destino
            )
            """  # noqa: S608
        ),
        {"order_item_id": order_item_id},
    )
    await db.commit()

    try:
        response = await client.get("/api/devolutions/order-lookup?pedido=240992")
    finally:
        await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
        await db.commit()

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["sku"] == "dg019.pi"
    assert rows[0]["produtos"] == "Uranyx Fossibot F105 12.64 - Preto"
    assert rows[0]["custo_produto"] == 258.3333
