"""Audit trigger de bling_orders.em_andamento_data (migration 0135).

Como o conftest cria as tabelas via Base.metadata.create_all (não roda
alembic), o teste instala o trigger inline e valida que:
  - INSERT com em_andamento_data NULL não gera row
  - INSERT com em_andamento_data setada gera row 'I'
  - UPDATE mudando em_andamento_data gera row 'U'
  - UPDATE em outro campo (sem tocar em em_andamento_data) NÃO gera row
"""
# ruff: noqa: S608
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder

SCHEMA = "davinci_test"


_TRIGGER_DDL = [
    f"""
    CREATE TABLE IF NOT EXISTS "{SCHEMA}".audit_em_andamento_data (
        id BIGSERIAL PRIMARY KEY,
        bling_order_id UUID NOT NULL,
        bling_id BIGINT,
        numero TEXT,
        op CHAR(1) NOT NULL,
        old_data DATE,
        new_data DATE,
        old_situacao TEXT,
        new_situacao TEXT,
        application_name TEXT,
        "session_user" TEXT NOT NULL DEFAULT current_user,
        pg_backend_pid INTEGER NOT NULL DEFAULT pg_backend_pid(),
        changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    f"""
    CREATE OR REPLACE FUNCTION "{SCHEMA}".audit_em_andamento_data_fn()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.em_andamento_data IS NOT NULL THEN
                INSERT INTO "{SCHEMA}".audit_em_andamento_data
                    (bling_order_id, bling_id, numero, op,
                     old_data, new_data, old_situacao, new_situacao,
                     application_name)
                VALUES
                    (NEW.id, NEW.bling_id, NEW.numero, 'I',
                     NULL, NEW.em_andamento_data, NULL, NEW.situacao,
                     current_setting('application_name', true));
            END IF;
        ELSIF TG_OP = 'UPDATE'
              AND OLD.em_andamento_data IS DISTINCT FROM NEW.em_andamento_data THEN
            INSERT INTO "{SCHEMA}".audit_em_andamento_data
                (bling_order_id, bling_id, numero, op,
                 old_data, new_data, old_situacao, new_situacao,
                 application_name)
            VALUES
                (NEW.id, NEW.bling_id, NEW.numero, 'U',
                 OLD.em_andamento_data, NEW.em_andamento_data,
                 OLD.situacao, NEW.situacao,
                 current_setting('application_name', true));
        END IF;
        RETURN NULL;
    END;
    $$
    """,
    f"""
    DROP TRIGGER IF EXISTS bling_orders_audit_em_andamento_data
    ON "{SCHEMA}".bling_orders
    """,
    f"""
    CREATE TRIGGER bling_orders_audit_em_andamento_data
    AFTER INSERT OR UPDATE OF em_andamento_data
    ON "{SCHEMA}".bling_orders
    FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".audit_em_andamento_data_fn()
    """,
]


async def _install_audit(db: AsyncSession) -> None:
    for stmt in _TRIGGER_DDL:
        await db.execute(text(stmt))
    await db.commit()


async def _drop_audit(db: AsyncSession) -> None:
    """Cleanup pra não vazar pra outros testes."""
    await db.execute(text(
        f'DROP TRIGGER IF EXISTS bling_orders_audit_em_andamento_data '
        f'ON "{SCHEMA}".bling_orders'
    ))
    await db.execute(text(
        f'DROP FUNCTION IF EXISTS "{SCHEMA}".audit_em_andamento_data_fn() CASCADE'
    ))
    await db.execute(text(
        f'DROP TABLE IF EXISTS "{SCHEMA}".audit_em_andamento_data'
    ))
    await db.commit()


async def _count_audit(db: AsyncSession) -> int:
    r = await db.execute(text(
        f'SELECT count(*) FROM "{SCHEMA}".audit_em_andamento_data'
    ))
    return int(r.scalar() or 0)


@pytest.mark.asyncio
async def test_insert_com_data_gera_audit_e_sem_data_nao_gera(db: AsyncSession):
    await _install_audit(db)
    try:
        db.add(BlingOrder(
            bling_id=100001, numero="100001", item_codigo="a", item_index=0,
            situacao="15", em_andamento_data=None,
        ))
        await db.commit()
        assert await _count_audit(db) == 0

        db.add(BlingOrder(
            bling_id=100002, numero="100002", item_codigo="a", item_index=0,
            situacao="15", em_andamento_data=date(2026, 6, 9),
        ))
        await db.commit()
        rows = (await db.execute(text(
            f'SELECT op, new_data, new_situacao '
            f'FROM "{SCHEMA}".audit_em_andamento_data'
        ))).all()
        assert len(rows) == 1
        assert rows[0].op == "I"
        assert rows[0].new_data == date(2026, 6, 9)
        assert rows[0].new_situacao == "15"
    finally:
        await _drop_audit(db)


@pytest.mark.asyncio
async def test_update_mudando_data_gera_audit_u(db: AsyncSession):
    await _install_audit(db)
    try:
        order = BlingOrder(
            bling_id=100003, numero="100003", item_codigo="a", item_index=0,
            situacao="15", em_andamento_data=date(2026, 6, 1),
        )
        db.add(order)
        await db.commit()
        # Reset: a row do INSERT é esperada — limpa antes de testar o UPDATE.
        await db.execute(text(f'TRUNCATE "{SCHEMA}".audit_em_andamento_data'))
        await db.commit()

        order.em_andamento_data = date(2026, 6, 5)
        order.situacao = "83953"
        await db.commit()

        rows = (await db.execute(text(
            f'SELECT op, old_data, new_data, old_situacao, new_situacao '
            f'FROM "{SCHEMA}".audit_em_andamento_data'
        ))).all()
        assert len(rows) == 1
        r = rows[0]
        assert r.op == "U"
        assert r.old_data == date(2026, 6, 1)
        assert r.new_data == date(2026, 6, 5)
        assert r.old_situacao == "15"
        assert r.new_situacao == "83953"
    finally:
        await _drop_audit(db)


@pytest.mark.asyncio
async def test_update_em_outro_campo_nao_gera_audit(db: AsyncSession):
    """O trigger é `OF em_andamento_data` — UPDATE em outras colunas
    nem deve disparar a função."""
    await _install_audit(db)
    try:
        order = BlingOrder(
            bling_id=100004, numero="100004", item_codigo="a", item_index=0,
            situacao="15", em_andamento_data=date(2026, 6, 1),
        )
        db.add(order)
        await db.commit()
        await db.execute(text(f'TRUNCATE "{SCHEMA}".audit_em_andamento_data'))
        await db.commit()

        # Só mexe na situacao.
        order.situacao = "83953"
        await db.commit()

        assert await _count_audit(db) == 0
    finally:
        await _drop_audit(db)
