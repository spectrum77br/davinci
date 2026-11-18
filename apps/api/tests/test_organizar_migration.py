"""A 0500 cria as tabelas do Organizar igual ao model (17/11/2026).

O conftest monta o schema de teste pelo `create_all` do model, nunca pela
migration. Aqui a migration roda de verdade num schema rascunho (com um
`users` mínimo, o alvo das FKs) e o catálogo — colunas, constraints e
índices, inclusive o ÚNICO PARCIAL — tem de sair igual ao do model. Depois:
downgrade (as duas tabelas saem) e upgrade de novo.
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
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0500_organizar_ocultos.py"
)
TABELAS = ("organizar_ocultos", "organizar_previas")


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0500", _MIGRATION)
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
async def test_0500_cria_igual_ao_model_e_desce(db):
    mod = _carregar()
    assert mod.revision == "0500_organizar_ocultos"
    assert mod.down_revision == "0499_pricing_conferencia_margem"
    from app.models.organizar import CHAVES_ORGANIZAR, TABELAS_ORGANIZAR

    assert mod.CHAVES == CHAVES_ORGANIZAR
    assert mod.TABELAS == TABELAS_ORGANIZAR

    modelo = Base.metadata.schema
    rascunho = f"{modelo}_m0500"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
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
        assert any(
            nome == "uq_organizar_ocultos_ativo"
            and "UNIQUE" in definicao
            and "WHERE (reexibido_em IS NULL)" in definicao
            for _t, nome, definicao in da_migration["indices"]
        )

        # uma marca ativa por item; a reexibida libera uma nova; motivo = chave
        usuario = "00000000-0000-0000-0000-0000000000aa"
        item = "00000000-0000-0000-0000-0000000000bb"
        await db.execute(text(f'INSERT INTO "{rascunho}".users (id) VALUES (:u)'), {"u": usuario})
        inserir = text(
            f'INSERT INTO "{rascunho}".organizar_ocultos (tabela, registro_id, motivo, lote_id, '
            "oculto_por) VALUES ('products', :r, :m, gen_random_uuid(), :u)"
        )
        await db.execute(inserir, {"r": item, "m": "produto_inativo", "u": usuario})
        await db.commit()
        with pytest.raises(IntegrityError, match="uq_organizar_ocultos_ativo"):
            await db.execute(inserir, {"r": item, "m": "produto_inativo", "u": usuario})
        await db.rollback()
        with pytest.raises(IntegrityError, match="ck_organizar_ocultos_motivo"):
            await db.execute(inserir, {"r": item, "m": "excluido_bling", "u": usuario})
        await db.rollback()
        await db.execute(text(f'UPDATE "{rascunho}".organizar_ocultos SET reexibido_em = now()'))
        await db.execute(inserir, {"r": item, "m": "produto_inativo", "u": usuario})
        await db.commit()
        # FK SET NULL: o usuário apagado não leva a trilha junto
        await db.execute(text(f'DELETE FROM "{rascunho}".users'))
        await db.commit()
        quem = (
            (await db.execute(text(f'SELECT oculto_por FROM "{rascunho}".organizar_ocultos')))
            .scalars()
            .all()
        )
        assert quem == [None, None]

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert (await _catalogo(db, rascunho))["colunas"] == []

        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        assert await _catalogo(db, rascunho) == do_model
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
