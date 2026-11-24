"""A 0509 (24/11/2026) cria a aprovação automática igual ao model e já com o Harry.

O conftest monta o schema de teste pelo `create_all` do model; aqui a migration roda de
verdade num schema rascunho (com as tabelas de antes copiadas do model) e o catálogo da
tabela nova tem de sair igual ao do model. Confere a regra inicial (harry potter, autorizado
por heisenberg — só se heisenberg for aprovador), o downgrade e o upgrade de novo.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.models import Base

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0509_mudanca_auto_aprovacao.py"
)
NOVA = "sistema_mudanca_auto_aprovacao"


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0509", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db, schema: str) -> dict:
    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "") if s else s

    colunas = (
        await db.execute(
            text(
                "SELECT table_name, column_name, udt_name, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_schema = :s AND ("
                "table_name = :t OR (table_name = 'sistema_mudanca' "
                "AND column_name = 'aprovado_automatico')) ORDER BY table_name, column_name"
            ),
            {"s": schema, "t": NOVA},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                "SELECT co.conname, co.contype::text, pg_get_constraintdef(co.oid) "
                "FROM pg_constraint co JOIN pg_class cl ON cl.oid = co.conrelid "
                "JOIN pg_namespace n ON n.oid = cl.relnamespace "
                "WHERE n.nspname = :s AND cl.relname = :t ORDER BY co.conname"
            ),
            {"s": schema, "t": NOVA},
        )
    ).all()
    return {
        "colunas": [tuple(r[:4]) + (limpo(r[4]),) for r in colunas],
        "constraints": [(r[0], r[1], limpo(r[2])) for r in constraints],
    }


@pytest.mark.asyncio
async def test_0509_cria_igual_ao_model_com_o_harry_e_desce(db):
    mod = _carregar()
    assert mod.revision == "0509_mudanca_auto_aprovacao"
    assert mod.down_revision == "0508_produto_cadastros"
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    raiz = Path(__file__).resolve().parent.parent
    cfg = Config(str(raiz / "alembic.ini"))
    cfg.set_main_option("script_location", str(raiz / "alembic"))
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1

    modelo = Base.metadata.schema
    rascunho = f"{modelo}_m0509"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que a 0509 toca, como na produção antes dela (role/status são enum lá; aqui texto).
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".users (id uuid PRIMARY KEY, name text, '
            "role text NOT NULL, status text NOT NULL)"
        )
    )
    await db.execute(text(f'CREATE TABLE "{rascunho}".sistema_mudanca (id uuid PRIMARY KEY)'))
    await db.execute(
        text(f'CREATE TABLE "{rascunho}".sistema_mudanca_aprovador (user_id uuid PRIMARY KEY)')
    )
    ids = {
        "harry": "00000000-0000-0000-0000-000000000001",
        "heis": "00000000-0000-0000-0000-000000000002",
        "thor": "00000000-0000-0000-0000-000000000003",
        "harry_velho": "00000000-0000-0000-0000-000000000004",
    }
    await db.execute(
        text(
            f'INSERT INTO "{rascunho}".users VALUES '
            f"('{ids['harry']}', 'Harry Potter', 'admin', 'active'), "
            f"('{ids['heis']}', 'heisenberg', 'admin', 'active'), "
            f"('{ids['thor']}', 'thorfinn', 'admin', 'active'), "
            f"('{ids['harry_velho']}', 'harry potter', 'admin', 'disabled')"
        )
    )
    await db.execute(
        text(
            f'INSERT INTO "{rascunho}".sistema_mudanca_aprovador VALUES '
            f"('{ids['heis']}'), ('{ids['thor']}')"
        )
    )
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
        assert da_migration["colunas"], "a migration não criou a tabela"
        assert da_migration == await _catalogo(db, modelo)

        regras = (
            await db.execute(
                text(f'SELECT pedido_por::text, autorizado_por::text FROM "{rascunho}".{NOVA}')
            )
        ).all()
        assert regras == [(ids["harry"], ids["heis"])]  # o desativado de mesmo nome fica fora

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert (await _catalogo(db, rascunho))["colunas"] == []

        # sem heisenberg aprovador, a regra não nasce
        await db.execute(text(f'DELETE FROM "{rascunho}".sistema_mudanca_aprovador'))
        await db.commit()
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        n = (await db.execute(text(f'SELECT count(*) FROM "{rascunho}".{NOVA}'))).scalar_one()
        assert n == 0
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
