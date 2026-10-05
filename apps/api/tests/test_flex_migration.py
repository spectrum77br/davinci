"""A migration 0364 (Flex) cria EXATAMENTE o que o model declara — e o downgrade desfaz.

O conftest monta o schema de teste pelo `create_all` do model, nunca pela
migration. Aqui a 0364 roda de verdade num schema descartável e o catálogo
do Postgres dos dois lados é comparado: as cinco tabelas `flex_*` (colunas,
CHECK, FK com ON DELETE, PK, índices) e as duas colunas novas da
`logistica` com o índice parcial da aba Flex.

E o Histórico: as cinco tabelas são da máquina (fora do gatilho); a
`logistica` continua coberta.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.historico import sql as hsql
from app.models import Base

_MIGRATION = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0367_flex.py"
TABELAS = ["flex_anuncio_estado", "flex_conta", "flex_emergencia", "flex_log", "flex_pedido"]


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0367_flex", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db: AsyncSession, schema: str) -> dict[str, list]:
    """Colunas, constraints e índices das tabelas flex_* e do que a 0364 põe
    na `logistica`."""

    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "") if s else s

    filtro_col = (
        "(table_name LIKE 'flex\\_%' OR (table_name = 'logistica'"
        " AND column_name IN ('envio_tipo', 'envio_flex')))"
    )
    colunas = (
        await db.execute(
            text(
                "SELECT table_name, column_name, udt_name, is_nullable, column_default,"
                " is_identity FROM information_schema.columns"
                f" WHERE table_schema = :schema AND {filtro_col}"
                " ORDER BY table_name, column_name"
            ),
            {"schema": schema},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                """
                SELECT cl.relname, co.conname, co.contype::text, pg_get_constraintdef(co.oid)
                  FROM pg_constraint co
                  JOIN pg_class cl ON cl.oid = co.conrelid
                  JOIN pg_namespace n ON n.oid = cl.relnamespace
                 WHERE n.nspname = :schema AND cl.relname LIKE 'flex\\_%'
                 ORDER BY cl.relname, co.conname
                """
            ),
            {"schema": schema},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                """
                SELECT tablename, indexname, indexdef
                  FROM pg_indexes
                 WHERE schemaname = :schema
                   AND (tablename LIKE 'flex\\_%' OR indexname = 'ix_logistica_envio_flex')
                 ORDER BY tablename, indexname
                """
            ),
            {"schema": schema},
        )
    ).all()
    return {
        "tabelas": sorted({r[0] for r in colunas if r[0] != "logistica"}),
        "colunas": [tuple(r[:4]) + (limpo(r[4]), r[5]) for r in colunas],
        "constraints": [(r[0], r[1], r[2], limpo(r[3])) for r in constraints],
        "indices": [(r[0], r[1], limpo(r[2])) for r in indices],
    }


@pytest.mark.asyncio
async def test_migration_bate_com_o_model_e_o_downgrade_desfaz(db: AsyncSession):
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0364"
    mod = _carregar()
    assert mod.revision == "0367_flex"
    assert mod.down_revision == "0366_atendimento_automacoes"

    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que a 0364 toca/referencia.
    await db.execute(text(f'CREATE TABLE "{rascunho}".users (id uuid PRIMARY KEY)'))
    await db.execute(text(f'CREATE TABLE "{rascunho}".integrations (id uuid PRIMARY KEY)'))
    await db.execute(text(f'CREATE TABLE "{rascunho}".logistica (id uuid PRIMARY KEY)'))
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
        # Comparação que não compara nada passaria calada: o catálogo tem corpo.
        assert len(da_migration["colunas"]) > 30
        nomes = {c[1] for c in da_migration["constraints"]}
        assert {
            "pk_flex_pedido",
            "pk_flex_anuncio_estado",
            "pk_flex_log",
            "ck_flex_pedido_plataforma",
            "ck_flex_anuncio_estado_desejado",
            "ck_flex_anuncio_estado_observado",
            "ck_flex_log_acao",
            "ck_flex_log_modo",
            "ck_flex_log_resultado",
            "fk_flex_pedido_integration_id_integrations",
            "fk_flex_anuncio_estado_integration_id_integrations",
            "fk_flex_anuncio_estado_aprovado_por_users",
            "ck_flex_anuncio_estado_status_anuncio",
            "pk_flex_conta",
            "fk_flex_conta_integration_id_integrations",
            "pk_flex_emergencia",
            "ck_flex_emergencia_status",
        } <= nomes
        assert da_migration["colunas"] == do_model["colunas"]
        assert da_migration["constraints"] == do_model["constraints"]
        assert da_migration["indices"] == do_model["indices"]

        defs = {nome: d for _, nome, d in da_migration["indices"]}
        assert "WHERE (envio_flex IS TRUE)" in defs["ix_logistica_envio_flex"]
        assert "WHERE aguardando_aprovacao" in defs["ix_flex_anuncio_estado_aguardando"]
        assert "(integration_id, external_id, criado_em)" in defs["ix_flex_log_anuncio"]
        fks = {nome: d for _, nome, tipo, d in da_migration["constraints"] if tipo == "f"}
        assert "ON DELETE SET NULL" in fks["fk_flex_pedido_integration_id_integrations"]
        assert "ON DELETE CASCADE" in fks["fk_flex_anuncio_estado_integration_id_integrations"]
        assert "ON DELETE SET NULL" in fks["fk_flex_anuncio_estado_aprovado_por_users"]

        # As colunas da Logística nascem vazias e sem default (não reescreve).
        cols = {(c[0], c[1]): c for c in da_migration["colunas"]}
        assert cols[("logistica", "envio_flex")][3:5] == ("YES", None)
        assert cols[("logistica", "envio_tipo")][3:5] == ("YES", None)
        assert cols[("flex_pedido", "no_sp")][3:5] == ("NO", "false")
        assert cols[("flex_log", "id")][5] == "YES"  # identity

        # O CHECK segura valor fora da lista (TEXT, não enum).
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".flex_log (acao, modo, resultado)'
                " VALUES ('desligar', 'observar', 'simulado')"
            )
        )
        await db.commit()
        with pytest.raises(Exception, match="ck_flex_log_modo"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".flex_log (acao, modo, resultado)'
                    " VALUES ('desligar', 'turbo', 'simulado')"
                )
            )
        await db.rollback()

        # O downgrade tira tudo o que a 0364 pôs.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        depois = await _catalogo(db, rascunho)
        assert depois == {"tabelas": [], "colunas": [], "constraints": [], "indices": []}
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()


def test_historico_fica_fora_das_tabelas_do_flex():
    nomes = [*TABELAS, "logistica"]
    assert hsql.a_cobrir(nomes) == ["logistica"]


def test_alembic_tem_uma_ponta_so():
    """`alembic upgrade head` (scripts/migrate.sh) para com "Multiple head
    revisions" se duas migrations apontam para o mesmo pai — o git junta os
    arquivos sem conflito (nomes diferentes) e ninguém percebe até o deploy.
    A 0364 nasceu 0361 em cima da 0360 enquanto o main ganhava a 0361/0362/
    0363; aqui a árvore inteira tem de terminar numa ponta só."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    raiz = Path(__file__).resolve().parent.parent
    cfg = Config(str(raiz / "alembic.ini"))
    cfg.set_main_option("script_location", str(raiz / "alembic"))
    pontas = ScriptDirectory.from_config(cfg).get_heads()
    # Uma ponta só (não necessariamente a 0364: o main vai ganhar outras).
    assert len(pontas) == 1, pontas
