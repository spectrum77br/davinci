"""A 0512 (M-68) refaz a trava do Certificado para aceitar 'anvisa'.

O conftest monta o schema de teste pelo `create_all` do model, que não declara essa
trava. Aqui a migration roda de verdade num schema rascunho com a trava da 0083 com o
nome que ela tem na produção (`ck_financeiro_suprimentos_ck_financeiro_suprimentos_
certificado`, conferido na previa52): depois do upgrade aceita 'anvisa' e continua
recusando o resto; nenhum dado muda. O downgrade volta a trava antiga e PARA (sem
mudar nada) se houver linha com 'anvisa'.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Base

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0515_certificacoes_sku_anvisa.py"
)
TRAVA = "ck_financeiro_suprimentos_ck_financeiro_suprimentos_certificado"
CHECK_0083 = (
    "CHECK (((certificado IS NULL) OR (certificado = ANY "
    "(ARRAY['anatel'::text, 'inmetro'::text, 'isento'::text, ''::text]))))"
)
CHECK_0512 = (
    "CHECK (((certificado IS NULL) OR (certificado = ANY "
    "(ARRAY['anatel'::text, 'inmetro'::text, 'anvisa'::text, 'isento'::text, ''::text]))))"
)


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0512", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _travas(db: AsyncSession, schema: str) -> list[tuple[str, str]]:
    return [
        tuple(r)
        for r in (
            await db.execute(
                text(
                    "SELECT co.conname, pg_get_constraintdef(co.oid) FROM pg_constraint co "
                    "JOIN pg_class cl ON cl.oid = co.conrelid "
                    "JOIN pg_namespace n ON n.oid = cl.relnamespace "
                    "WHERE n.nspname = :s AND cl.relname = 'financeiro_suprimentos' "
                    "AND co.contype = 'c' AND pg_get_constraintdef(co.oid) LIKE '%certificado%' "
                    "ORDER BY co.conname"
                ),
                {"s": schema},
            )
        ).all()
    ]


async def _aceita(db: AsyncSession, schema: str, valor: str | None) -> bool:
    try:
        async with db.begin_nested():
            await db.execute(
                text(
                    f'INSERT INTO "{schema}".financeiro_suprimentos (id, produto, certificado)'
                    " VALUES (:id, 'teste da trava', :v)"
                ),
                {"id": uuid4(), "v": valor},
            )
        return True
    except IntegrityError:
        return False


async def _dados(db: AsyncSession, schema: str) -> list[tuple]:
    return [
        tuple(r)
        for r in (
            await db.execute(
                text(
                    "SELECT id, produto, certificado, modelo, numero"
                    f' FROM "{schema}".financeiro_suprimentos ORDER BY id'
                )
            )
        ).all()
    ]


@pytest.mark.parametrize("nome_antigo", [TRAVA, "ck_financeiro_suprimentos_certificado"])
async def test_migration_0512_aceita_anvisa_sem_mudar_dados_e_o_downgrade_volta(
    db: AsyncSession, nome_antigo: str
):
    mod = _carregar()
    assert (mod.revision, mod.down_revision) == (
        "0515_certificacoes_sku_anvisa",
        "0514_threema_contatos_copia",
    )
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0512"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".financeiro_suprimentos'
            f' (LIKE "{schema_model}".financeiro_suprimentos INCLUDING DEFAULTS INCLUDING INDEXES)'
        )
    )
    # A conversão da M-69 (mesma migration) lê a Tabela de Preços: vazia aqui
    # (a conversão tem teste próprio, test_certificacao_sku_migration.py).
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".pricing_products'
            f' (LIKE "{schema_model}".pricing_products INCLUDING DEFAULTS)'
        )
    )
    # A trava como a 0083 deixou (na produção, com o nome dobrado pela convenção).
    await db.execute(
        text(
            f'ALTER TABLE "{rascunho}".financeiro_suprimentos ADD CONSTRAINT {nome_antigo}'
            " CHECK (certificado IS NULL OR certificado IN ('anatel', 'inmetro', 'isento', ''))"
        )
    )
    for valor in ("anatel", "inmetro", "isento", "", None):
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".financeiro_suprimentos'
                " (id, produto, modelo, numero, certificado) VALUES (:id, :p, 'M1', 'N1', :v)"
            ),
            {"id": uuid4(), "p": f"linha {valor!r}", "v": valor},
        )
    await db.commit()
    assert await _travas(db, rascunho) == [(nome_antigo, CHECK_0083)]
    assert not await _aceita(db, rascunho, "anvisa"), "antes da 0512 a Anvisa não entra"
    dados_antes = await _dados(db, rascunho)

    def _rodar_passo(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar_passo, "upgrade")
        await db.commit()
        assert await _travas(db, rascunho) == [(TRAVA, CHECK_0512)]
        assert await _dados(db, rascunho) == dados_antes, "nenhum dado muda"
        for valor in ("anvisa", "anatel", "inmetro", "isento", "", None):
            assert await _aceita(db, rascunho, valor), valor
        for ruim in ("ANVISA", "Anatel", "outro"):
            assert not await _aceita(db, rascunho, ruim), ruim
        await db.commit()

        # Com linha Anvisa o downgrade para e não muda nada.
        conn = await db.connection()
        with pytest.raises(RuntimeError, match=r"1 certificação\(ões\) com Certificado = Anvisa"):
            await conn.run_sync(_rodar_passo, "downgrade")
        await db.rollback()
        assert await _travas(db, rascunho) == [(TRAVA, CHECK_0512)]
        anvisa = await db.scalar(
            text(
                f'SELECT count(*) FROM "{rascunho}".financeiro_suprimentos'
                " WHERE certificado = 'anvisa'"
            )
        )
        assert anvisa == 1

        # Trocada a linha Anvisa, o downgrade volta a trava da 0083 (nome de produção).
        await db.execute(
            text(
                f"UPDATE \"{rascunho}\".financeiro_suprimentos SET certificado = 'isento'"
                " WHERE certificado = 'anvisa'"
            )
        )
        await db.commit()
        conn = await db.connection()
        await conn.run_sync(_rodar_passo, "downgrade")
        await db.commit()
        assert await _travas(db, rascunho) == [(TRAVA, CHECK_0083)]
        assert not await _aceita(db, rascunho, "anvisa")
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
