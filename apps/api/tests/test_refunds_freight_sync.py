from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.refunds_freight_sync import (
    backfill_freight_refunds,
    upsert_freight_refund_for_bling_order,
)

pytestmark = pytest.mark.asyncio


# Conftest cria o schema dos models do SQLAlchemy — migrations não rodam
# nos testes, então recriamos a view stub que o serviço lê.
async def _setup_view(db: AsyncSession, rows: list[dict]) -> None:
    schema = get_settings().database_schema
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_conciliacao_margens_marketplace'))

    if not rows:
        await db.execute(
            text(
                f"""
                CREATE VIEW "{schema}".vw_conciliacao_margens_marketplace AS
                SELECT
                    NULL::timestamptz AS data,
                    NULL::text AS pedido_bling,
                    NULL::text AS pedido_marketplace,
                    NULL::text AS plataforma_bling,
                    NULL::text AS plataforma_financeiro,
                    NULL::text AS loja_nome,
                    NULL::numeric AS frete_projetado_item,
                    NULL::numeric AS evento_freight,
                    NULL::numeric AS evento_frete_anuncio,
                    NULL::numeric AS item_proportion,
                    NULL::numeric AS marketplace_frete_real_cobrado_item
                WHERE false
                """  # noqa: S608
            )
        )
    else:
        unions = " UNION ALL ".join(
            f"""SELECT
                {f"'{r['data']}'::timestamptz" if r.get('data') else "NULL::timestamptz"} AS data,
                {f"'{r['pedido_bling']}'::text" if r.get('pedido_bling') else "NULL::text"} AS pedido_bling,
                {f"'{r['pedido_marketplace']}'::text" if r.get('pedido_marketplace') else "NULL::text"} AS pedido_marketplace,
                {f"'{r['plataforma_bling']}'::text" if r.get('plataforma_bling') else "NULL::text"} AS plataforma_bling,
                {f"'{r['plataforma_financeiro']}'::text" if r.get('plataforma_financeiro') else "NULL::text"} AS plataforma_financeiro,
                {f"'{r['loja_nome']}'::text" if r.get('loja_nome') else "NULL::text"} AS loja_nome,
                {r['frete_projetado_item']}::numeric AS frete_projetado_item,
                {r.get('evento_freight', 'NULL')}::numeric AS evento_freight,
                {r.get('evento_frete_anuncio', 'NULL')}::numeric AS evento_frete_anuncio,
                {r.get('item_proportion', '1')}::numeric AS item_proportion,
                {r.get('marketplace_frete_real_cobrado_item', 'NULL')}::numeric AS marketplace_frete_real_cobrado_item
            """
            for r in rows
        )
        await db.execute(
            text(
                f"""
                CREATE VIEW "{schema}".vw_conciliacao_margens_marketplace AS
                {unions}
                """  # noqa: S608
            )
        )
    await db.commit()


async def _count_refunds(db: AsyncSession, **where: str) -> int:
    schema = get_settings().database_schema
    clauses = " AND ".join(f"{k} = :{k}" for k in where)
    sql = f"SELECT count(*) FROM \"{schema}\".refunds"
    if clauses:
        sql += f" WHERE {clauses}"
    result = await db.execute(text(sql), where)
    return int(result.scalar_one())


async def _select_refund(db: AsyncSession, pedido_bling: str) -> dict | None:
    schema = get_settings().database_schema
    row = (
        await db.execute(
            text(
                f"SELECT pedido_bling, conta, tipo, prejuizo, conferido, plataforma "
                f"FROM \"{schema}\".refunds WHERE pedido_bling = :p AND tipo = 'Logistica'"
            ),
            {"p": pedido_bling},
        )
    ).mappings().first()
    return dict(row) if row else None


async def test_creates_refund_for_non_shopee_when_charged_exceeds_anuncio(db: AsyncSession):
    # ML order: frete anuncio R$10 mas marketplace cobrou R$15.
    # Esperado prejuizo = 15 - 10 = 5.
    await _setup_view(
        db,
        [
            {
                "data": "2026-05-20T12:00:00+00:00",
                "pedido_bling": "PED-001",
                "pedido_marketplace": "MLB-001",
                "plataforma_bling": "ml",
                "loja_nome": "Loja ML",
                "frete_projetado_item": 10,
                "evento_frete_anuncio": 10,
                "marketplace_frete_real_cobrado_item": 15,
            }
        ],
    )

    result = await upsert_freight_refund_for_bling_order(db, pedido_bling="PED-001")
    await db.commit()

    assert result["ok"] is True
    refund = await _select_refund(db, "PED-001")
    assert refund is not None
    assert refund["tipo"] == "Logistica"
    assert refund["conta"] == "Loja ML"
    assert refund["plataforma"] == "ml"
    assert refund["conferido"] is False
    assert float(refund["prejuizo"]) == pytest.approx(5.0)


async def test_ml_frete_anuncio_is_not_prorated_per_item(db: AsyncSession):
    # Pedido com 2 itens: item_proportion=0.5, mas frete anuncio fica cheio
    # em cada item. Exemplo real: 104,175 - 78,26 = 25,915 por item.
    await _setup_view(
        db,
        [
            {
                "data": "2026-06-01T00:00:00+00:00",
                "pedido_bling": "278867",
                "pedido_marketplace": "2000016712859896",
                "plataforma_bling": "ml",
                "loja_nome": "ML Marquezini",
                "frete_projetado_item": 90,
                "evento_frete_anuncio": 78.26,
                "item_proportion": 0.5,
                "marketplace_frete_real_cobrado_item": 104.175,
            },
            {
                "data": "2026-06-01T00:00:00+00:00",
                "pedido_bling": "278867",
                "pedido_marketplace": "2000016712859896",
                "plataforma_bling": "ml",
                "loja_nome": "ML Marquezini",
                "frete_projetado_item": 90,
                "evento_frete_anuncio": 78.26,
                "item_proportion": 0.5,
                "marketplace_frete_real_cobrado_item": 104.175,
            },
        ],
    )

    result = await upsert_freight_refund_for_bling_order(db, pedido_bling="278867")
    await db.commit()

    assert result["ok"] is True
    refund = await _select_refund(db, "278867")
    assert refund is not None
    assert float(refund["prejuizo"]) == pytest.approx(51.83)


async def test_skips_when_anuncio_exceeds_charged(db: AsyncSession):
    # Marketplace cobrou menos que o frete anuncio — sem perda, sem refund.
    await _setup_view(
        db,
        [
            {
                "pedido_bling": "PED-002",
                "plataforma_bling": "ml",
                "loja_nome": "Loja ML",
                "frete_projetado_item": 20,
                "evento_frete_anuncio": 20,
                "marketplace_frete_real_cobrado_item": 12,
            }
        ],
    )

    await upsert_freight_refund_for_bling_order(db, pedido_bling="PED-002")
    await db.commit()

    assert await _count_refunds(db, pedido_bling="PED-002") == 0


async def test_skips_when_no_financial_data_yet(db: AsyncSession):
    # marketplace_frete_real_cobrado_item NULL (financial sync ainda não
    # rodou) — não cria refund.
    await _setup_view(
        db,
        [
            {
                "pedido_bling": "PED-003",
                "plataforma_bling": "ml",
                "loja_nome": "Loja ML",
                "frete_projetado_item": 10,
                "evento_frete_anuncio": 10,
            }
        ],
    )

    await upsert_freight_refund_for_bling_order(db, pedido_bling="PED-003")
    await db.commit()

    assert await _count_refunds(db, pedido_bling="PED-003") == 0


async def test_does_not_create_duplicate_on_re_run(db: AsyncSession):
    # Re-execução não cria duplicata pro mesmo (pedido_bling, conta).
    await _setup_view(
        db,
        [
            {
                "pedido_bling": "PED-004",
                "plataforma_bling": "ml",
                "loja_nome": "Loja ML",
                "frete_projetado_item": 10,
                "evento_frete_anuncio": 10,
                "marketplace_frete_real_cobrado_item": 15,
            }
        ],
    )

    await upsert_freight_refund_for_bling_order(db, pedido_bling="PED-004")
    await db.commit()
    await upsert_freight_refund_for_bling_order(db, pedido_bling="PED-004")
    await db.commit()

    assert await _count_refunds(db, pedido_bling="PED-004", tipo="Logistica") == 1


async def test_never_overwrites_existing_logistica_refund(db: AsyncSession):
    # Usuário já criou um refund Logistica manual pro pedido. O auto
    # nunca sobrescreve — nem em re-execução com prejuizo diferente.
    schema = get_settings().database_schema
    await db.execute(
        text(
            f"""
            INSERT INTO "{schema}".refunds (
                pedido_bling, conta, tipo, prejuizo, reembolso,
                chamado, conferido
            ) VALUES (
                'PED-005', 'Loja ML', 'Logistica', 99, 50,
                'CH-MANUAL', false
            )
            """  # noqa: S608
        )
    )
    await db.commit()

    # View mostra que esse pedido qualifica (prejuizo seria 40).
    await _setup_view(
        db,
        [
            {
                "pedido_bling": "PED-005",
                "plataforma_bling": "ml",
                "loja_nome": "Loja ML",
                "frete_projetado_item": 10,
                "evento_frete_anuncio": 10,
                "marketplace_frete_real_cobrado_item": 50,
            }
        ],
    )

    await upsert_freight_refund_for_bling_order(db, pedido_bling="PED-005")
    await db.commit()

    # Não criou novo, não tocou no manual.
    assert await _count_refunds(db, pedido_bling="PED-005", tipo="Logistica") == 1
    refund = await _select_refund(db, "PED-005")
    assert refund is not None
    assert float(refund["prejuizo"]) == pytest.approx(99.0)


async def test_shopee_uses_evento_freight_with_floor_at_zero(db: AsyncSession):
    # Shopee: frete plataforma = GREATEST(evento_freight * item_proportion, 0).
    # evento_freight=8, item_proportion=1 → 8. Projetado 5 < 8 → prejuizo = 3.
    await _setup_view(
        db,
        [
            {
                "pedido_bling": "PED-006",
                "plataforma_bling": "shopee",
                "loja_nome": "Loja Shopee",
                "frete_projetado_item": 5,
                "evento_freight": 8,
                "evento_frete_anuncio": 5,
                "item_proportion": 1,
            }
        ],
    )

    await upsert_freight_refund_for_bling_order(db, pedido_bling="PED-006")
    await db.commit()

    refund = await _select_refund(db, "PED-006")
    assert refund is not None
    assert refund["plataforma"] == "shopee"
    assert float(refund["prejuizo"]) == pytest.approx(3.0)


async def test_backfill_processes_all_qualifying_pedidos(db: AsyncSession):
    await _setup_view(
        db,
        [
            # Qualifica.
            {
                "pedido_bling": "PED-100",
                "plataforma_bling": "ml",
                "loja_nome": "Loja A",
                "frete_projetado_item": 5,
                "evento_frete_anuncio": 5,
                "marketplace_frete_real_cobrado_item": 8,
            },
            # Qualifica.
            {
                "pedido_bling": "PED-101",
                "plataforma_bling": "ml",
                "loja_nome": "Loja A",
                "frete_projetado_item": 2,
                "evento_frete_anuncio": 2,
                "marketplace_frete_real_cobrado_item": 9,
            },
            # Não qualifica (cobrado < anuncio).
            {
                "pedido_bling": "PED-102",
                "plataforma_bling": "ml",
                "loja_nome": "Loja A",
                "frete_projetado_item": 20,
                "evento_frete_anuncio": 20,
                "marketplace_frete_real_cobrado_item": 5,
            },
        ],
    )

    result = await backfill_freight_refunds(db)
    await db.commit()

    assert result["ok"] is True
    assert await _count_refunds(db, tipo="Logistica") == 2
    assert await _count_refunds(db, pedido_bling="PED-100") == 1
    assert await _count_refunds(db, pedido_bling="PED-101") == 1
    assert await _count_refunds(db, pedido_bling="PED-102") == 0
