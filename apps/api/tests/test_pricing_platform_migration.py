"""Backfill real Carrefour/Netshoes em schema PostgreSQL descartável."""

# Os identificadores SQL vêm de uuid4, nunca de entrada externa.
# ruff: noqa: S608

import importlib.util
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


async def test_pricing_platform_migration_backfills_idempotently_in_external_transaction(
    db, monkeypatch,
):
    path = Path(__file__).parents[1] / "alembic/versions/0379_pricing_carrefour_netshoes.py"
    spec = importlib.util.spec_from_file_location("pricing_platform_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = "pricing_platform_migration_" + uuid4().hex
    monkeypatch.setattr(migration, "SCHEMA", schema)
    engine = db.bind
    owner, other_owner = uuid4(), uuid4()
    segments = {slug: uuid4() for slug in ("celular", "mala", "eletro", "catalogo", "child")}
    stores = [
        (uuid4(), owner, "carrefour", "Poofy", ["mala", "mala", "catalogo", "child", "missing"]),
        (uuid4(), owner, " CARREFOUR ", "VR ", ["celular", "eletro"]),
        (uuid4(), owner, "netshoes", "Poofy", ["mala"]),
        (uuid4(), owner, "site", "Site", ["mala"]),
        (uuid4(), owner, "carrefour", "Sem tipo", None),
        (uuid4(), other_owner, "carrefour", "Poofy", ["mala"]),
        (uuid4(), owner, "carrefour", "  ", ["mala"]),
    ]
    existing_id = uuid4()
    money_fields = ["commission"] + [
        f"{kind}{slot}" for slot in range(1, 6) for kind in ("margin", "shipping")
    ]

    def upgrade(connection):
        # Mesmo envelope de transação externa usado pelo alembic/env.py.
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)
        with context.begin_transaction():
            migration.upgrade()
            connection.execute(text(
                f'INSERT INTO "{schema}".revision_marker VALUES (:revision) ON CONFLICT DO NOTHING'
            ), {"revision": migration.revision})

    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'CREATE TYPE "{schema}".pricing_platform AS ENUM '
                                    "('mercadolivre','shopee','temu','amazon','aliexpress',"
                                    "'tiktok','magalu','shein')"))
            await conn.execute(text(f'CREATE TABLE "{schema}".segments '
                                    '(id uuid PRIMARY KEY, slug text, parent_id uuid)'))
            await conn.execute(text(f'CREATE TABLE "{schema}".store_info ('
                                    'id uuid PRIMARY KEY, user_id uuid, platform text, '
                                    'account_name text, manual_departments text[], '
                                    'archived_at timestamptz, observation text)'))
            money_columns = ', '.join(f'{field} numeric' for field in money_fields)
            await conn.execute(text(f'CREATE TABLE "{schema}".pricing_accounts ('
                                    'id uuid PRIMARY KEY, user_id uuid NOT NULL, '
                                    'name text NOT NULL, '
                                    f'platform "{schema}".pricing_platform NOT NULL, '
                                    'segment_id uuid NOT NULL, store_info_id uuid, '
                                    'canal text NOT NULL, kit_number integer NOT NULL, '
                                    f'sort_order integer NOT NULL, {money_columns}, '
                                    'integration_id uuid)'))
            await conn.execute(text(f'CREATE TABLE "{schema}".revision_marker '
                                    '(revision text PRIMARY KEY)'))
            for slug, segment_id in segments.items():
                await conn.execute(text(f'INSERT INTO "{schema}".segments VALUES '
                                        '(:id,:slug,:parent)'), {
                    "id": segment_id, "slug": slug,
                    "parent": segments["mala"] if slug == "child" else None,
                })
            for store_id, user_id, platform, name, departments in stores:
                await conn.execute(text(f'INSERT INTO "{schema}".store_info VALUES '
                                        '(:id,:owner,:platform,:name,:types,'
                                        "CASE WHEN :archived THEN now() END,'Preservar')"), {
                    "id": store_id, "owner": user_id, "platform": platform, "name": name,
                    "types": departments, "archived": platform == "netshoes",
                })
            await conn.execute(text(f'INSERT INTO "{schema}".pricing_accounts '
                                    '(id,user_id,name,platform,segment_id,canal,kit_number,'
                                    'sort_order,commission,margin1,shipping1) VALUES '
                                    "(:id,:owner,'Existente','amazon',:segment,'kit',2,7,"
                                    '0.19,0.33,21.50)'), {
                "id": existing_id, "owner": owner, "segment": segments["mala"],
            })

        async with engine.begin() as conn:
            before_stores = (await conn.execute(text(
                f'SELECT id,user_id,platform,account_name,archived_at,observation '
                f'FROM "{schema}".store_info ORDER BY id'
            ))).all()
            await conn.run_sync(upgrade)

        async with engine.begin() as conn:
            rows = (await conn.execute(text(
                f'SELECT * FROM "{schema}".pricing_accounts ORDER BY id'
            ))).mappings().all()
            created = [row for row in rows if row["id"] != existing_id]
            assert len(created) == 6
            expected = {
                (store[0], segments[slug], store[1])
                for store, slugs in (
                    (stores[0], ["mala"]), (stores[1], ["celular", "eletro"]),
                    (stores[2], ["mala"]), (stores[5], ["mala"]), (stores[6], ["mala"]),
                ) for slug in slugs
            }
            actual = {(r["store_info_id"], r["segment_id"], r["user_id"]) for r in created}
            assert actual == expected
            for row in created:
                assert row["platform"] in ("carrefour", "netshoes")
                assert row["canal"] == "kit"
                assert row["integration_id"] is None
                assert all(row[field] is None for field in money_fields)
            fallback = next(r for r in created if r["store_info_id"] == stores[6][0])
            assert fallback["name"] == "carrefour — mala"
            assert (await conn.execute(text(
                f'SELECT id,user_id,platform,account_name,archived_at,observation '
                f'FROM "{schema}".store_info ORDER BY id'
            ))).all() == before_stores
            tags = dict((await conn.execute(text(
                f'SELECT id,manual_departments FROM "{schema}".store_info'
            ))).all())
            assert tags[stores[0][0]] == ["catalogo", "child", "missing"]
            assert tags[stores[3][0]] == ["mala"]
            assert all(tags[stores[i][0]] is None for i in (1, 2, 4, 5, 6))
            await conn.execute(text(f'UPDATE "{schema}".pricing_accounts '
                                    'SET commission=0.13, margin2=0.27, shipping2=11.90 '
                                    'WHERE id=:id'), {"id": created[0]["id"]})
            before_retry = (await conn.execute(text(
                f'SELECT * FROM "{schema}".pricing_accounts ORDER BY id'
            ))).all()

        async with engine.begin() as conn:
            await conn.run_sync(upgrade)

        async with engine.begin() as conn:
            assert (await conn.execute(text(
                f'SELECT * FROM "{schema}".pricing_accounts ORDER BY id'
            ))).all() == before_retry
            original = (await conn.execute(text(
                f'SELECT commission,margin1,shipping1 FROM "{schema}".pricing_accounts WHERE id=:id'
            ), {"id": existing_id})).one()
            assert tuple(original) == (Decimal("0.19"), Decimal("0.33"), Decimal("21.50"))
            values = (await conn.execute(text(
                f'SELECT unnest(enum_range(NULL::"{schema}".pricing_platform))::text'
            ))).scalars().all()
            assert values.count("carrefour") == values.count("netshoes") == 1
            assert (await conn.execute(text(
                f'SELECT revision FROM "{schema}".revision_marker'
            ))).scalars().all() == [migration.revision]
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
