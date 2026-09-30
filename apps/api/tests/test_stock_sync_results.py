"""Preserve failed stock results and logs without adding sellout alerts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.models import AlertType, IntegrationPlatform, Product, ProductLink
from app.services import sync_orchestrator as module
from app.services.marketplaces.base import SyncResult, SyncStatus


async def process(monkeypatch, *, qty=0, status=SyncStatus.RETRYABLE,
                  error_code="product.error_busi_update_stock_failed", raises=False,
                  replacement_qty=None):
    user_id = uuid4()
    product = Product(id=uuid4(), user_id=user_id, sku="dg090.pi", stock=0)
    integration = SimpleNamespace(id=uuid4(), archived_at=None, vacation_mode=False)
    link = ProductLink(
        id=uuid4(), user_id=user_id, product_id=product.id,
        integration_id=integration.id, platform=IntegrationPlatform.SHOPEE,
        external_id="23094874851", variation_id="228803725060", stock=1,
    )
    result = SyncResult(
        status=status, qty_before=1, qty_after=qty if status == SyncStatus.OK else None,
        error_code=error_code, error_detail="Reserved stock cannot be reduced",
    )
    client = SimpleNamespace(update_stock=AsyncMock(
        side_effect=RuntimeError("Connection interrupted") if raises else None,
        return_value=result,
    ))
    session = SimpleNamespace(add=Mock())
    orch = module.SyncOrchestrator(session, user_id=user_id)
    orch._get_store = AsyncMock(return_value=None)
    orch._get_integration = AsyncMock(return_value=integration)
    orch._client = AsyncMock(return_value=client)
    orch._atualizar_saude = AsyncMock()
    orch._append_detail = AsyncMock()
    stock_lookup = AsyncMock(return_value=qty)
    if replacement_qty is not None:
        replacement = Product(id=uuid4(), user_id=user_id, sku="dg091.pi", stock=replacement_qty)
        orch._produto_do_sku = AsyncMock(return_value=replacement)
        stock_lookup = AsyncMock(side_effect=[qty, replacement_qty])
        client.update_stock.side_effect = [
            SyncResult(status=SyncStatus.REQUIRES_REVIEW, error_code="sku_trocado",
                       payload={"sku_atual": replacement.sku}),
            result,
        ]
    alert = AsyncMock()
    monkeypatch.setattr(module, "emit_alert", alert)
    monkeypatch.setattr(module.estoque_familia, "saldo_publicavel", stock_lookup)
    outcome = await orch._process_link(product, link)
    return link, outcome, alert, session


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code,existing_alert", [
    (SyncStatus.RETRYABLE, "product.error_busi_update_stock_failed", None),
    (SyncStatus.FATAL, "http_400", AlertType.SYNC_FAILURE),
    (SyncStatus.REQUIRES_REVIEW, "stock_rejected", AlertType.LISTING_BANNED),
    (SyncStatus.SKIPPED, "b1_guard_zero_block", None),
])
async def test_rejected_zero_preserves_error_log_and_existing_alerts_only(
    monkeypatch, status, code, existing_alert
):
    link, result, alert, session = await process(monkeypatch, status=status, error_code=code)
    assert [c.kwargs["type"] for c in alert.await_args_list] == (
        [existing_alert] if existing_alert is not None else []
    )
    assert link.stock == 1
    assert result.status == status
    assert result.error_code == code
    log = session.add.call_args.args[0]
    assert log.payload["requested_stock"] == 0
    assert log.error_code == code
    assert log.error_detail == "Reserved stock cannot be reduced"
    assert log.qty_after is None


@pytest.mark.asyncio
async def test_interrupted_zero_preserves_error_and_existing_failure_alert(monkeypatch):
    link, result, alert, session = await process(monkeypatch, raises=True)
    assert result.status == SyncStatus.FATAL
    assert link.stock == 1
    alert.assert_awaited_once()
    assert alert.await_args.kwargs["dedupe_key"] == f"sync_failure:{link.id}"
    log = session.add.call_args.args[0]
    assert log.error_code == "orchestrator_exception"
    assert log.error_detail == "Connection interrupted"
    assert log.payload["requested_stock"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("qty,status,code", [
    (0, SyncStatus.OK, None),
    (0, SyncStatus.SKIPPED, "ml_listing_paused"),
    (0, SyncStatus.SKIPPED, "ml_listing_closed"),
    (12, SyncStatus.RETRYABLE, "product.error_busi_update_stock_failed"),
])
async def test_confirmed_or_safe_skip_and_positive_retry_do_not_alert(
    monkeypatch, qty, status, code
):
    link, _, alert, _ = await process(monkeypatch, qty=qty, status=status, error_code=code)
    alert.assert_not_awaited()
    assert link.stock == (qty if status == SyncStatus.OK else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("original,replacement", [(0, 77), (77, 0)])
async def test_sku_repoint_log_uses_actual_target_quantity(monkeypatch, original, replacement):
    link, result, alert, session = await process(
        monkeypatch, qty=original, replacement_qty=replacement
    )
    assert result.payload["requested_stock"] == replacement
    log = session.add.call_args.args[0]
    assert log.payload["requested_stock"] == replacement
    assert log.product_id == link.product_id
    assert link.stock == 1
    alert.assert_not_awaited()
