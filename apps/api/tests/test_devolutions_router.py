from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

pytestmark = pytest.mark.asyncio


def _devolution_permissions(*, view: bool = True, edit: bool = True, delete: bool = True) -> dict:
    return {"devolucoes": {"view": view, "edit": edit, "delete": delete}}


async def test_order_lookup_uses_base_catalog_name_when_split_sku_missing(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    user = await make_user(permissions=_devolution_permissions(edit=False, delete=False))
    auth_as(user)
    schema = get_settings().database_schema
    order_item_id = UUID("11111111-1111-1111-1111-111111111111")

    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.execute(
        text(
            """
            INSERT INTO products (id, user_id, sku, name, cost_price, situacao)
            VALUES (
                :phone_id,
                :user_id,
                'dg019.ra',
                'Uranyx Fossibot F105 12.64 - Preto',
                670,
                'A'
            ), (
                :fone_id,
                :user_id,
                'a003.pi',
                'Fone Uranyx UFB10 - Branco',
                40,
                'A'
            ), (
                :relogio_id,
                :user_id,
                'a004.pi',
                'Relogio Uranyx USW10 - Laranja',
                65,
                'A'
            )
            """
        ),
        {
            "phone_id": UUID("22222222-2222-2222-2222-222222222222"),
            "fone_id": UUID("33333333-3333-3333-3333-333333333333"),
            "relogio_id": UUID("44444444-4444-4444-4444-444444444444"),
            "user_id": user.id,
        },
    )
    await db.execute(
        text(
            """
            INSERT INTO bling_orders (id, numero, numeroloja, preco_custo)
            VALUES (:order_item_id, '240992', '251127TFQ6QURJ', 775)
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
                    'dg019.pi+a003.pi+a004.pi'::text,
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
    assert len(rows) == 3
    assert rows[0]["sku"] == "dg019.pi"
    assert rows[0]["produtos"] == "Uranyx Fossibot F105 12.64 - Preto"
    assert rows[0]["custo_produto"] == pytest.approx(775 / 3)
    assert rows[1]["sku"] == "a003.pi"
    assert rows[1]["produtos"] == "Fone Uranyx UFB10 - Branco"
    assert rows[1]["custo_produto"] == 40
    assert rows[2]["sku"] == "a004.pi"
    assert rows[2]["produtos"] == "Relogio Uranyx USW10 - Laranja"
    assert rows[2]["custo_produto"] == 65


async def test_order_lookup_numeric_does_not_match_recipient_name_substring(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    """Buscar nº de pedido não pode trazer outro pedido só porque os dígitos
    aparecem dentro do nickname embutido no nome do destinatário."""
    user = await make_user(permissions=_devolution_permissions(edit=False, delete=False))
    auth_as(user)
    schema = get_settings().database_schema

    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_devolucoes AS
            SELECT * FROM (VALUES
                (
                    '2025-10-30T03:00:00+00:00'::timestamptz,
                    '230724'::text,
                    '2000013615898090'::text,
                    'Loja 205370233'::text,
                    NULL::bigint,
                    'dg017.pi'::text,
                    'Uranyx Fossibot F109S 24.256 - Preto'::text,
                    1::integer,
                    NULL::uuid,
                    NULL::text,
                    NULL::text,
                    NULL::text, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text
                ),
                (
                    '2026-04-07T21:00:00+00:00'::timestamptz,
                    '267954'::text,
                    '2000015897926172'::text,
                    'ML Vlta'::text,
                    NULL::bigint,
                    'a004.sp'::text,
                    'Relogio Uranyx USW10 - Laranja'::text,
                    1::integer,
                    NULL::uuid,
                    'Davi Santos Machado (santosdavi20230724154447)'::text,
                    NULL::text,
                    NULL::text, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text
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
        )
    )
    await db.commit()

    try:
        response = await client.get("/api/devolutions/order-lookup?pedido=230724")
    finally:
        await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
        await db.commit()

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["pedido_bling"] == "230724"
    assert all(r["pedido_bling"] != "267954" for r in rows)
