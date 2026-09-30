"""Rejected sellouts must be visible without mirroring an unconfirmed zero."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.models import AlertSeverity, AlertType, IntegrationPlatform, Product, ProductLink
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
@pytest.mark.parametrize("status,code", [
    (SyncStatus.RETRYABLE, "product.error_busi_update_stock_failed"),
    (SyncStatus.FATAL, "http_400"),
    (SyncStatus.REQUIRES_REVIEW, "stock_rejected"),
    (SyncStatus.SKIPPED, "b1_guard_zero_block"),
])
async def test_rejected_zero_alerts_without_claiming_success(monkeypatch, status, code):
    link, result, alert, session = await process(monkeypatch, status=status, error_code=code)
    critical = [c.kwargs for c in alert.await_args_list
                if c.kwargs["dedupe_key"].startswith("stock_zero_unconfirmed:")]
    assert len(critical) == 1
    notice = critical[0]
    assert notice["type"] == AlertType.SYNC_FAILURE
    assert notice["severity"] == AlertSeverity.ERROR
    assert notice["notify_telegram"] is False
    assert notice["payload"]["variation_id"] == "228803725060"
    assert notice["payload"]["requested_stock"] == 0
    assert notice["dedupe_key"] == f"stock_zero_unconfirmed:{link.id}"
    assert link.stock == 1
    assert result.status == status
    assert result.error_code == code
    assert session.add.call_args.args[0].payload["requested_stock"] == 0


@pytest.mark.asyncio
async def test_interrupted_zero_request_also_alerts(monkeypatch):
    link, result, alert, _ = await process(monkeypatch, raises=True)
    assert result.status == SyncStatus.FATAL
    assert link.stock == 1
    assert any(c.kwargs["title"].startswith("Zeramento não confirmado")
               for c in alert.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("qty,status,code", [
    (0, SyncStatus.OK, None),
    (0, SyncStatus.SKIPPED, "ml_listing_paused"),
    (0, SyncStatus.SKIPPED, "ml_listing_closed"),
    (12, SyncStatus.RETRYABLE, "product.error_busi_update_stock_failed"),
])
async def test_confirmed_or_safe_skip_and_positive_stock_do_not_raise_zero_alert(
    monkeypatch, qty, status, code
):
    link, _, alert, _ = await process(monkeypatch, qty=qty, status=status, error_code=code)
    assert not any(c.kwargs["dedupe_key"].startswith("stock_zero_unconfirmed:")
                   for c in alert.await_args_list)
    assert link.stock == (qty if status == SyncStatus.OK else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("original,replacement", [(0, 77), (77, 0)])
async def test_sku_repoint_alert_uses_actual_target_quantity(monkeypatch, original, replacement):
    link, result, alert, _ = await process(
        monkeypatch, qty=original, replacement_qty=replacement
    )
    notices = [c.kwargs for c in alert.await_args_list
               if c.kwargs["dedupe_key"].startswith("stock_zero_unconfirmed:")]
    assert result.payload["requested_stock"] == replacement
    assert len(notices) == (1 if replacement == 0 else 0)
    if notices:
        assert notices[0]["title"].endswith("dg091.pi")
        assert notices[0]["payload"]["product_id"] == str(link.product_id)
