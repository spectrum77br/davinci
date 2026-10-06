"""A migration 0377 (Conferência Shopee) cria EXATAMENTE o que o model declara.

E o downgrade desfaz.

O conftest monta o schema de teste pelo `create_all` do model, nunca pela
migration. Aqui a 0377 roda de verdade num schema descartável e o catálogo do
Postgres dos dois lados é comparado: as quatro tabelas `conferencia_shopee_*`
(colunas, defaults, CHECK, FK com ON DELETE, PK, UNIQUE, índices). E os seeds:
as 20 lojas do documento (17 ativas, 4 de Mala) e a linha do Informar, os dois
idempotentes.

E o Histórico: execução, coleta e saldo são da máquina (fora do gatilho); a
lista de lojas, editada por pessoas, continua coberta.
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

_MIGRATION = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0377_conferencia_shopee.py"
)
TABELAS = [
    "conferencia_shopee_coleta",
    "conferencia_shopee_conta",
    "conferencia_shopee_execucao",
    "conferencia_shopee_saldo",
]


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0377_conferencia", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db: AsyncSession, schema: str) -> dict[str, list]:
    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "") if s else s

    colunas = (
        await db.execute(
            text(
                "SELECT table_name, column_name, udt_name, is_nullable, column_default,"
                " is_identity, numeric_precision, numeric_scale"
                " FROM information_schema.columns"
                " WHERE table_schema = :schema AND table_name LIKE 'conferencia\\_shopee\\_%'"
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
                 WHERE n.nspname = :schema AND cl.relname LIKE 'conferencia\\_shopee\\_%'
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
                 WHERE schemaname = :schema AND tablename LIKE 'conferencia\\_shopee\\_%'
                 ORDER BY tablename, indexname
                """
            ),
            {"schema": schema},
        )
    ).all()
    return {
        "tabelas": sorted({r[0] for r in colunas}),
        "colunas": [tuple(r[:4]) + (limpo(r[4]),) + tuple(r[5:]) for r in colunas],
        "constraints": [(r[0], r[1], r[2], limpo(r[3])) for r in constraints],
        "indices": [(r[0], r[1], limpo(r[2])) for r in indices],
    }


@pytest.mark.asyncio
async def test_migration_bate_com_o_model_semeia_e_o_downgrade_desfaz(db: AsyncSession):
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0377"
    mod = _carregar()
    assert mod.revision == "0377_conferencia_shopee"
    assert mod.down_revision == "0376_imobilizado"

    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que a 0377 toca: o cadastro do Informar (mesmas colunas do model).
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".threema_informar_config ('
            " id uuid PRIMARY KEY, contexto text NOT NULL UNIQUE,"
            " recipients text NOT NULL DEFAULT '',"
            " created_at timestamptz NOT NULL DEFAULT now(),"
            " updated_at timestamptz NOT NULL DEFAULT now())"
        )
    )
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
        assert len(da_migration["colunas"]) > 35
        nomes = {c[1] for c in da_migration["constraints"]}
        assert {
            "pk_conferencia_shopee_conta",
            "uq_conferencia_shopee_conta_adspower_user_id",
            "ck_conferencia_shopee_conta_grupo",
            "pk_conferencia_shopee_execucao",
            "ck_conferencia_shopee_execucao_tipo",
            "ck_conferencia_shopee_execucao_origem",
            "ck_conferencia_shopee_execucao_status",
            "pk_conferencia_shopee_coleta",
            "ck_conferencia_shopee_coleta_status",
            "fk_conferencia_shopee_coleta_execucao",
            "fk_conferencia_shopee_coleta_conta",
            "pk_conferencia_shopee_saldo",
        } <= nomes
        assert da_migration["colunas"] == do_model["colunas"]
        assert da_migration["constraints"] == do_model["constraints"]
        assert da_migration["indices"] == do_model["indices"]

        defs = {nome: d for _, nome, d in da_migration["indices"]}
        assert "(criado_em DESC)" in defs["ix_conferencia_shopee_execucao_criado_em"]
        assert "(status, disponivel_apos)" in defs[
            "ix_conferencia_shopee_coleta_status_disponivel_apos"
        ]
        assert "(adspower_user_id, lido_em)" in defs[
            "ix_conferencia_shopee_saldo_adspower_user_id_lido_em"
        ]
        fks = {nome: d for _, nome, tipo, d in da_migration["constraints"] if tipo == "f"}
        assert "ON DELETE CASCADE" in fks["fk_conferencia_shopee_coleta_execucao"]
        assert "ON DELETE SET NULL" in fks["fk_conferencia_shopee_coleta_conta"]
        cols = {(c[0], c[1]): c for c in da_migration["colunas"]}
        assert cols[("conferencia_shopee_saldo", "id")][5] == "YES"  # identity
        assert cols[("conferencia_shopee_saldo", "valor")][6:8] == (14, 2)
        assert cols[("conferencia_shopee_execucao", "status")][3:5] == (
            "NO",
            "'coletando'::text",
        )
        assert cols[("conferencia_shopee_coleta", "status")][4] == "'pendente'::text"

        # Os seeds: 20 lojas, 17 ativas, 4 de Mala; o Informar nasce vazio.
        async def contas() -> list[tuple]:
            return (
                await db.execute(
                    text(
                        "SELECT adspower_user_id, nome, grupo, ativo, conta_key"
                        f' FROM "{rascunho}".conferencia_shopee_conta ORDER BY nome'
                    )
                )
            ).all()

        semeadas = await contas()
        assert len(semeadas) == 20
        assert sum(1 for c in semeadas if c[3]) == 17
        mala = sorted(c[1] for c in semeadas if c[2] == "mala")
        assert mala == ["Inova", "KFA", "Minas", "Poofy"]
        assert {c[1] for c in semeadas if not c[3]} == {"VR", "Eron", "Lucas MEI"}
        por_nome = {c[1]: c for c in semeadas}
        assert por_nome["Victor"][4] == "victor mei"
        assert por_nome["Barbosa"][0] == "k1dkeaxv"
        informar = (
            await db.execute(
                text(
                    f'SELECT recipients FROM "{rascunho}".threema_informar_config'
                    " WHERE contexto = 'conferencia_shopee'"
                )
            )
        ).all()
        assert informar == [("",)]

        # Idempotente: rodar o seed de novo não duplica nada.
        conn = await db.connection()
        await conn.run_sync(_rodar, "_semear")
        await db.commit()
        assert len(await contas()) == 20
        n = (
            await db.execute(
                text(f'SELECT count(*) FROM "{rascunho}".threema_informar_config')
            )
        ).scalar_one()
        assert n == 1

        # O CHECK segura valor fora da lista (TEXT, não enum).
        with pytest.raises(Exception, match="ck_conferencia_shopee_conta_grupo"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".conferencia_shopee_conta'
                    " (adspower_user_id, nome, grupo) VALUES ('kx', 'X', 'eletro')"
                )
            )
        await db.rollback()

        # O downgrade tira tudo o que a 0377 pôs.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        depois = await _catalogo(db, rascunho)
        assert depois == {"tabelas": [], "colunas": [], "constraints": [], "indices": []}
        n = (
            await db.execute(
                text(f'SELECT count(*) FROM "{rascunho}".threema_informar_config')
            )
        ).scalar_one()
        assert n == 0
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()


def test_historico_fica_fora_das_tabelas_da_maquina():
    assert hsql.a_cobrir(TABELAS) == ["conferencia_shopee_conta"]
