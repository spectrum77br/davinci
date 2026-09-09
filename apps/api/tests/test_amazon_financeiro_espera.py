"""Amazon: esperar a transação ser postada não pode matar o pedido na fila.

Eduardo (09/09/2026): "amazon kia, não está pegando o valor do pedido na
plataforma". A conta estava sã (39 dos 41 pedidos enviados no mês com valor),
mas dois pedidos enviados em 08/09 (295311 e 295378) saíram da esteira de
retry no mesmo dia — o teto de 8 tentativas estourou por churn de webhook —
enquanto a Amazon só posta a transação 3 a 4 dias depois do envio. Quando ela
postasse, ninguém buscaria mais.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.models import IntegrationPlatform
from app.models.integration import Integration
from app.models.marketplace_financial import MarketplaceOrderFinancial
from app.services.marketplace_financials import (
    AMAZON_AGUARDANDO_POSTAGEM,
    AMAZON_ESPERA_INTERVALO_HORAS,
    _next_retry_at,
    run_due_marketplace_financial_retries,
)

pytestmark = pytest.mark.asyncio

AGORA = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def test_espera_da_amazon_nao_gasta_o_teto_de_tentativas():
    # Erro de verdade: some da fila na 8ª tentativa, como sempre foi.
    assert _next_retry_at("error", 8, AGORA) is None
    assert _next_retry_at("pending", 20, AGORA) is None
    # Só esperando a Amazon postar: continua na fila, em ritmo lento.
    esperado = AGORA + timedelta(hours=AMAZON_ESPERA_INTERVALO_HORAS)
    assert _next_retry_at("pending", 20, AGORA, aguardando_amazon=True) == esperado
    assert _next_retry_at("pending", 1, AGORA, aguardando_amazon=True) == esperado
    # Status que não é de retry (posted) continua fora.
    assert _next_retry_at("posted", 1, AGORA, aguardando_amazon=True) is None


async def _integ(db, make_user) -> Integration:
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.AMAZON,
        name="kia",
        credentials=b"x",
        status="active",
    )
    db.add(integ)
    await db.flush()
    return integ


async def test_fila_pega_o_pedido_amazon_que_estourou_as_tentativas(db, make_user, monkeypatch):
    """O pedido com 14 tentativas esperando a Amazon volta pra fila; o que
    estourou por erro de verdade fica fora."""
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    integ = await _integ(db, make_user)
    ontem = datetime.now(UTC) - timedelta(hours=13)
    db.add_all(
        [
            MarketplaceOrderFinancial(
                platform=IntegrationPlatform.AMAZON,
                integration_id=integ.id,
                external_order_id="701-9438506-3052219",
                bling_id=26806865006,
                status="pending",
                attempts=14,
                next_retry_at=ontem,
                last_error=AMAZON_AGUARDANDO_POSTAGEM,
            ),
            MarketplaceOrderFinancial(
                platform=IntegrationPlatform.AMAZON,
                integration_id=integ.id,
                external_order_id="701-0000000-0000000",
                bling_id=26800000000,
                status="error",
                attempts=88,
                next_retry_at=ontem,
                last_error="Client error '403 ' for url ...",
            ),
        ]
    )
    await db.commit()

    chamados: list[int] = []

    async def fake_sync(session, *, bling_order_id, **kw):
        chamados.append(bling_order_id)
        return {"status": "pending"}

    monkeypatch.setattr(
        "app.services.marketplace_financials.run_sync_marketplace_financials_for_bling_order",
        fake_sync,
    )

    resumo = await run_due_marketplace_financial_retries(db)

    assert chamados == [26806865006]
    assert resumo["queued"] == 1
