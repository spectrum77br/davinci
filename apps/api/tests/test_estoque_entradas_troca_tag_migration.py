"""A 0510 cria as tabelas do "Incluir" (M-43) e do "Trocar a tag" (M-46) igual ao model.

O conftest monta o schema de teste pelo `create_all` do model, nunca pela migration. Aqui a
migration roda de verdade num schema rascunho e o catálogo — colunas, defaults, constraints e
índices — tem de sair igual ao do model; depois desce (as três tabelas e a sequência saem) e sobe
de novo. Uma ponta só no alembic (a 0510 vem depois da 0509 que está no ar).
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Base

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0510_estoque_entradas_troca_tag.py"
)
TABELAS = ("estoque_entradas", "produto_trocas_tag", "produto_trocas_tag_itens")


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0510", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db, schema: str) -> dict:
    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "") if s else s

    colunas = (
        await db.execute(
            text(
                "SELECT table_name, column_name, udt_name, character_maximum_length, "
                "is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = ANY(:t) "
                "ORDER BY table_name, column_name"
            ),
            {"s": schema, "t": list(TABELAS)},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                "SELECT cl.relname, co.conname, co.contype::text, pg_get_constraintdef(co.oid) "
                "FROM pg_constraint co JOIN pg_class cl ON cl.oid = co.conrelid "
                "JOIN pg_namespace n ON n.oid = cl.relnamespace "
                "WHERE n.nspname = :s AND cl.relname = ANY(:t) ORDER BY cl.relname, co.conname"
            ),
            {"s": schema, "t": list(TABELAS)},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                "SELECT tablename, indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = :s AND tablename = ANY(:t) ORDER BY tablename, indexname"
            ),
            {"s": schema, "t": list(TABELAS)},
        )
    ).all()
    return {
        "colunas": [tuple(r[:5]) + (limpo(r[5]),) for r in colunas],
        "constraints": [(r[0], r[1], r[2], limpo(r[3])) for r in constraints],
        "indices": [(r[0], r[1], limpo(r[2])) for r in indices],
    }


@pytest.mark.asyncio
async def test_0510_cria_igual_ao_model_e_desce(db):
    mod = _carregar()
    assert mod.revision == "0510_estoque_entradas_troca_tag"
    assert mod.down_revision == "0509_mudanca_auto_aprovacao"
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    raiz = Path(__file__).resolve().parent.parent
    cfg = Config(str(raiz / "alembic.ini"))
    cfg.set_main_option("script_location", str(raiz / "alembic"))
    roteiro = ScriptDirectory.from_config(cfg)
    assert roteiro.get_heads() == ["0510_estoque_entradas_troca_tag"]

    modelo = Base.metadata.schema
    rascunho = f"{modelo}_m0510"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # As FKs apontam para users: basta a chave.
    await db.execute(text(f'CREATE TABLE "{rascunho}".users (id uuid PRIMARY KEY)'))
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        da_migration = await _catalogo(db, rascunho)
        do_model = await _catalogo(db, modelo)
        assert da_migration["colunas"], "a migration não criou as tabelas"
        assert da_migration == do_model

        # numero vem da sequência; quantidade e status têm CHECK; idem_key é única.
        s = rascunho
        numeros = [
            (
                await db.execute(
                    text(
                        f'INSERT INTO "{s}".estoque_entradas (id, idem_key, sku, bling_product_id, '
                        "quantidade) VALUES (gen_random_uuid(), :k, 'dg019.ra', 1, 1) "
                        "RETURNING numero"
                    ),
                    {"k": k},
                )
            ).scalar_one()
            for k in ("chave-a", "chave-b")
        ]
        assert numeros[1] == numeros[0] + 1
        await db.commit()
        for sql, nome in (
            (
                f'INSERT INTO "{s}".estoque_entradas (id, idem_key, sku, bling_product_id, '
                "quantidade) VALUES (gen_random_uuid(), 'chave-c', 'x', 1, 0)",
                "ck_estoque_entradas_quantidade",
            ),
            (
                f'INSERT INTO "{s}".estoque_entradas (id, idem_key, sku, bling_product_id, '
                "quantidade, status) VALUES (gen_random_uuid(), 'chave-d', 'x', 1, 1, 'xx')",
                "ck_estoque_entradas_status",
            ),
            (
                f'INSERT INTO "{s}".estoque_entradas (id, idem_key, sku, bling_product_id, '
                "quantidade) VALUES (gen_random_uuid(), 'chave-a', 'x', 1, 1)",
                "uq_estoque_entradas_idem_key",
            ),
        ):
            with pytest.raises(IntegrityError, match=nome):
                await db.execute(text(sql))
            await db.rollback()

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert (await _catalogo(db, rascunho))["colunas"] == []
        sobrou = (
            await db.execute(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relkind = 'S'"
                ),
                {"s": rascunho},
            )
        ).scalar_one()
        assert sobrou == 0

        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        assert await _catalogo(db, rascunho) == do_model
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
