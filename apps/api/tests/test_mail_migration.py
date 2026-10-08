"""Apply and reverse the real migration in an isolated, disposable schema."""

import importlib.util
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.models import Base


def test_mail_migration_extends_main_without_creating_another_head():
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert len(scripts.get_heads()) == 1, "mail migration must extend the current main chain"
    migration = scripts.get_revision("0386_mail_central")
    assert migration.down_revision == "0385_flex_origem"
    assert migration.revision in {item.revision for item in scripts.walk_revisions()}


async def test_mail_migration_matches_models_and_downgrades(db):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0386_mail_central.py"
    spec = importlib.util.spec_from_file_location("mail_migration_test", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = f"mail_migration_{uuid4().hex[:12]}"
    migration.SCHEMA = schema
    tables = ["mail_mailboxes", "mail_messages", "mail_attachments", "mail_outbox"]

    def catalog(connection, selected_schema):
        inspector = inspect(connection)
        result = {}
        for name in tables:
            result[name] = {
                "columns": [
                    (c["name"], str(c["type"]), c["nullable"], c["default"])
                    for c in inspector.get_columns(name, schema=selected_schema)
                ],
                "uniques": sorted(
                    tuple(c["column_names"])
                    for c in inspector.get_unique_constraints(name, schema=selected_schema)
                ),
                "indexes": sorted(
                    (i["name"], tuple(i["column_names"]), i["unique"])
                    for i in inspector.get_indexes(name, schema=selected_schema)
                ),
            }
        return result

    async def run(step):
        connection = await db.connection()

        def execute(sync_connection):
            context = MigrationContext.configure(
                sync_connection, opts={"target_metadata": Base.metadata}
            )
            with Operations.context(context):
                getattr(migration, step)()

        await connection.run_sync(execute)

    # Identifiers are generated locally, never taken from request data.
    await db.execute(text(f'CREATE SCHEMA "{schema}"'))  # noqa: S608
    await db.execute(text(f'CREATE TABLE "{schema}".users (id uuid PRIMARY KEY)'))  # noqa: S608
    try:
        await run("upgrade")
        connection = await db.connection()
        actual = await connection.run_sync(catalog, schema)
        expected = await connection.run_sync(catalog, Base.metadata.schema)
        assert actual == expected
        await run("downgrade")
        connection = await db.connection()
        remaining = await connection.run_sync(lambda c: inspect(c).get_table_names(schema=schema))
        assert remaining == ["users"]
    finally:
        await db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))  # noqa: S608
        await db.commit()
