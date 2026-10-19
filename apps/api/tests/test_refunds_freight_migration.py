"""Compara a função migrada com a view, incluindo grão e limites de data reais."""

# Identificadores SQL locais, criados por uuid4; valores são parametrizados.
# ruff: noqa: S608

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0418_refunds_freight_for_pedido.py"
    spec = importlib.util.spec_from_file_location("refunds_freight_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("count", [0, 1, 3])
def test_migration_refuses_changed_date_guard_structure(count):
    migration = _migration()
    with pytest.raises(RuntimeError, match="dois filtros de 20 dias"):
        migration._scoped_body("\n".join([migration._DATE_GUARD] * count))


async def test_migrated_function_preserves_rows_dates_and_pricing_multiplicity(db, monkeypatch):
    migration = _migration()
    schema = "freight_equivalence_" + uuid4().hex
    monkeypatch.setattr(migration, "SCHEMA", schema)

    def migrate(connection, direction):
        migration.op = Operations(MigrationContext.configure(connection))
        getattr(migration, direction)()

    async with db.bind.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        try:
            # Mesmos pontos de entrada e mesma chave de junção da view real.
            # As colunas de cálculo irrelevantes são omitidas, sem mockar o SQL
            # da migration: pg_get_viewdef + CREATE FUNCTION rodam no PostgreSQL.
            await connection.execute(text(f'CREATE TABLE "{schema}".order_items ('
                                          "id integer PRIMARY KEY, bling_id bigint, numero text, "
                                          "data timestamptz, item_codigo text, loja text, "
                                          "freight numeric)"))
            await connection.execute(text(f'CREATE VIEW "{schema}".vw_bling_pedidos '
                                          f'AS SELECT * FROM "{schema}".order_items'))
            await connection.execute(text(f"""
                CREATE VIEW "{schema}".{migration.VIEW} AS
                WITH bp_keys AS (
                    SELECT DISTINCT vw_bling_pedidos.bling_id,
                        vw_bling_pedidos.item_codigo, vw_bling_pedidos.loja
                    FROM "{schema}".vw_bling_pedidos
                    WHERE vw_bling_pedidos.data >= (now() - interval '20 days')
                ), pricing_match AS (
                    SELECT * FROM bp_keys
                ), joined AS (
                    SELECT * FROM "{schema}".vw_bling_pedidos
                    WHERE vw_bling_pedidos.data >= (now() - interval '20 days')
                )
                SELECT j.id, j.bling_id, j.numero AS pedido_bling, j.data,
                    j.item_codigo, j.freight, pm.loja AS pricing_store
                FROM joined j LEFT JOIN pricing_match pm
                    ON pm.bling_id = j.bling_id AND pm.item_codigo = j.item_codigo
            """))
            # Mesmo número em IDs diferentes; mesmo ID em números diferentes;
            # loja antiga não pode multiplicar linhas recentes; SKU repetido;
            # pedido sem ID; fronteira de 20 dias inclusiva; futuro preservado.
            rows = [
                (1, 100, "target", "1 day", "sku-a", "A", 10),
                (2, 100, "target", "1 day", "sku-a", "A", 20),
                (3, 100, "other-number", "2 days", "sku-a", "B", 30),
                (4, 100, "old", "21 days", "sku-a", "OLD", 40),
                (5, 200, "target", "3 days", "sku-b", "C", 50),
                (6, 300, "boundary", "20 days", "sku-c", "D", 60),
                (7, 300, "outside", "20 days 1 microsecond", "sku-c", "OLD2", 70),
                (8, None, "no-id", "1 day", "sku-d", "E", 80),
                (9, 400, "future", "-1 day", "sku-e", "F", 90),
                (10, 500, "aspas ' e acento ç", "1 day", "sku-f", "G", 100),
                (11, 600, "null-date", None, "sku-g", "H", 110),
            ]
            for row in rows:
                await connection.execute(text(f'INSERT INTO "{schema}".order_items '
                                              "VALUES (:id,:bling_id,:numero,"
                                              "now()-CAST(CAST(:age AS text) AS interval),"
                                              ":sku,:loja,:freight)"),
                                         dict(zip(("id", "bling_id", "numero", "age", "sku",
                                                   "loja", "freight"), row, strict=True)))
            before = (await connection.execute(text(
                f'SELECT * FROM "{schema}".order_items ORDER BY id'
            ))).all()
            await connection.run_sync(lambda conn: migrate(conn, "upgrade"))
            # Reexecutar CREATE OR REPLACE também mantém a equivalência.
            await connection.run_sync(lambda conn: migrate(conn, "upgrade"))
            for number in [row[2] for row in rows] + ["missing", "", None]:
                legacy = (await connection.execute(text(
                    f'SELECT * FROM "{schema}".{migration.VIEW} '
                    "WHERE pedido_bling=:number ORDER BY id,pricing_store"
                ), {"number": number})).all()
                scoped = (await connection.execute(text(
                    f'SELECT * FROM "{schema}".{migration.FN}(:number) '
                    "ORDER BY id,pricing_store"
                ), {"number": number})).all()
                assert scoped == legacy, number
            target = (await connection.execute(text(
                f'SELECT id,pricing_store FROM "{schema}".{migration.FN}(\'target\') '
                "ORDER BY id,pricing_store"
            ))).all()
            assert target == [(1, "A"), (1, "B"), (2, "A"), (2, "B"), (5, "C")]
            assert (await connection.execute(text(
                f'SELECT * FROM "{schema}".order_items ORDER BY id'
            ))).all() == before
            await connection.run_sync(lambda conn: migrate(conn, "downgrade"))
            assert (await connection.execute(text("SELECT to_regprocedure(:name)"), {
                "name": f"{schema}.{migration.FN}(text)",
            })).scalar_one() is None
            # Downgrade remove só a função. View e dados continuam disponíveis.
            assert (await connection.execute(text(
                f'SELECT count(*) FROM "{schema}".{migration.VIEW}'
            ))).scalar_one() > 0
        finally:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
