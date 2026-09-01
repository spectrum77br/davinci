"""Sweep do unsettled TikTok (estimativa oficial pré-liquidação).

Contexto (01-02/09/26): o settlement real do TikTok só sai dias após a
entrega, então a Margem ficava com o Saldo Plataforma em branco — mas a
Central do Vendedor mostra o valor na hora. O sweep pagina
GET /finance/202507/orders/unsettled por integração e grava
est_settlement_amount como status='estimated' (padrão do ML pré-billing).

Payloads aqui são o formato REAL validado ao vivo em prod (01/09, loja
91b3410b): transactions[] com order_id, est_settlement_amount string,
type ORDER, e ajustes identificados por adjustment_id.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from app.models import IntegrationPlatform
from app.models.integration import Integration
from app.models.marketplace_financial import MarketplaceOrderFinancial
from app.services.marketplace_financials import (
    FinancialSnapshot,
    _persist_snapshot,
    _tiktok_unsettled_order_map,
    run_tiktok_unsettled_sweep,
)

ORDER_ID = "585843182357153080"


def _tx(order_id: str = ORDER_ID, est: str = "321.25", **extra) -> dict:
    return {
        "order_id": order_id,
        "type": "ORDER",
        "currency": "BRL",
        "est_settlement_amount": est,
        "est_revenue_amount": "402.90",
        "est_fee_tax_amount": "-81.65",
        "unsettled_reason": "WAITING_FOR_PACKAGE_DELIVERY",
        "estimated_settlement": "Delivered + 7 days",
        **extra,
    }


def _page(transactions: list[dict], next_page_token: str = "") -> dict:
    return {
        "code": 0,
        "message": "Success",
        "data": {
            "transactions": transactions,
            "next_page_token": next_page_token,
            "total_count": len(transactions),
        },
    }


class _FakeTikTok:
    """Serve páginas pré-montadas na ordem; registra os page_tokens pedidos."""

    def __init__(self, pages: list[dict]):
        self._pages = list(pages)
        self.tokens_seen: list[str | None] = []

    async def get_unsettled_orders(self, *, search_time_ge, search_time_lt, page_token=None):
        assert search_time_ge < search_time_lt
        self.tokens_seen.append(page_token)
        return self._pages.pop(0)


async def _wipe(db) -> None:
    await db.execute(text("DELETE FROM marketplace_financial_events"))
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    await db.commit()


async def _tiktok_integration(db, make_user, *, status: str = "active") -> Integration:
    user = await make_user()
    integ = Integration(
        user_id=user.id, platform=IntegrationPlatform.TIKTOK, name="loja",
        credentials=b"ignored-since-client-factory-is-injected", status=status,
    )
    db.add(integ)
    await db.flush()
    return integ


def _mof(integ: Integration, *, external_order_id: str = ORDER_ID, status: str = "pending",
         **extra) -> MarketplaceOrderFinancial:
    return MarketplaceOrderFinancial(
        platform=IntegrationPlatform.TIKTOK,
        integration_id=integ.id,
        external_order_id=external_order_id,
        status=status,
        **extra,
    )


# ---------------------------------------------------------------- order map

def test_order_map_ignora_ajustes_e_soma_linhas_do_mesmo_pedido():
    pages = [_page([
        _tx(),
        # refund parcial: segunda linha ORDER do MESMO pedido, negativa
        _tx(est="-21.25"),
        # ajuste avulso tem adjustment_id → fora do repasse base do pedido
        _tx(est="-10.00", adjustment_id="7001", adjustment_order_id=ORDER_ID),
        # lixo defensivo: sem order_id / sem valor
        {"type": "ORDER", "est_settlement_amount": "5.00"},
        {"order_id": "999", "type": "ORDER"},
    ])]
    m = _tiktok_unsettled_order_map(pages)
    assert set(m) == {ORDER_ID}
    assert m[ORDER_ID]["est_settlement_amount"] == "300.00"
    assert m[ORDER_ID]["currency"] == "BRL"
    assert m[ORDER_ID]["unsettled_reason"] == "WAITING_FOR_PACKAGE_DELIVERY"


# ------------------------------------------------------------------- sweep

async def test_sweep_pending_vira_estimated_sem_tocar_ciclo_de_retry(db, make_user):
    await _wipe(db)
    integ = await _tiktok_integration(db, make_user)
    retry_at = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    db.add(_mof(integ, attempts=3, next_retry_at=retry_at, last_error="boom"))
    await db.commit()

    fake = _FakeTikTok([_page([_tx()])])
    result = await run_tiktok_unsettled_sweep(db, client_factory=lambda _i: fake)

    assert result == {"integrations": 1, "candidates": 1, "updated": 1}
    row = (await db.execute(
        text("SELECT status, net_amount, currency, attempts, next_retry_at, last_error, raw "
             "FROM marketplace_order_financials")
    )).mappings().one()
    assert row["status"] == "estimated"
    assert Decimal(row["net_amount"]) == Decimal("321.25")
    assert row["currency"] == "BRL"
    # ciclo do settlement REAL fica intocado — o retry continua agendado
    assert row["attempts"] == 3
    assert row["next_retry_at"] is not None
    assert row["last_error"] is None
    assert row["raw"]["unsettled_estimate"]["est_settlement_amount"] == "321.25"
    assert row["raw"]["unsettled_estimate"]["fetched_at"]


async def test_sweep_nao_toca_posted_nem_sem_match(db, make_user):
    await _wipe(db)
    integ = await _tiktok_integration(db, make_user)
    db.add(_mof(integ, status="posted", net_amount=Decimal("500.00")))
    db.add(_mof(integ, external_order_id="111", status="pending"))
    await db.commit()

    fake = _FakeTikTok([_page([_tx()])])  # só o pedido posted aparece no unsettled
    result = await run_tiktok_unsettled_sweep(db, client_factory=lambda _i: fake)

    # posted nem vira candidato; o pending sem match continua pending
    assert result == {"integrations": 1, "candidates": 1, "updated": 0}
    rows = (await db.execute(
        text("SELECT external_order_id, status, net_amount FROM marketplace_order_financials")
    )).mappings().all()
    by_id = {r["external_order_id"]: r for r in rows}
    assert by_id[ORDER_ID]["status"] == "posted"
    assert Decimal(by_id[ORDER_ID]["net_amount"]) == Decimal("500.00")
    assert by_id["111"]["status"] == "pending"
    assert by_id["111"]["net_amount"] is None


async def test_sweep_pagina_ate_o_next_page_token_acabar(db, make_user):
    await _wipe(db)
    integ = await _tiktok_integration(db, make_user)
    db.add(_mof(integ, external_order_id="222"))
    await db.commit()

    fake = _FakeTikTok([
        _page([_tx()], "tok2"),
        _page([_tx(order_id="222", est="88.10")]),
    ])
    result = await run_tiktok_unsettled_sweep(db, client_factory=lambda _i: fake)

    assert fake.tokens_seen == [None, "tok2"]
    assert result["updated"] == 1
    net = (await db.execute(
        text("SELECT net_amount FROM marketplace_order_financials")
    )).scalar_one()
    assert Decimal(net) == Decimal("88.10")


async def test_sweep_repetido_com_mesmo_valor_nao_regrava(db, make_user):
    await _wipe(db)
    integ = await _tiktok_integration(db, make_user)
    db.add(_mof(integ))
    await db.commit()

    await run_tiktok_unsettled_sweep(
        db, client_factory=lambda _i: _FakeTikTok([_page([_tx()])])
    )
    result = await run_tiktok_unsettled_sweep(
        db, client_factory=lambda _i: _FakeTikTok([_page([_tx()])])
    )
    # estimated segue candidato (RETRYABLE), mas valor igual não conta update
    assert result == {"integrations": 1, "candidates": 1, "updated": 0}


async def test_sweep_erro_em_uma_loja_nao_derruba_a_outra(db, make_user):
    await _wipe(db)
    integ_a = await _tiktok_integration(db, make_user)
    integ_b = await _tiktok_integration(db, make_user)
    db.add(_mof(integ_a))
    db.add(_mof(integ_b, external_order_id="333"))
    await db.commit()

    fakes = {
        integ_a.id: _FakeTikTok([{"code": 36009002, "message": "rate limit"}]),
        integ_b.id: _FakeTikTok([_page([_tx(order_id="333", est="50.00")])]),
    }
    result = await run_tiktok_unsettled_sweep(db, client_factory=lambda i: fakes[i.id])

    assert result == {"integrations": 1, "candidates": 2, "updated": 1}
    rows = (await db.execute(
        text("SELECT external_order_id, status FROM marketplace_order_financials")
    )).mappings().all()
    by_id = {r["external_order_id"]: r["status"] for r in rows}
    assert by_id[ORDER_ID] == "pending"  # loja com erro fica como estava
    assert by_id["333"] == "estimated"


async def test_sweep_pula_integracao_inativa(db, make_user):
    await _wipe(db)
    integ = await _tiktok_integration(db, make_user, status="disconnected")
    db.add(_mof(integ))
    await db.commit()

    result = await run_tiktok_unsettled_sweep(
        db, client_factory=lambda _i: _FakeTikTok([_page([_tx()])])
    )
    assert result == {"integrations": 0, "candidates": 1, "updated": 0}


# ------------------------------------------- guard do _persist_snapshot

async def _persist(db, integ, snapshot):
    return await _persist_snapshot(
        db, snapshot,
        platform=IntegrationPlatform.TIKTOK, integration=integ, store=None,
        bling_id=291000, pedido_bling="291000", external_order_id=ORDER_ID,
    )


async def test_retry_vazio_preserva_estimativa_e_dado_real_substitui(db, make_user):
    await _wipe(db)
    integ = await _tiktok_integration(db, make_user)
    db.add(_mof(integ))
    await db.commit()
    await run_tiktok_unsettled_sweep(
        db, client_factory=lambda _i: _FakeTikTok([_page([_tx()])])
    )

    # retry do settlement real volta vazio (ainda não postado) → NÃO apaga
    fin = await _persist(db, integ, FinancialSnapshot(status="pending", raw={"statements": []}))
    await db.commit()
    assert fin.status == "estimated"
    assert fin.net_amount == Decimal("321.25")
    assert fin.raw["unsettled_estimate"]["est_settlement_amount"] == "321.25"
    assert fin.raw["statements"] == []  # raw do fetch preservado junto
    assert fin.attempts == 1  # o ciclo do retry continua contando

    # settlement REAL postado → substitui a estimativa e derruba o marcador
    fin = await _persist(
        db, integ,
        FinancialSnapshot(status="posted", net_amount=Decimal("318.90"), raw={"real": True}),
    )
    await db.commit()
    assert fin.status == "posted"
    assert fin.net_amount == Decimal("318.90")
    assert "unsettled_estimate" not in fin.raw


async def test_retry_com_erro_preserva_estimativa(db, make_user):
    await _wipe(db)
    integ = await _tiktok_integration(db, make_user)
    db.add(_mof(integ))
    await db.commit()
    await run_tiktok_unsettled_sweep(
        db, client_factory=lambda _i: _FakeTikTok([_page([_tx()])])
    )

    fin = await _persist(
        db, integ, FinancialSnapshot(status="error", error="HTTP 500", raw={})
    )
    await db.commit()
    assert fin.status == "estimated"
    assert fin.net_amount == Decimal("321.25")
    assert fin.last_error == "HTTP 500"  # o erro do fetch continua registrado
