"""A migration 0384 (conta de Shopee Vídeo → loja) cria EXATAMENTE o que o model declara.

O conftest monta o schema de teste pelo `create_all` do model, nunca pela
migration. Aqui a 0384 roda de verdade num schema descartável (só com o que
ela toca: `integrations(id)` e `redes_sociais(id)`) e o catálogo do Postgres
dos dois lados é comparado — a coluna, a FK com ON DELETE SET NULL e o único
PARCIAL (uma conta de Shopee Vídeo por loja). E o downgrade desfaz tudo.

A coluna é nulável e o código antigo não a conhece: é isso que permite rodar
o `alembic upgrade head` ANTES de trocar os containers no deploy.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Base

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0384_redes_sociais_loja_shopee.py"
)


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0384_loja_shopee", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db: AsyncSession, schema: str) -> dict[str, list]:
    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "").replace(f'"{schema}".', "") if s else s

    coluna = (
        await db.execute(
            text(
                "SELECT column_name, udt_name, is_nullable, column_default"
                " FROM information_schema.columns"
                " WHERE table_schema = :schema AND table_name = 'redes_sociais'"
                " AND column_name = 'integration_id'"
            ),
            {"schema": schema},
        )
    ).all()
    fks = (
        await db.execute(
            text(
                """
                SELECT co.conname, pg_get_constraintdef(co.oid)
                  FROM pg_constraint co
                  JOIN pg_class cl ON cl.oid = co.conrelid
                  JOIN pg_namespace n ON n.oid = cl.relnamespace
                 WHERE n.nspname = :schema AND cl.relname = 'redes_sociais'
                   AND co.contype = 'f' AND co.conname LIKE '%integration%'
                """
            ),
            {"schema": schema},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes"
                " WHERE schemaname = :schema AND tablename = 'redes_sociais'"
                " AND indexdef LIKE '%integration_id%'"
            ),
            {"schema": schema},
        )
    ).all()
    return {
        "coluna": [tuple(r) for r in coluna],
        "fks": [(r[0], limpo(r[1])) for r in fks],
        "indices": [(r[0], limpo(r[1])) for r in indices],
    }


@pytest.mark.asyncio
async def test_migration_0384_bate_com_o_model_e_o_downgrade_desfaz(db: AsyncSession):
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0384"
    mod = _carregar()
    assert mod.revision == "0384_redes_sociais_loja_shopee"
    # Nasceu 0383 e foi renumerada em 08/10: o origin chegou primeiro com a
    # 0383 (trocas de produto do atendimento).
    assert mod.down_revision == "0383_atendimento_trocas"

    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que a 0384 toca.
    await db.execute(text(f'CREATE TABLE "{rascunho}".integrations (id uuid PRIMARY KEY)'))
    await db.execute(text(f'CREATE TABLE "{rascunho}".redes_sociais (id uuid PRIMARY KEY)'))
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()

        da_migration = await _catalogo(db, rascunho)
        do_model = await _catalogo(db, schema_model)
        assert da_migration["coluna"] == [("integration_id", "uuid", "YES", None)]
        assert da_migration == do_model
        (fk,) = da_migration["fks"]
        assert fk[0] == "fk_redes_sociais_integration_id_integrations"
        assert "REFERENCES integrations(id) ON DELETE SET NULL" in fk[1]
        (idx,) = da_migration["indices"]
        assert idx[0] == "uq_redes_sociais_integration_id"
        assert "UNIQUE" in idx[1] and "WHERE (integration_id IS NOT NULL)" in idx[1]

        # Uma conta por loja — e várias contas SEM loja (as outras redes).
        loja = uuid.uuid4()
        await db.execute(text(f"INSERT INTO \"{rascunho}\".integrations (id) VALUES ('{loja}')"))
        for _ in range(2):
            await db.execute(
                text(f"INSERT INTO \"{rascunho}\".redes_sociais (id) VALUES ('{uuid.uuid4()}')")
            )
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".redes_sociais (id, integration_id)'
                f" VALUES ('{uuid.uuid4()}', '{loja}')"
            )
        )
        await db.commit()
        with pytest.raises(Exception, match="uq_redes_sociais_integration_id"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".redes_sociais (id, integration_id)'
                    f" VALUES ('{uuid.uuid4()}', '{loja}')"
                )
            )
        await db.rollback()
        # Apagar a loja solta a conta (SET NULL), não a apaga.
        await db.execute(text(f"DELETE FROM \"{rascunho}\".integrations WHERE id = '{loja}'"))
        await db.commit()
        soltas = (
            await db.execute(
                text(
                    f'SELECT count(*) FROM "{rascunho}".redes_sociais WHERE integration_id IS NULL'
                )
            )
        ).scalar_one()
        assert soltas == 3

        # O downgrade tira tudo o que a 0384 pôs.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert await _catalogo(db, rascunho) == {"coluna": [], "fks": [], "indices": []}
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
