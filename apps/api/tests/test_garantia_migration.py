"""A migration 0380 (Painel de Garantia Uranyx) cria EXATAMENTE o que o model declara.

O conftest monta o schema de teste pelo `create_all` do model, nunca pela
migration. Aqui a 0380 roda de verdade num schema descartável e o catálogo do
Postgres dos dois lados é comparado (colunas, defaults, CHECK, FK com ON
DELETE, PK, UNIQUE, índices e os gatilhos "só inserção"). E o downgrade
desfaz tudo, inclusive a função do gatilho.
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

_MIGRATION = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0380_garantias.py"
TABELAS = ["garantia_atendimento_anexos", "garantia_atendimentos", "garantia_log", "garantias"]
_FILTRO = "('garantias', 'garantia_atendimentos', 'garantia_atendimento_anexos', 'garantia_log')"


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0380_garantias", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db: AsyncSession, schema: str) -> dict[str, list]:
    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "").replace(f'"{schema}".', "") if s else s

    colunas = (
        await db.execute(
            text(
                "SELECT table_name, column_name, udt_name, is_nullable, column_default,"
                " character_maximum_length"
                " FROM information_schema.columns"
                f" WHERE table_schema = :schema AND table_name IN {_FILTRO}"
                " ORDER BY table_name, column_name"
            ),
            {"schema": schema},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                f"""
                SELECT cl.relname, co.conname, co.contype::text, pg_get_constraintdef(co.oid)
                  FROM pg_constraint co
                  JOIN pg_class cl ON cl.oid = co.conrelid
                  JOIN pg_namespace n ON n.oid = cl.relnamespace
                 WHERE n.nspname = :schema AND cl.relname IN {_FILTRO}
                 ORDER BY cl.relname, co.conname
                """
            ),
            {"schema": schema},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                f"""
                SELECT tablename, indexname, indexdef
                  FROM pg_indexes
                 WHERE schemaname = :schema AND tablename IN {_FILTRO}
                 ORDER BY tablename, indexname
                """
            ),
            {"schema": schema},
        )
    ).all()
    gatilhos = (
        await db.execute(
            text(
                f"""
                SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid)
                  FROM pg_trigger t
                  JOIN pg_class c ON c.oid = t.tgrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = :schema AND c.relname IN {_FILTRO} AND NOT t.tgisinternal
                 ORDER BY c.relname, t.tgname
                """
            ),
            {"schema": schema},
        )
    ).all()
    funcoes = (
        await db.execute(
            text(
                "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                " WHERE n.nspname = :schema AND p.proname = 'garantia_so_insercao'"
            ),
            {"schema": schema},
        )
    ).all()
    return {
        "tabelas": sorted({r[0] for r in colunas}),
        "colunas": [tuple(r[:4]) + (limpo(r[4]),) + tuple(r[5:]) for r in colunas],
        "constraints": [(r[0], r[1], r[2], limpo(r[3])) for r in constraints],
        "indices": [(r[0], r[1], limpo(r[2])) for r in indices],
        "gatilhos": [(r[0], r[1], limpo(r[2])) for r in gatilhos],
        "funcoes": [r[0] for r in funcoes],
    }


@pytest.mark.asyncio
async def test_migration_bate_com_o_model_e_o_downgrade_desfaz(db: AsyncSession):
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0380"
    mod = _carregar()
    assert mod.revision == "0380_garantias"
    assert mod.down_revision == "0379_pricing_carrefour_netshoes"

    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que a 0380 referencia: users(id).
    await db.execute(text(f'CREATE TABLE "{rascunho}".users (id uuid PRIMARY KEY)'))
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
        assert da_migration["tabelas"] == TABELAS
        assert len(da_migration["colunas"]) > 50
        assert da_migration["colunas"] == do_model["colunas"]
        assert da_migration["constraints"] == do_model["constraints"]
        assert da_migration["indices"] == do_model["indices"]
        assert da_migration["gatilhos"] == do_model["gatilhos"]
        assert da_migration["funcoes"] == ["garantia_so_insercao"]
        nomes = {c[1] for c in da_migration["constraints"]}
        assert {
            "pk_garantias",
            "uq_garantias_cpf_nf",
            "ck_garantias_cpf_digitos",
            "ck_garantias_prazos_completos",
            "fk_garantias_criado_por_users",
            "fk_garantia_atendimentos_garantia_id_garantias",
            "ck_garantia_atendimentos_cobertura_valida",
            "ck_garantia_log_acao_valida",
        } <= nomes
        # O nome da FK do anexo passa de 63 caracteres (o SQLAlchemy encurta):
        # confere pelo que ela faz.
        fks = [d for t, _, tipo, d in da_migration["constraints"] if tipo == "f"]
        assert "REFERENCES garantia_atendimentos(id) ON DELETE RESTRICT" in " | ".join(fks)
        assert "REFERENCES users(id) ON DELETE RESTRICT" in " | ".join(fks)
        gatilhos = {(t, g) for t, g, _ in da_migration["gatilhos"]}
        for tabela in ("garantia_atendimentos", "garantia_atendimento_anexos", "garantia_log"):
            assert (tabela, f"{tabela}_so_insercao") in gatilhos
            assert (tabela, f"{tabela}_sem_truncate") in gatilhos
        assert not any(t == "garantias" for t, _ in gatilhos)  # o cadastro se corrige

        # O gatilho do rascunho recusa UPDATE/DELETE de verdade.
        uid = uuid.uuid4()
        await db.execute(text(f"INSERT INTO \"{rascunho}\".users (id) VALUES ('{uid}')"))
        gid = (
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".garantias'
                    " (pedido_bling, nf_numero, cliente_nome, cpf, criado_por)"
                    f" VALUES ('1', '10', 'Fulano', '52998224725', '{uid}') RETURNING id"
                )
            )
        ).scalar_one()
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".garantia_log (garantia_id, acao, user_nome)'
                f" VALUES ({gid}, 'cadastrou', 'x')"
            )
        )
        await db.commit()
        with pytest.raises(Exception, match="garantia_so_insercao"):
            await db.execute(text(f"UPDATE \"{rascunho}\".garantia_log SET detalhe = 'y'"))
        await db.rollback()
        # CPF fora do formato e prazo pela metade: o banco recusa.
        with pytest.raises(Exception, match="ck_garantias_cpf_digitos"):
            await db.execute(text(f"UPDATE \"{rascunho}\".garantias SET cpf = '123'"))
        await db.rollback()
        with pytest.raises(Exception, match="ck_garantias_prazos_completos"):
            await db.execute(
                text(f"UPDATE \"{rascunho}\".garantias SET data_inicio = '2026-10-07'")
            )
        await db.rollback()
        await db.execute(text("SELECT set_config('davinci.garantia_expurgo', 'sim', true)"))
        await db.execute(text(f'DELETE FROM "{rascunho}".garantia_log'))
        await db.execute(text(f'DELETE FROM "{rascunho}".garantias'))
        await db.commit()

        # O downgrade tira tudo o que a 0380 pôs.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        depois = await _catalogo(db, rascunho)
        assert depois == {
            "tabelas": [],
            "colunas": [],
            "constraints": [],
            "indices": [],
            "gatilhos": [],
            "funcoes": [],
        }
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
