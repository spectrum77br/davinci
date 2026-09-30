"""As migrations 0346+0347 criam EXATAMENTE o que o model declara — e o downgrade desfaz.

O conftest monta o schema de teste pelo `create_all` do model, nunca pela
migration. Sem este teste, uma coluna esquecida na 0346 (ou um índice parcial
com outro predicado) só apareceria no deploy, em produção. Aqui a migration
roda de verdade num schema descartável e o catálogo do Postgres dos dois
lados é comparado: colunas (tipo, tamanho, nulo, default), constraints (PK,
FK com ON DELETE, UNIQUE) e índices (inclusive os parciais).

A 0347 (30/09/2026, lojas do robô do Mac mini: Temu e AliExpress) mexe nas
mesmas tabelas: roda em cima da 0346, e o catálogo que se compara com o model
é o das duas juntas.
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

from app.models import Base

_VERSOES = Path(__file__).resolve().parent.parent / "alembic" / "versions"
_MIGRATION = _VERSOES / "0346_atendimento.py"
_MIGRATION_ROBO = _VERSOES / "0347_atendimento_robo.py"
TABELAS = sorted(t.name for t in Base.metadata.sorted_tables if t.name.startswith("atendimento_"))


def _carregar_migration(caminho: Path = _MIGRATION):
    spec = importlib.util.spec_from_file_location(f"migration_{caminho.stem}", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db: AsyncSession, schema: str, *remover: str) -> dict[str, list]:
    """Colunas, constraints e índices das tabelas atendimento_* de um schema."""

    def limpo(s: str | None) -> str | None:
        for prefixo in remover:
            s = s.replace(f"{prefixo}.", "") if s else s
        return s

    colunas = (
        await db.execute(
            text(
                """
                SELECT table_name, column_name, udt_name, character_maximum_length,
                       is_nullable, column_default
                  FROM information_schema.columns
                 WHERE table_schema = :schema AND table_name LIKE 'atendimento\\_%'
                 ORDER BY table_name, column_name
                """
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
                 WHERE n.nspname = :schema AND cl.relname LIKE 'atendimento\\_%'
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
                 WHERE schemaname = :schema AND tablename LIKE 'atendimento\\_%'
                 ORDER BY tablename, indexname
                """
            ),
            {"schema": schema},
        )
    ).all()
    return {
        "tabelas": sorted({r[0] for r in colunas}),
        "colunas": [tuple(r[:5]) + (limpo(r[5]),) for r in colunas],
        "constraints": [(r[0], r[1], r[2], limpo(r[3])) for r in constraints],
        "indices": [(r[0], r[1], limpo(r[2])) for r in indices],
    }


@pytest.mark.asyncio
async def test_migration_bate_com_o_model_e_o_downgrade_desfaz(db: AsyncSession):
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0346"
    mod = _carregar_migration()
    assert mod.revision == "0346_atendimento"
    assert mod.down_revision == "0345_denuncia_acesso_cairo"
    robo = _carregar_migration(_MIGRATION_ROBO)
    assert robo.revision == "0347_atendimento_robo"
    assert robo.down_revision == "0346_atendimento"
    # 7 da primeira parte + 3 da parte 2 (categorias e os índices do cartão
    # "Cliente").
    assert len(TABELAS) == 10
    assert {
        "atendimento_categorias",
        "atendimento_pedidos_comprador",
        "atendimento_avaliacoes_loja",
    } <= set(TABELAS)

    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que as FKs da 0346 referenciam.
    await db.execute(text(f'CREATE TABLE "{rascunho}".users (id uuid PRIMARY KEY)'))
    await db.execute(text(f'CREATE TABLE "{rascunho}".integrations (id uuid PRIMARY KEY)'))
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        # Na ordem de subida; a descida desfaz ao contrário (0347 antes da 0346).
        ordem = (mod, robo) if passo == "upgrade" else (robo, mod)
        with Operations.context(ctx):
            for m in ordem:
                getattr(m, passo)()

    mod.SCHEMA = rascunho
    robo.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()

        da_migration = await _catalogo(db, rascunho, rascunho)
        do_model = await _catalogo(db, schema_model, schema_model)
        assert da_migration["tabelas"] == TABELAS
        # Comparação que não compara nada passaria calada: o catálogo tem corpo.
        assert len(da_migration["colunas"]) > 80
        nomes = {c[1] for c in da_migration["constraints"]}
        assert {
            "pk_atendimento_canais",
            "uq_atendimento_canais_integration_id_canal",
            "uq_atendimento_conversas_integration_id_canal_externo_id",
            "uq_atendimento_mensagens_conversa_id_externo_id",
            "uq_atendimento_avaliacoes_rascunho_id",
            "fk_atendimento_rascunhos_gatilho_atendimento_mensagens",
            "fk_atendimento_conversas_canal_id_atendimento_canais",
            "pk_atendimento_categorias",
            "uq_atendimento_pedidos_comprador_integration_id_pedido",
            "fk_atendimento_pedidos_comprador_integration_id_integrations",
            "uq_atendimento_avaliacoes_loja_integration_id_comentario_id",
            "fk_atendimento_avaliacoes_loja_integration_id_integrations",
            # 0347: o canal do robô (perfil do AdsPower, sem integração).
            "uq_atendimento_canais_robo_perfil_id",
            "ck_atendimento_canais_integracao_ou_robo",
        } <= nomes
        assert da_migration["colunas"] == do_model["colunas"]
        assert da_migration["constraints"] == do_model["constraints"]
        assert da_migration["indices"] == do_model["indices"]
        # Os dois parciais, com o predicado certo.
        defs = {nome: d for _, nome, d in da_migration["indices"]}
        assert "WHERE ((status)::text = 'enviando'::text)" in defs["uq_atendimento_envio_em_voo"]
        assert (
            "WHERE ((status)::text = 'pendente'::text)"
            in defs["uq_atendimento_rascunho_pendente"]
        )
        # 0347: a conversa do robô é única por (canal, id externo), só sem
        # integração e com canal (a Amazon sem conta fica de fora).
        assert "(canal_id, externo_id)" in defs["uq_atendimento_conversas_robo"]
        assert (
            "WHERE ((integration_id IS NULL) AND (canal_id IS NOT NULL))"
            in defs["uq_atendimento_conversas_robo"]
        )
        # Os índices compostos do cartão "Cliente", na ordem das colunas.
        assert "(integration_id, comprador_id)" in defs[
            "ix_atendimento_pedidos_comprador_integration_id_comprador_id"
        ]
        assert "(integration_id, pedido)" in defs[
            "ix_atendimento_avaliacoes_loja_integration_id_pedido"
        ]
        # As colunas novas da regra (P7), com o default que deixa o manual
        # antigo como estava: tipo `categoria` sem categoria = geral.
        cols = {(c[0], c[1]): c for c in da_migration["colunas"]}
        assert cols[("atendimento_canais", "integration_id")][4] == "YES"
        assert cols[("atendimento_canais", "robo_perfil_id")][4] == "YES"
        assert cols[("atendimento_regras", "tipo")][5] == "'categoria'::character varying"
        assert cols[("atendimento_regras", "prioridade")][5] == "100"
        assert cols[("atendimento_regras", "categoria")][4] == "YES"
        assert cols[("atendimento_modelos", "categoria")][4] == "YES"

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        depois = await _catalogo(db, rascunho, rascunho)
        assert depois["tabelas"] == []
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
