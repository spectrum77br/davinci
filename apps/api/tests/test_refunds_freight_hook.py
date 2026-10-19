"""A conferência de frete não pode desfazer o financeiro já sincronizado.

Exercita PostgreSQL, persistência real e o hook real. Somente a resolução da
integração e a consulta externa são simuladas: nenhuma API de marketplace é
chamada. A função SQL mínima permite provocar uma falha real de transação.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, select, text

from app.config import get_settings
from app.models import BlingOrder, Integration, IntegrationPlatform, Refund
from app.models.marketplace_financial import MarketplaceFinancialEvent, MarketplaceOrderFinancial
from app.services import marketplace_financials as financials

pytestmark = pytest.mark.asyncio

_BLING_ID = 9900417001
_PEDIDO = "HOOK-FRETE-001"
_EXTERNO = "MARKETPLACE-HOOK-001"


async def _install_refund_function(db, *, mode: str) -> None:
    """Instala somente no schema isolado dos testes; não faz commit."""
    schema = get_settings().database_schema
    function = f'"{schema}".refunds_freight_for_pedido'
    await db.execute(text(f"DROP FUNCTION IF EXISTS {function}(text)"))
    if mode == "missing":
        return
    columns = """
        data timestamptz,
        pedido_bling text,
        pedido_marketplace text,
        plataforma_bling text,
        plataforma_financeiro text,
        loja_nome text,
        evento_freight numeric,
        evento_frete_anuncio numeric,
        item_proportion numeric,
        marketplace_frete_real_cobrado_item numeric
    """
    if mode == "sql_failure":
        body = """
            LANGUAGE plpgsql AS $test$
            BEGIN
                RAISE EXCEPTION 'Falha SQL deliberada no teste de frete'
                    USING ERRCODE = '22012';
            END
            $test$
        """
    else:
        assert mode == "success"
        # Usa o financeiro recém-gravado pela sincronização, ainda sem commit.
        body = f"""
            LANGUAGE sql STABLE AS $test$
                SELECT now(), f.pedido_bling, f.external_order_id,
                       f.platform::text, f.platform::text, 'Loja do teste'::text,
                       f.freight_amount, 10::numeric, 1::numeric, f.freight_amount
                FROM "{schema}".marketplace_order_financials f
                WHERE f.pedido_bling = p_pedido
            $test$
        """  # noqa: S608 -- schema isolado controlado pelo conftest
    await db.execute(
        text(f"CREATE FUNCTION {function}(p_pedido text) RETURNS TABLE ({columns}) {body}")
    )


async def _setup_sync(db, make_user, monkeypatch, *, function_mode: str):
    # O cleanup comum não apaga marketplace_order_financials. Remove somente
    # os dados desta fixture; seus eventos são removidos por ON DELETE CASCADE.
    await db.execute(
        delete(MarketplaceOrderFinancial).where(MarketplaceOrderFinancial.bling_id == _BLING_ID)
    )
    user = await make_user()
    integration = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.ML,
        name="Integração fictícia do teste de frete",
        credentials=b"test-only-no-real-credentials",
        status="active",
    )
    db.add(integration)
    db.add(
        BlingOrder(
            bling_id=_BLING_ID,
            numero=_PEDIDO,
            numeroloja=_EXTERNO,
            data=datetime.now(UTC),
            situacao="Em aberto",
            item_codigo="SKU-DO-TESTE",
            item_quantidade=1,
            item_index=0,
        )
    )
    await _install_refund_function(db, mode=function_mode)
    await db.commit()
    snapshot = financials.FinancialSnapshot(
        status="posted",
        gross_amount=Decimal("100.00"),
        fee_amount=Decimal("10.00"),
        freight_amount=Decimal("18.00"),
        net_amount=Decimal("72.00"),
        events=[
            financials.FinancialEventDraft(event_type="sale", amount=Decimal("100.00")),
            financials.FinancialEventDraft(event_type="commission_fee", amount=Decimal("-10.00")),
            financials.FinancialEventDraft(event_type="freight", amount=Decimal("18.00")),
        ],
    )
    fetch = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(
        financials, "_resolve_store_and_integration", AsyncMock(return_value=(None, integration))
    )
    monkeypatch.setattr(financials, "_fetch_snapshot", fetch)
    # trigger=webhook não deve executar refresh de margem, nem realizar I/O externo.
    monkeypatch.setattr(
        financials,
        "_verificar_margem_refresh_silent",
        AsyncMock(side_effect=AssertionError("O hook não deve refrescar a margem em webhook")),
    )
    return fetch


async def _sync(db):
    return await financials.run_sync_marketplace_financials_for_bling_order(
        db, bling_order_id=_BLING_ID, trigger="webhook"
    )


async def _assert_financial_persisted(db) -> None:
    record = (
        await db.execute(
            select(MarketplaceOrderFinancial).where(MarketplaceOrderFinancial.bling_id == _BLING_ID)
        )
    ).scalar_one()
    assert record.status == "posted"
    assert record.net_amount == Decimal("72.00")
    assert record.freight_amount == Decimal("18.00")
    assert record.external_order_id == _EXTERNO
    events = (
        await db.execute(
            select(MarketplaceFinancialEvent.event_type, MarketplaceFinancialEvent.amount).where(
                MarketplaceFinancialEvent.order_financial_id == record.id
            )
        )
    ).all()
    assert dict(events) == {
        "sale": Decimal("100.00"),
        "commission_fee": Decimal("-10.00"),
        "freight": Decimal("18.00"),
    }


@pytest.mark.parametrize("failure", ["missing", "sql_failure"])
async def test_refund_sql_failure_preserves_financial_sync_and_session(
    db, make_user, monkeypatch, failure
):
    fetch = await _setup_sync(db, make_user, monkeypatch, function_mode=failure)

    result = await _sync(db)

    assert result["ok"] is True
    # Um except sem savepoint deixa o PostgreSQL abortado e falha aqui.
    assert (await db.execute(text("SELECT 42"))).scalar_one() == 42
    await db.commit()
    await _assert_financial_persisted(db)
    assert (await db.execute(select(func.count()).select_from(Refund))).scalar_one() == 0
    fetch.assert_awaited_once()

    # A falha também não pode deixar o controle de concorrência preso:
    # após reparar a função, a próxima tentativa termina e cria o registro.
    await _install_refund_function(db, mode="success")
    result = await asyncio.wait_for(_sync(db), timeout=10)
    await db.commit()
    assert result["ok"] is True
    await _assert_financial_persisted(db)
    refund = (await db.execute(select(Refund))).scalar_one()
    assert refund.pedido_bling == _PEDIDO
    assert refund.prejuizo == pytest.approx(8.0)
    assert refund.reembolso is None
    assert refund.conferido is False


async def test_refund_hook_sees_uncommitted_financial_and_does_not_duplicate(
    db, make_user, monkeypatch
):
    fetch = await _setup_sync(db, make_user, monkeypatch, function_mode="success")

    assert (await _sync(db))["ok"] is True
    await db.commit()
    await _assert_financial_persisted(db)
    original = (await db.execute(select(Refund))).scalar_one()
    original_id = original.id
    assert original.prejuizo == pytest.approx(8.0)

    assert (await _sync(db))["ok"] is True
    await db.commit()
    rows = (await db.execute(select(Refund))).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == original_id
    assert rows[0].prejuizo == pytest.approx(8.0)
    assert fetch.await_count == 2


async def test_successful_refund_hook_preserves_callers_rollback(db, make_user, monkeypatch):
    await _setup_sync(db, make_user, monkeypatch, function_mode="success")

    assert (await _sync(db))["ok"] is True
    assert (await db.execute(select(func.count()).select_from(Refund))).scalar_one() == 1
    await db.rollback()

    # Savepoint concluído não equivale a commit: o chamador ainda decide
    # se persiste o financeiro, os eventos e o novo reembolso juntos.
    assert (await db.execute(select(func.count()).select_from(Refund))).scalar_one() == 0
    for model in (MarketplaceOrderFinancial, MarketplaceFinancialEvent):
        assert (
            await db.execute(
                select(func.count()).select_from(model).where(model.bling_id == _BLING_ID)
            )
        ).scalar_one() == 0
    assert (
        await db.execute(
            select(func.count()).select_from(BlingOrder).where(BlingOrder.bling_id == _BLING_ID)
        )
    ).scalar_one() == 1


async def test_concurrent_worker_syncs_keep_one_refund_and_consistent_financial(
    db, make_user, monkeypatch
):
    """Dois jobs reais do mesmo pedido: o segundo aguarda o commit do primeiro.

    O teste confirma a espera no PostgreSQL, não apenas uma ordem acidental
    das corrotinas. A proteção testada é o lock por Bling ID já usado no worker.
    """
    import app.db as app_db
    from app import worker

    fetch = await _setup_sync(db, make_user, monkeypatch, function_mode="success")
    snapshot = fetch.return_value
    first_fetch_started = asyncio.Event()
    release_first_fetch = asyncio.Event()
    second_session_started = asyncio.Event()
    session_pids: list[int] = []

    async def controlled_fetch(*_args, **_kwargs):
        if not first_fetch_started.is_set():
            first_fetch_started.set()
            await release_first_fetch.wait()
        return snapshot

    fetch.side_effect = controlled_fetch

    @asynccontextmanager
    async def local_session_scope():
        # SessionLocal foi substituído pelo conftest: todas as conexões são
        # locais, com o search_path preso ao schema exclusivo desta execução.
        async with app_db.SessionLocal() as session:
            pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            session_pids.append(pid)
            if len(session_pids) == 2:
                second_session_started.set()
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    monkeypatch.setattr(worker, "session_scope", local_session_scope)

    async def wait_for_database_lock_waiter():
        while True:
            waiting = (
                await db.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        "WHERE pid = :pid AND locktype = 'advisory' AND NOT granted)"
                    ),
                    {"pid": session_pids[1]},
                )
            ).scalar_one()
            if waiting:
                return
            await asyncio.sleep(0.01)

    jobs: list[asyncio.Task] = []
    try:
        jobs.append(
            asyncio.create_task(
                worker.sync_marketplace_financials_for_order_run({}, _BLING_ID, trigger="webhook")
            )
        )
        await asyncio.wait_for(first_fetch_started.wait(), timeout=10)
        jobs.append(
            asyncio.create_task(
                worker.sync_marketplace_financials_for_order_run({}, _BLING_ID, trigger="webhook")
            )
        )
        await asyncio.wait_for(second_session_started.wait(), timeout=10)
        await asyncio.wait_for(wait_for_database_lock_waiter(), timeout=10)
        assert len(set(session_pids)) == 2
        assert fetch.await_count == 1

        release_first_fetch.set()
        await asyncio.wait_for(asyncio.gather(*jobs), timeout=10)
    finally:
        release_first_fetch.set()
        for job in jobs:
            if not job.done():
                job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    assert fetch.await_count == 2
    await _assert_financial_persisted(db)
    refund = (await db.execute(select(Refund))).scalar_one()
    assert refund.pedido_bling == _PEDIDO
    assert refund.prejuizo == pytest.approx(8.0)
    assert refund.reembolso is None
    assert refund.conferido is False
