"""Queda de API não pode apagar a Margem para sempre.

Eduardo, 15/09/2026: "margem e logistica tinham parado de popular, porque tinha
parado as apis". Nove contas Shopee com 403, a Amazon com o LWA vencido e o
Mercado Livre recusando o refresh deixaram as plataformas fora do ar por dias.
Cada tentativa falha gastava uma das 8 do backoff, então ~3.900 pedidos
terminaram com `next_retry_at = NULL` — fora da fila para sempre. Quando os
tokens voltaram, o Frete/Taxa desses pedidos continuou em branco porque nada no
sistema reabria a linha.

Aqui ficam as três garantias da correção:
  1. falha de API é classificada como transitória;
  2. transitória vai pra esteira lenta e NÃO gasta tentativa;
  3. quem já morreu volta sozinho pela ressurreição, e só quem merece.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.models import IntegrationPlatform
from app.models.integration import Integration
from app.models.marketplace_financial import (
    MarketplaceFinancialEvent,
    MarketplaceOrderFinancial,
)
from app.services.marketplace_financials import (
    ESPERA_INTERVALO_HORAS,
    ESPERA_MAX_DIAS,
    FinancialSnapshot,
    _persist_snapshot,
    _rodar_lote,
    falha_transitoria,
    run_due_marketplace_financial_retries,
    run_esteira_lenta_financials,
    run_ressuscitar_financials,
)

pytestmark = pytest.mark.asyncio


# Mensagens REAIS colhidas em produção em 15/09/2026.
TRANSITORIAS = [
    "Client error '403 ' for url 'https://sellingpartnerapi-na.amazon.com/finances/x'",
    "Client error '403 Forbidden' for url 'https://partner.shopeemobile.com/api/v2/x'",
    "Client error '429 Too Many Requests' for url 'https://api.mercadolibre.com/orders/1'",
    'ml_refresh_failed status=400 body={"message":"invalid client_id or client_secret"}',
    "missing refresh_token",
    'TikTok finance error: {"code":36009002,"message":"Too many requests"}',
    "TikTok settlement not available yet",
    "ML billing detail not posted yet",
    "Amazon finance transaction not posted yet",
    "Server error '503 Service Unavailable' for url 'x'",
    "ReadTimeout",
    "Connection reset by peer",
]

DEFINITIVAS = [
    "Client error '400 Bad Request' for url 'https://api.mercadolibre.com/billing/x'",
    "Client error '404 Not Found' for url 'x'",
    "financial adapter not implemented for magalu",
    "",
    None,
]


def test_classifica_falha_de_api_como_transitoria():
    for erro in TRANSITORIAS:
        assert falha_transitoria(erro) is True, erro
    for erro in DEFINITIVAS:
        assert falha_transitoria(erro) is False, erro


async def _integ(db, make_user, platform=IntegrationPlatform.SHOPEE, nome="poofy") -> Integration:
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=platform,
        name=nome,
        credentials=b"x",
        status="active",
    )
    db.add(integ)
    await db.flush()
    return integ


async def _persistir(db, integ, *, external_order_id, bling_id, erro, status="error"):
    return await _persist_snapshot(
        db,
        FinancialSnapshot(status=status, raw={}, error=erro),
        platform=integ.platform,
        integration=integ,
        store=None,
        bling_id=bling_id,
        pedido_bling=str(bling_id),
        external_order_id=external_order_id,
    )


async def test_403_nao_gasta_tentativa_e_vai_pra_esteira_lenta(db, make_user):
    """O sintoma original: oito 403 seguidos matavam o pedido."""
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    integ = await _integ(db, make_user)

    linha = None
    for _ in range(10):
        linha = await _persistir(
            db,
            integ,
            external_order_id="2609165JTSF5WS",
            bling_id=26900000001,
            erro="Client error '403 Forbidden' for url 'https://partner.shopeemobile.com/x'",
        )
    await db.commit()

    assert linha is not None
    # Dez quedas da API e o contador segue zerado: o teto de 8 fica intacto
    # para erro de verdade.
    assert linha.attempts == 0
    assert linha.espera_lenta is True
    # Continua na fila, em ritmo lento — nunca com next_retry_at nulo.
    assert linha.next_retry_at is not None
    horas = (linha.next_retry_at - datetime.now(UTC)).total_seconds() / 3600
    assert ESPERA_INTERVALO_HORAS - 1 < horas <= ESPERA_INTERVALO_HORAS


async def test_erro_de_verdade_continua_morrendo_na_oitava(db, make_user):
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    integ = await _integ(db, make_user)

    linha = None
    for _ in range(8):
        linha = await _persistir(
            db,
            integ,
            external_order_id="2609165G1DATXB",
            bling_id=26900000002,
            erro="Client error '400 Bad Request' for url 'x'",
        )
    await db.commit()

    assert linha is not None
    assert linha.attempts == 8
    assert linha.espera_lenta is False
    assert linha.next_retry_at is None


async def test_backlog_nao_rouba_a_vez_dos_pedidos_do_dia(db, make_user, monkeypatch):
    """A razão de existirem duas filas: 3.900 linhas velhas não podem ocupar as
    vagas do ciclo e deixar o pedido de hoje sem Frete na Margem."""
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    integ = await _integ(db, make_user)
    vencido = datetime.now(UTC) - timedelta(hours=2)

    db.add_all(
        [
            MarketplaceOrderFinancial(
                platform=IntegrationPlatform.SHOPEE,
                integration_id=integ.id,
                external_order_id=f"BACKLOG{i}",
                bling_id=26800000000 + i,
                status="error",
                attempts=0,
                espera_lenta=True,
                next_retry_at=vencido,
                last_error="Client error '403 Forbidden' for url 'x'",
            )
            for i in range(200)
        ]
    )
    db.add(
        MarketplaceOrderFinancial(
            platform=IntegrationPlatform.SHOPEE,
            integration_id=integ.id,
            external_order_id="PEDIDO-DE-HOJE",
            bling_id=26999999999,
            status="pending",
            attempts=1,
            espera_lenta=False,
            next_retry_at=vencido,
            last_error="Shopee net payout not available yet",
        )
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

    rapida = await run_due_marketplace_financial_retries(db, limit=100)

    assert rapida["queued"] == 1
    assert chamados == [26999999999]


async def test_ressuscita_so_quem_morreu_por_falha_de_api(db, make_user):
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    integ = await _integ(db, make_user)

    db.add_all(
        [
            # Morto por queda da API, recente: volta.
            MarketplaceOrderFinancial(
                platform=IntegrationPlatform.SHOPEE,
                integration_id=integ.id,
                external_order_id="VOLTA",
                bling_id=26910000001,
                status="error",
                attempts=8,
                next_retry_at=None,
                last_error="Client error '403 Forbidden' for url 'x'",
            ),
            # Erro de verdade: fica fora.
            MarketplaceOrderFinancial(
                platform=IntegrationPlatform.SHOPEE,
                integration_id=integ.id,
                external_order_id="FICA-FORA",
                bling_id=26910000002,
                status="error",
                attempts=8,
                next_retry_at=None,
                last_error="Client error '404 Not Found' for url 'x'",
            ),
            # Já resolvido: não é status de retry, fica fora.
            MarketplaceOrderFinancial(
                platform=IntegrationPlatform.SHOPEE,
                integration_id=integ.id,
                external_order_id="JA-PAGO",
                bling_id=26910000003,
                status="posted",
                attempts=1,
                next_retry_at=None,
                last_error=None,
            ),
        ]
    )
    await db.commit()

    # Pedido antigo demais (fora da janela) não volta: envelheceu o created_at
    # direto no banco porque a coluna é server_default now().
    velho = MarketplaceOrderFinancial(
        platform=IntegrationPlatform.SHOPEE,
        integration_id=integ.id,
        external_order_id="ANTIGO",
        bling_id=26910000004,
        status="error",
        attempts=8,
        next_retry_at=None,
        last_error="Client error '403 Forbidden' for url 'x'",
    )
    db.add(velho)
    await db.commit()
    await db.execute(
        text(
            "UPDATE marketplace_order_financials SET created_at = now() - "
            "make_interval(days => :dias) WHERE external_order_id = 'ANTIGO'"
        ),
        {"dias": ESPERA_MAX_DIAS + 10},
    )
    await db.commit()

    resumo = await run_ressuscitar_financials(db)

    assert resumo["revividos"] == 1

    linhas = {
        row.external_order_id: row
        for row in (
            await db.execute(select(MarketplaceOrderFinancial))
        ).scalars().all()
    }
    assert linhas["VOLTA"].next_retry_at is not None
    assert linhas["VOLTA"].espera_lenta is True
    assert linhas["FICA-FORA"].next_retry_at is None
    assert linhas["JA-PAGO"].next_retry_at is None
    assert linhas["ANTIGO"].next_retry_at is None


async def test_ressurreicao_e_idempotente(db, make_user):
    """Roda de hora em hora: o segundo tick não pode reempurrar o que já voltou."""
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    integ = await _integ(db, make_user)
    db.add(
        MarketplaceOrderFinancial(
            platform=IntegrationPlatform.SHOPEE,
            integration_id=integ.id,
            external_order_id="UNICO",
            bling_id=26920000001,
            status="error",
            attempts=8,
            next_retry_at=None,
            last_error="Client error '403 Forbidden' for url 'x'",
        )
    )
    await db.commit()

    assert (await run_ressuscitar_financials(db))["revividos"] == 1
    assert (await run_ressuscitar_financials(db))["revividos"] == 0


async def test_um_pedido_com_erro_nao_derruba_o_resto_do_lote(db, make_user, monkeypatch):
    """A fila existe para linhas que já falharam. Com um commit único no fim,
    um erro no meio jogava fora o trabalho de todo o lote."""
    await _integ(db, make_user)
    vistos: list[int] = []

    async def fake_sync(session, *, bling_order_id, **kw):
        vistos.append(bling_order_id)
        if bling_order_id == 2:
            raise RuntimeError("banco caiu nesta linha")
        return {"ok": True, "status": "posted"}

    monkeypatch.setattr(
        "app.services.marketplace_financials.run_sync_marketplace_financials_for_bling_order",
        fake_sync,
    )

    resumo = await _rodar_lote(db, [1, 2, 3], trigger="esteira_lenta")

    assert vistos == [1, 2, 3]
    assert resumo == {"queued": 3, "ok": 2, "error": 1}


async def _persistir_sucesso(db, integ, *, external_order_id, bling_id, frete, taxa):
    from decimal import Decimal

    from app.services.marketplace_financials import FinancialEventDraft

    return await _persist_snapshot(
        db,
        FinancialSnapshot(
            status="posted",
            raw={"escrow": "ok"},
            error=None,
            gross_amount=Decimal("1000.00"),
            fee_amount=Decimal(str(taxa)),
            freight_amount=Decimal(str(frete)),
            net_amount=Decimal("900.00"),
            events=[
                FinancialEventDraft(event_type="freight", amount=Decimal(str(frete))),
                FinancialEventDraft(event_type="fee", amount=Decimal(str(taxa))),
            ],
        ),
        platform=integ.platform,
        integration=integ,
        store=None,
        bling_id=bling_id,
        pedido_bling=str(bling_id),
        external_order_id=external_order_id,
    )


async def test_403_nao_apaga_o_frete_que_ja_estava_na_tela(db, make_user):
    """O pior sintoma do incidente: não era 'não preencheu', era dado bom
    destruído. Um pedido que mostrava Frete R$ 23,87 passou a mostrar R$ 0,00
    depois que a Shopee recusou."""
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    integ = await _integ(db, make_user)

    linha = await _persistir_sucesso(
        db, integ, external_order_id="COM-FRETE", bling_id=26930000001,
        frete="23.87", taxa="120.00",
    )
    await db.commit()
    assert float(linha.freight_amount) == 23.87

    # Agora a Shopee cai.
    linha = await _persistir(
        db, integ, external_order_id="COM-FRETE", bling_id=26930000001,
        erro="Client error '403 Forbidden' for url 'https://partner.shopeemobile.com/x'",
    )
    await db.commit()

    # O valor continua na tela; só o status e o erro mudam.
    assert float(linha.freight_amount) == 23.87
    assert float(linha.fee_amount) == 120.00
    assert float(linha.net_amount) == 900.00
    assert linha.status == "error"
    assert linha.espera_lenta is True
    assert (linha.raw or {}).get("ultima_falha", "").startswith("Client error")
    # E os eventos, que são o que a tela de Margem soma, seguem lá.
    eventos = (
        await db.execute(
            select(MarketplaceFinancialEvent).where(
                MarketplaceFinancialEvent.order_financial_id == linha.id
            )
        )
    ).scalars().all()
    assert len(eventos) == 2


async def test_tentativas_contam_falha_seguida_e_nao_sincronizacao(db, make_user):
    """Antes `attempts` subia também no sucesso e nunca zerava: pedido
    re-sincronizado 8 vezes chegava no teto sem nunca ter falhado, e morria na
    fila no primeiro erro de verdade."""
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    integ = await _integ(db, make_user)

    linha = None
    for _ in range(9):
        linha = await _persistir_sucesso(
            db, integ, external_order_id="SAUDAVEL", bling_id=26940000001,
            frete="10.00", taxa="50.00",
        )
    await db.commit()
    assert linha is not None
    assert linha.attempts == 0

    # Primeiro erro DE VERDADE: ainda tem fila, não morre de cara.
    linha = await _persistir(
        db, integ, external_order_id="SAUDAVEL", bling_id=26940000001,
        erro="Client error '400 Bad Request' for url 'x'",
    )
    await db.commit()
    assert linha.attempts == 1
    assert linha.next_retry_at is not None


async def test_depois_de_45_dias_de_api_fora_o_pedido_sai_da_fila(db, make_user):
    """Falha de API não gasta tentativa — então, sem uma saída explícita, o
    pedido velho voltaria pra fila RÁPIDA com o contador baixo e tentaria de 30
    em 30 minutos para sempre, roubando a vez dos pedidos do dia."""
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    integ = await _integ(db, make_user)

    linha = await _persistir(
        db, integ, external_order_id="VELHO", bling_id=26950000001,
        erro="Client error '403 Forbidden' for url 'x'",
    )
    await db.commit()
    await db.execute(
        text(
            "UPDATE marketplace_order_financials SET created_at = now() - "
            "make_interval(days => :dias) WHERE external_order_id = 'VELHO'"
        ),
        {"dias": ESPERA_MAX_DIAS + 3},
    )
    await db.commit()
    await db.refresh(linha)

    linha = await _persistir(
        db, integ, external_order_id="VELHO", bling_id=26950000001,
        erro="Client error '403 Forbidden' for url 'x'",
    )
    await db.commit()

    assert linha.espera_lenta is False
    assert linha.next_retry_at is None


async def test_tiktok_lento_nao_monopoliza_a_esteira(db, make_user, monkeypatch):
    """Medido em produção: uma consulta ao financeiro do TikTok leva ~37s e ele
    sozinho tem mais de mil pedidos esperando settlement. Numa fila só por
    antiguidade, a Shopee e o ML nunca chegavam a ser consultados."""
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    user = await make_user()
    vencido = datetime.now(UTC) - timedelta(hours=2)

    integs = {}
    for plataforma, nome in (
        (IntegrationPlatform.TIKTOK, "poofy-tt"),
        (IntegrationPlatform.SHOPEE, "minas"),
    ):
        integ = Integration(
            user_id=user.id, platform=plataforma, name=nome,
            credentials=b"x", status="active",
        )
        db.add(integ)
        await db.flush()
        integs[plataforma] = integ

    linhas = [
        MarketplaceOrderFinancial(
            platform=IntegrationPlatform.TIKTOK,
            integration_id=integs[IntegrationPlatform.TIKTOK].id,
            external_order_id=f"TT{i}",
            bling_id=27000000000 + i,
            status="pending",
            espera_lenta=True,
            # Mais antigos: numa fila por antiguidade viriam TODOS na frente.
            next_retry_at=vencido - timedelta(days=5),
            last_error="TikTok settlement not available yet",
        )
        for i in range(60)
    ]
    linhas += [
        MarketplaceOrderFinancial(
            platform=IntegrationPlatform.SHOPEE,
            integration_id=integs[IntegrationPlatform.SHOPEE].id,
            external_order_id=f"SP{i}",
            bling_id=27100000000 + i,
            status="error",
            espera_lenta=True,
            next_retry_at=vencido,
            last_error="Client error '403 Forbidden' for url 'x'",
        )
        for i in range(60)
    ]
    db.add_all(linhas)
    await db.commit()

    vistos: list[int] = []

    async def fake_sync(session, *, bling_order_id, **kw):
        vistos.append(bling_order_id)
        return {"ok": True, "status": "posted"}

    monkeypatch.setattr(
        "app.services.marketplace_financials.run_sync_marketplace_financials_for_bling_order",
        fake_sync,
    )

    await run_esteira_lenta_financials(db, limit=40)

    shopee_vistos = [b for b in vistos if b >= 27100000000]
    tiktok_vistos = [b for b in vistos if b < 27100000000]
    assert shopee_vistos, "Shopee ficou de fora — o TikTok monopolizou a esteira"
    assert tiktok_vistos, "TikTok ficou de fora"
    # E a Shopee aparece cedo, não depois de toda a fila do TikTok: se o job for
    # cortado no meio, ela já foi atendida.
    assert vistos.index(shopee_vistos[0]) <= 2
