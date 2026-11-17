"""A migration 0494 (Novos procedimentos 3/6, 17/11/2026) cria EXATAMENTE o que o model declara.

O conftest monta o schema pelo `create_all` do model, nunca pela migration: sem
este teste, uma coluna com outro tamanho/default (ou a FK com outro nome) só
apareceria no deploy. Aqui a 0494 roda de verdade num schema descartável, em
cima da `refunds` como ela está em produção (0493), e as colunas novas e a FK
são comparadas com as do model; o downgrade volta a tabela ao que era, com a
linha antiga intacta.
"""

# Identificadores SQL locais, criados por uuid4.
# ruff: noqa: S608

import importlib.util
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.conftest import TEST_SCHEMA

NOVAS = (
    "motivo",
    "motivo_detalhe",
    "forma_pagamento",
    "dados_pagamento",
    "requer_aprovacao",
    "aprovado_por",
    "aprovado_em",
    "origem",
    "conversa_id",
)
FK = "fk_refunds_aprovado_por_users"


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0494_reembolso_cliente_atendimento.py"
    spec = importlib.util.spec_from_file_location("reembolso_cliente_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0494_esta_na_arvore_e_ha_uma_ponta_so():
    """17/11/2026: a 0493 desta frente colidia com a 0493_sistema_mudancas da main
    (o `alembic upgrade head` do deploy parava em "Multiple head revisions")."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    api = Path(__file__).parents[1]
    cfg = Config(str(api / "alembic.ini"))
    cfg.set_main_option("script_location", str(api / "alembic"))
    scripts = ScriptDirectory.from_config(cfg)
    # Uma ponta só (não necessariamente a 0494: a main vai ganhar outras).
    assert len(scripts.get_heads()) == 1, scripts.get_heads()
    assert scripts.get_revision("0494_reembolso_cliente_atendimento").down_revision == (
        "0493_sistema_mudancas"
    )


async def _colunas(conn, schema: str) -> dict[str, tuple]:
    linhas = (
        await conn.execute(
            text(
                "SELECT column_name, udt_name, character_maximum_length, is_nullable, "
                "column_default FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = 'refunds'"
            ),
            {"s": schema},
        )
    ).all()
    return {r[0]: tuple(r[1:]) for r in linhas}


async def _fks(conn, schema: str) -> dict[str, str]:
    linhas = (
        await conn.execute(
            text(
                "SELECT co.conname, pg_get_constraintdef(co.oid) FROM pg_constraint co "
                "JOIN pg_class cl ON cl.oid = co.conrelid "
                "JOIN pg_namespace n ON n.oid = cl.relnamespace "
                "WHERE n.nspname = :s AND cl.relname = 'refunds' AND co.contype = 'f'"
            ),
            {"s": schema},
        )
    ).all()
    return {nome: definicao.replace(f"{schema}.", "") for nome, definicao in linhas}


async def test_0494_cria_o_que_o_model_declara_e_o_downgrade_desfaz(db, monkeypatch):
    migration = _migration()
    # 17/11/2026: depois da 0493_sistema_mudancas da main (uma ponta só).
    assert migration.revision == "0494_reembolso_cliente_atendimento"
    assert migration.down_revision == "0493_sistema_mudancas"
    schema = "reembolso_0494_" + uuid4().hex[:12]
    monkeypatch.setattr(migration, "SCHEMA", schema)

    def migrar(connection, sentido):
        migration.op = Operations(MigrationContext.configure(connection))
        getattr(migration, sentido)()

    async with db.bind.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        try:
            # A `refunds` de produção antes da 0494 (as colunas da 0065…0160).
            await conn.execute(text(f'CREATE TABLE "{schema}".users (id uuid PRIMARY KEY)'))
            await conn.execute(
                text(
                    f"""
                    CREATE TABLE "{schema}".refunds (
                        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                        data timestamptz,
                        pedido_bling text,
                        pedido_marketplace text,
                        plataforma text,
                        conta text NOT NULL,
                        tipo text,
                        prejuizo double precision,
                        reembolso double precision,
                        chamado text,
                        operacao text,
                        conferido boolean NOT NULL DEFAULT false,
                        observacao text,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        created_by uuid CONSTRAINT fk_refunds_created_by_users
                            REFERENCES "{schema}".users(id) ON DELETE SET NULL,
                        chamado_url text,
                        chamado_resolvido boolean NOT NULL DEFAULT false,
                        conferido_at timestamptz,
                        reembolso_at timestamptz
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    f'INSERT INTO "{schema}".refunds (conta, tipo, reembolso, conferido, chamado) '
                    "VALUES ('Shopee ATV', 'Cliente', -50, true, 'texto antigo')"
                )
            )
            antes = await _colunas(conn, schema)
            fks_antes = await _fks(conn, schema)

            await conn.run_sync(lambda c: migrar(c, "upgrade"))

            depois = await _colunas(conn, schema)
            do_model = await _colunas(conn, TEST_SCHEMA)
            assert set(depois) == set(antes) | set(NOVAS)
            for coluna in NOVAS:
                assert depois[coluna] == do_model[coluna], coluna
            assert depois["requer_aprovacao"][2:] == ("NO", "false")
            fks = await _fks(conn, schema)
            assert fks[FK] == (await _fks(conn, TEST_SCHEMA))[FK]
            assert fks[FK] == "FOREIGN KEY (aprovado_por) REFERENCES users(id) ON DELETE SET NULL"
            # A linha antiga: nada a aprovar, o resto vazio, o texto antigo como estava.
            linha = (
                await conn.execute(
                    text(
                        f"SELECT requer_aprovacao, motivo, origem, aprovado_em, chamado "
                        f'FROM "{schema}".refunds'
                    )
                )
            ).one()
            assert tuple(linha) == (False, None, None, None, "texto antigo")

            await conn.run_sync(lambda c: migrar(c, "downgrade"))

            assert await _colunas(conn, schema) == antes
            assert await _fks(conn, schema) == fks_antes
            assert (
                await conn.execute(text(f'SELECT conta, reembolso FROM "{schema}".refunds'))
            ).one() == ("Shopee ATV", -50.0)
        finally:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
