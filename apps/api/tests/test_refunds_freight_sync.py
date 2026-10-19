# SQL de fixtures locais: schema controlado pelo conftest, dados fictícios.
# ruff: noqa: S608

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services import refunds_freight_sync as service
from app.services.refunds_freight_sync import (
    backfill_freight_refunds,
    upsert_freight_refund_for_bling_order,
)

pytestmark = pytest.mark.asyncio


# Os stubs preservam as janelas reais: 20d no hook, 90d no backfill.
_VIEW_STUBS = (
    "vw_conciliacao_margens_marketplace",  # hook (por pedido)
    "vw_conciliacao_margens_marketplace_all",  # backfill (janela própria 90d)
)

# Datas relativas pro teste da janela de 90d do backfill.
_DATA_RECENTE = (datetime.now(UTC) - timedelta(days=5)).isoformat()
_DATA_ANTIGA = (datetime.now(UTC) - timedelta(days=120)).isoformat()


@pytest.fixture(autouse=True)
async def _cleanup_freight_views(db):
    yield
    await db.rollback()
    schema = get_settings().database_schema
    await db.execute(text(f'DROP FUNCTION IF EXISTS "{schema}".refunds_freight_for_pedido(text)'))
    for view in _VIEW_STUBS:
        await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".{view}'))
    await db.commit()


def _data_sql(row: dict) -> str:
    if "age_days" in row:
        # now() é estável na transação: cobre exatamente 20/90 dias sem
        # depender dos microssegundos entre preparar e consultar o fixture.
        return f"(now() - interval '{float(row['age_days'])} days')"
    return f"'{row['data']}'::timestamptz" if row.get("data") else "NULL::timestamptz"


def _text_sql(value) -> str:
    return "'" + str(value).replace("'", "''") + "'::text" if value is not None else "NULL::text"


# Conftest cria o schema dos models do SQLAlchemy — migrations não rodam
# nos testes, então recriamos as views stub que o serviço lê.
async def _setup_view(db: AsyncSession, rows: list[dict]) -> None:
    schema = get_settings().database_schema
    rows = [{"data": _DATA_RECENTE, **row} for row in rows]

    if not rows:
        body = """
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
        """
    else:
        body = " UNION ALL ".join(
            f"""SELECT
                {_data_sql(r)} AS data,
                {_text_sql(r.get("pedido_bling"))} AS pedido_bling,
                {_text_sql(r.get("pedido_marketplace"))} AS pedido_marketplace,
                {_text_sql(r.get("plataforma_bling"))} AS plataforma_bling,
                {_text_sql(r.get("plataforma_financeiro"))} AS plataforma_financeiro,
                {_text_sql(r.get("loja_nome"))} AS loja_nome,
                {r["frete_projetado_item"]}::numeric AS frete_projetado_item,
                {r.get("evento_freight", "NULL")}::numeric AS evento_freight,
                {r.get("evento_frete_anuncio", "NULL")}::numeric AS evento_frete_anuncio,
                {r.get("item_proportion", "1")}::numeric AS item_proportion,
                {r.get("marketplace_frete_real_cobrado_item", "NULL")}::numeric
                    AS marketplace_frete_real_cobrado_item
            """
            for r in rows
        )

    await db.execute(text(f'DROP FUNCTION IF EXISTS "{schema}".refunds_freight_for_pedido(text)'))
    for view in _VIEW_STUBS:
        await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".{view}'))
        await db.execute(
            text(
                f'CREATE VIEW "{schema}".{view} AS SELECT * FROM ({body}) v '
                + ("WHERE v.data >= now() - interval '20 days'" if view == _VIEW_STUBS[0] else "")
            )  # noqa: S608
        )
    await db.execute(
        text(f"""
        CREATE FUNCTION "{schema}".refunds_freight_for_pedido(p_pedido_bling text)
        RETURNS SETOF "{schema}".{_VIEW_STUBS[0]} LANGUAGE sql STABLE AS $fn$
            SELECT * FROM "{schema}".{_VIEW_STUBS[0]} WHERE pedido_bling = p_pedido_bling
        $fn$
    """)
    )
    await db.commit()


async def _count_refunds(db: AsyncSession, **where: str) -> int:
    schema = get_settings().database_schema
    clauses = " AND ".join(f"{k} = :{k}" for k in where)
    sql = f'SELECT count(*) FROM "{schema}".refunds'
    if clauses:
        sql += f" WHERE {clauses}"
    result = await db.execute(text(sql), where)
    return int(result.scalar_one())


async def _select_refund(db: AsyncSession, pedido_bling: str) -> dict | None:
    schema = get_settings().database_schema
    row = (
        (
            await db.execute(
                text(
                    f"SELECT pedido_bling, conta, tipo, prejuizo, conferido, plataforma "
                    f"FROM \"{schema}\".refunds WHERE pedido_bling = :p AND tipo = 'Logistica'"
                ),
                {"p": pedido_bling},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def test_creates_refund_for_non_shopee_when_charged_exceeds_anuncio(db: AsyncSession):
    # ML order: frete anuncio R$10 mas marketplace cobrou R$20.
    # Esperado prejuizo = 20 - 10 = 10 (acima do piso de R$5).
    await _setup_view(
        db,
        [
            {
                "data": _DATA_RECENTE,
                "pedido_bling": "PED-001",
                "pedido_marketplace": "MLB-001",
                "plataforma_bling": "ml",
                "loja_nome": "Loja ML",
                "frete_projetado_item": 10,
                "evento_frete_anuncio": 10,
                "marketplace_frete_real_cobrado_item": 20,
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
    assert float(refund["prejuizo"]) == pytest.approx(10.0)


async def test_ml_frete_anuncio_is_not_prorated_per_item(db: AsyncSession):
    # Pedido com 2 itens: item_proportion=0.5, mas frete anuncio fica cheio
    # em cada item. Exemplo real: 104,175 - 78,26 = 25,915 por item.
    await _setup_view(
        db,
        [
            {
                "data": _DATA_RECENTE,
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
                "data": _DATA_RECENTE,
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


async def test_skips_when_prejuizo_below_min_threshold(db: AsyncSession):
    # Diferença de frete < R$5 nem compensa cobrar reembolso — não cria.
    # Cobrado R$14, anuncio R$10 → prejuizo R$4 (abaixo do piso de R$5).
    await _setup_view(
        db,
        [
            {
                "pedido_bling": "PED-007",
                "plataforma_bling": "ml",
                "loja_nome": "Loja ML",
                "frete_projetado_item": 10,
                "evento_frete_anuncio": 10,
                "marketplace_frete_real_cobrado_item": 14,
            }
        ],
    )

    await upsert_freight_refund_for_bling_order(db, pedido_bling="PED-007")
    await db.commit()

    assert await _count_refunds(db, pedido_bling="PED-007") == 0


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
                "marketplace_frete_real_cobrado_item": 20,
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
    # evento_freight=12, item_proportion=1 → 12. Projetado 5 < 12 → prejuizo = 7.
    await _setup_view(
        db,
        [
            {
                "pedido_bling": "PED-006",
                "plataforma_bling": "shopee",
                "loja_nome": "Loja Shopee",
                "frete_projetado_item": 5,
                "evento_freight": 12,
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
    assert float(refund["prejuizo"]) == pytest.approx(7.0)


async def test_backfill_processes_all_qualifying_pedidos(db: AsyncSession):
    # O backfill filtra por data (janela de 90d), então as linhas precisam
    # de uma data dentro da janela.
    await _setup_view(
        db,
        [
            # Qualifica (prejuizo 12 - 5 = 7, acima do piso de R$5).
            {
                "data": _DATA_RECENTE,
                "pedido_bling": "PED-100",
                "plataforma_bling": "ml",
                "loja_nome": "Loja A",
                "frete_projetado_item": 5,
                "evento_frete_anuncio": 5,
                "marketplace_frete_real_cobrado_item": 12,
            },
            # Qualifica.
            {
                "data": _DATA_RECENTE,
                "pedido_bling": "PED-101",
                "plataforma_bling": "ml",
                "loja_nome": "Loja A",
                "frete_projetado_item": 2,
                "evento_frete_anuncio": 2,
                "marketplace_frete_real_cobrado_item": 9,
            },
            # Não qualifica (cobrado < anuncio).
            {
                "data": _DATA_RECENTE,
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


async def test_backfill_window_90d_ignora_pedidos_antigos(db: AsyncSession):
    # A janela própria do backfill (90d na view _all) existe pra pegar
    # cobranças de frete que o ML fecha semanas depois — mas sem
    # ressuscitar pedidos antigos demais. Fora da janela → não cria.
    await _setup_view(
        db,
        [
            # Dentro da janela → cria.
            {
                "data": _DATA_RECENTE,
                "pedido_bling": "PED-200",
                "plataforma_bling": "ml",
                "loja_nome": "Loja A",
                "frete_projetado_item": 5,
                "evento_frete_anuncio": 5,
                "marketplace_frete_real_cobrado_item": 15,
            },
            # Fora da janela (120 dias atrás) → backfill não cria.
            {
                "data": _DATA_ANTIGA,
                "pedido_bling": "PED-201",
                "plataforma_bling": "ml",
                "loja_nome": "Loja A",
                "frete_projetado_item": 5,
                "evento_frete_anuncio": 5,
                "marketplace_frete_real_cobrado_item": 15,
            },
        ],
    )

    result = await backfill_freight_refunds(db)
    await db.commit()

    assert result["ok"] is True
    assert await _count_refunds(db, pedido_bling="PED-200") == 1
    assert await _count_refunds(db, pedido_bling="PED-201") == 0


def _row(**overrides):
    return {
        "pedido_bling": "EDGE",
        "pedido_marketplace": "EXTERNAL",
        "plataforma_bling": "ml",
        "loja_nome": "Loja A",
        "frete_projetado_item": 10,
        "evento_frete_anuncio": 10,
        "marketplace_frete_real_cobrado_item": 20,
        **overrides,
    }


@pytest.mark.parametrize("difference,expected", [(4.99, 0), (5, 1), (5.01, 1)])
async def test_exact_minimum_loss(db, difference, expected):
    await _setup_view(db, [_row(marketplace_frete_real_cobrado_item=10 + difference)])
    result = await upsert_freight_refund_for_bling_order(db, pedido_bling="EDGE")
    assert result["rows"] == expected
    refund = await _select_refund(db, "EDGE")
    if expected:
        assert refund["prejuizo"] == pytest.approx(difference)
    else:
        assert refund is None


async def test_sums_positive_items_without_subtracting_negative_items(db):
    await _setup_view(
        db,
        [
            _row(marketplace_frete_real_cobrado_item=13),
            _row(marketplace_frete_real_cobrado_item=12),
            _row(marketplace_frete_real_cobrado_item=0),
            _row(marketplace_frete_real_cobrado_item="NULL"),
        ],
    )
    await upsert_freight_refund_for_bling_order(db, pedido_bling="EDGE")
    assert (await _select_refund(db, "EDGE"))["prejuizo"] == 5


@pytest.mark.parametrize(
    "overrides",
    [
        {"loja_nome": None},
        {"loja_nome": "   "},
        {"pedido_bling": None},
        {"data": None},
        {"evento_frete_anuncio": "NULL"},
        {"marketplace_frete_real_cobrado_item": "NULL"},
        {"plataforma_bling": "shopee", "evento_freight": "NULL"},
        {"plataforma_bling": "shopee", "evento_freight": -20},
    ],
)
async def test_incomplete_or_nonqualifying_data_does_not_create_refund(db, overrides):
    await _setup_view(db, [_row(**overrides)])
    await upsert_freight_refund_for_bling_order(db, pedido_bling="EDGE")
    assert await _count_refunds(db) == 0


async def test_same_number_in_multiple_accounts_and_platform_fallback(db):
    await _setup_view(
        db,
        [
            _row(loja_nome="  Loja A  "),
            _row(
                loja_nome="Loja B",
                plataforma_bling=None,
                plataforma_financeiro="shopee",
                evento_freight=30,
                item_proportion=0.5,
            ),
            _row(pedido_bling="UNRELATED"),
        ],
    )
    await upsert_freight_refund_for_bling_order(db, pedido_bling="EDGE")
    schema = get_settings().database_schema
    rows = (
        await db.execute(
            text(f'SELECT conta, plataforma, prejuizo FROM "{schema}".refunds ORDER BY conta')
        )
    ).all()
    assert rows == [("Loja A", "ml", 10), ("Loja B", "shopee", 5)]


@pytest.mark.parametrize(
    "age,hook_rows,backfill_rows",
    [
        (0, 1, 0),
        (20, 1, 0),
        (20.001, 0, 1),
        (35, 0, 1),
        (90, 0, 1),
        (90.001, 0, 0),
    ],
)
async def test_hook_and_daily_backfill_keep_their_date_boundaries(
    db, age, hook_rows, backfill_rows
):
    await _setup_view(db, [_row(age_days=age)])
    hook = await upsert_freight_refund_for_bling_order(db, pedido_bling="EDGE")
    backfill = await backfill_freight_refunds(db)
    assert hook["rows"] == hook_rows
    assert backfill["rows"] == backfill_rows
    assert await _count_refunds(db) == hook_rows + backfill_rows


async def test_hook_and_backfill_preserve_manual_row_entirely(db):
    await _setup_view(db, [_row()])
    schema = get_settings().database_schema
    await db.execute(
        text(f"""
        INSERT INTO "{schema}".refunds
            (pedido_bling, conta, tipo, prejuizo, reembolso, chamado, conferido, observacao)
        VALUES ('EDGE', 'Loja A', 'Logistica', 99, 50, 'MANUAL', true, 'Preservar')
    """)
    )
    query = text(f'SELECT * FROM "{schema}".refunds WHERE pedido_bling = :p')
    before = (await db.execute(query, {"p": "EDGE"})).mappings().all()
    await upsert_freight_refund_for_bling_order(db, pedido_bling="EDGE")
    await backfill_freight_refunds(db)
    after = (await db.execute(query, {"p": "EDGE"})).mappings().all()
    assert after == before


async def test_missing_number_never_queries(db):
    assert await upsert_freight_refund_for_bling_order(db, pedido_bling="") == {
        "ok": False,
        "skipped": "missing_pedido_bling",
    }


async def test_per_process_gate_bounds_queries_and_does_not_drop_work(monkeypatch):
    monkeypatch.setattr(service, "_ORDER_CONCURRENCY", asyncio.Semaphore(1))
    active = peak = 0
    completed = []

    class Session:
        @asynccontextmanager
        async def begin_nested(self):
            yield

        async def execute(self, sql, params):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.005)
            completed.append(params["pedido_bling"])
            active -= 1
            return SimpleNamespace(rowcount=1)

    results = await asyncio.gather(
        *(upsert_freight_refund_for_bling_order(Session(), pedido_bling=str(i)) for i in range(8))
    )
    assert peak == 1
    assert len(completed) == 8
    assert sum(result["rows"] for result in results) == 8


async def test_cancelled_query_releases_gate_for_next_order(monkeypatch):
    monkeypatch.setattr(service, "_ORDER_CONCURRENCY", asyncio.Semaphore(1))
    started = asyncio.Event()
    released = asyncio.Event()

    class Session:
        @asynccontextmanager
        async def begin_nested(self):
            try:
                yield
            finally:
                released.set()

        async def execute(self, sql, params):
            if params["pedido_bling"] == "CANCEL":
                started.set()
                await asyncio.Event().wait()
            return SimpleNamespace(rowcount=1)

    task = asyncio.create_task(
        upsert_freight_refund_for_bling_order(Session(), pedido_bling="CANCEL")
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released.is_set()
    next_result = await asyncio.wait_for(
        upsert_freight_refund_for_bling_order(Session(), pedido_bling="NEXT"),
        timeout=1,
    )
    assert next_result["rows"] == 1
