"""Exercita upgrade/backfill e downgrade numa tabela isolada do Postgres de teste."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.db as app_db


async def test_migration_preserves_existing_manual_fields_without_marking_official_rows(
    monkeypatch,
):
    path = Path(__file__).parents[1] / "alembic/versions/0449_certificacoes_edicao_manual.py"
    spec = importlib.util.spec_from_file_location("certificacoes_edicao_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = f"certificacoes_migration_{uuid4().hex}"

    async with app_db.engine.begin() as connection:
        await connection.execute(sa.schema.CreateSchema(schema))

        def run(sync_connection):
            # Redireciona somente o schema: a operação e o SQL real da revisão são executados.
            operations = Operations(MigrationContext.configure(sync_connection))

            class IsolatedOperations:
                def execute(self, statement):
                    return operations.execute(statement.replace("davinci.", f"{schema}."))

                def add_column(self, table, column, **kwargs):
                    return operations.add_column(table, column, **{**kwargs, "schema": schema})

                def drop_column(self, table, column, **kwargs):
                    return operations.drop_column(table, column, **{**kwargs, "schema": schema})

            monkeypatch.setattr(migration, "op", IsolatedOperations())
            table = sa.Table(
                "financeiro_suprimentos", sa.MetaData(), sa.Column("id", sa.Integer),
                *[sa.Column(field, sa.Text) for field in (
                    "produto", "modelo", "nome_comercial", "certificado", "numero",
                    "inicio", "fim", "anatel_numero", "inmetro_chave",
                )], sa.Column("valor", sa.Numeric), schema=schema,
            )
            table.create(sync_connection)
            sync_connection.execute(table.insert(), [
                {"id": 1, "produto": "Manual", "modelo": "", "valor": 0,
                 "anatel_numero": None, "inmetro_chave": None},
                {"id": 2, "produto": "Oficial", "modelo": "M1", "valor": 10,
                 "anatel_numero": "123", "inmetro_chave": None},
                {"id": 3, "produto": "Oficial Inmetro", "modelo": "M2", "valor": 20,
                 "anatel_numero": None, "inmetro_chave": "certificado-modelo"},
                {"id": 4, "produto": None, "modelo": None, "valor": None,
                 "anatel_numero": None, "inmetro_chave": None},
            ])
            migration.upgrade()
            upgraded = sa.Table("financeiro_suprimentos", sa.MetaData(),
                                schema=schema, autoload_with=sync_connection)
            rows = sync_connection.execute(sa.select(upgraded).order_by(upgraded.c.id)).mappings()
            assert [row["campos_manuais"] for row in rows] == [
                ["produto", "valor"], None, None, None,
            ]
            migration.downgrade()
            restored = sa.Table("financeiro_suprimentos", sa.MetaData(),
                               schema=schema, autoload_with=sync_connection)
            assert "campos_manuais" not in restored.c
            assert sync_connection.scalar(sa.select(sa.func.count()).select_from(restored)) == 4

        try:
            await connection.run_sync(run)
        finally:
            await connection.execute(sa.schema.DropSchema(schema, cascade=True))
