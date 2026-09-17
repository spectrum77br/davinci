# ruff: noqa: E501
"""Coluna "Reembolso" da aba Acompanhamento (Vinicius 17/09: "o importante é
saber se estamos com o dinheiro ainda ou se já devolveu para o cliente").

Sim = já saiu dinheiro do NOSSO; Não = ainda com a gente (inclui a plataforma
pagando do próprio bolso). Fontes: o caso no marketplace (TikTok/Shopee/ML)
cruzado com o extrato financeiro já baixado (escrow/statement).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import DevolucaoRastreio, IntegrationPlatform, Logistica, MarketplaceOrderFinancial
from app.services import devolucao_rastreio_sync as svc
from app.services import logistica_meli, logistica_shopee, logistica_tiktok
from app.services.devolucao_returns import ReturnInfo

pytestmark = pytest.mark.asyncio


# --- TikTok ---------------------------------------------------------------

def test_tiktok_caso_concluido_e_reembolso_pago_mesmo_com_outro_cancelado():
    # 294865 real: um caso RETURN_AND_REFUND cancelado + um REFUND concluído.
    casos = [
        {"return_status": "RETURN_OR_REFUND_REQUEST_CANCEL", "refund_amount": {"refund_total": "744"}, "update_time": 1789093699},
        {"return_status": "RETURN_OR_REFUND_REQUEST_COMPLETE", "refund_amount": {"refund_total": "744"}, "update_time": 1789525794},
    ]
    pago, valor, em, detalhe = logistica_tiktok._reembolso_do_pedido(casos)
    assert pago is True and valor == Decimal("744")
    assert em == datetime.fromtimestamp(1789525794, tz=UTC)
    assert "TikTok" in (detalhe or "")


def test_tiktok_caso_vivo_ou_cancelado_nao_e_reembolso():
    vivo = [{"return_status": "BUYER_SHIPPED_ITEM", "refund_amount": {"refund_total": "100"}, "update_time": 1}]
    assert logistica_tiktok._reembolso_do_pedido(vivo) == (False, None, None, None)
    cancelado = [{"return_status": "RETURN_OR_REFUND_REQUEST_CANCEL", "refund_amount": {"refund_total": "100"}}]
    assert logistica_tiktok._reembolso_do_pedido(cancelado) == (False, None, None, None)
    assert logistica_tiktok._reembolso_do_pedido([None, "lixo"]) == (False, None, None, None)


# --- Shopee ---------------------------------------------------------------

def test_shopee_accepted_e_reembolso_pago_processing_nao():
    d = {"status": "ACCEPTED", "refund_amount": 525, "update_time": 1788439609, "return_sn": "26090302DRUDPB0"}
    info = logistica_shopee._return_info(d, "ACCEPTED")
    assert info.reembolso is True and info.reembolso_valor == Decimal("525")
    assert info.reembolso_em == datetime.fromtimestamp(1788439609, tz=UTC)
    em_transito = logistica_shopee._return_info({"status": "PROCESSING", "refund_amount": 1619, "update_time": 1}, "PROCESSING")
    assert em_transito.reembolso is False and em_transito.reembolso_valor is None


# --- Mercado Livre --------------------------------------------------------

def _order_ml(*payments, mediations=()):
    return {"status": "partially_refunded", "payments": list(payments), "mediations": [{"id": m} for m in mediations]}


def test_ml_estorno_parcial_via_bpp_tambem_saiu_do_nosso():
    # 296695 real: mediação fechada a favor do comprador, R$ 52,97 estornados
    # do pagamento ("partially_bpp_refunded") — dinheiro da venda que voltou.
    o = _order_ml(
        {"transaction_amount_refunded": 0.0, "status_detail": "accredited", "date_last_modified": "2026-09-16T15:16:22.000-04:00"},
        {"transaction_amount_refunded": 52.97, "status_detail": "partially_bpp_refunded", "date_last_modified": "2026-09-16T15:16:21.000-04:00"},
    )
    r = logistica_meli._reembolso_ml([o])
    assert r.pago is True and r.valor == Decimal("52.97")
    assert r.em == datetime(2026, 9, 16, 19, 16, 21, tzinfo=UTC)
    assert "Mercado Livre" in (r.detalhe or "")


def test_ml_estorno_sem_cobertura_saiu_do_nosso_e_sem_estorno_nao():
    o = _order_ml({"transaction_amount_refunded": 278.81, "status_detail": "refunded", "date_last_modified": "2026-09-16T10:00:00.000-03:00"})
    r = logistica_meli._reembolso_ml([o])
    assert r.pago is True and r.valor == Decimal("278.81")
    assert r.em == datetime(2026, 9, 16, 13, 0, tzinfo=UTC)
    assert logistica_meli._reembolso_ml([_order_ml({"transaction_amount_refunded": 0})]) == (False, None, None, None)
    assert logistica_meli._reembolso_ml([{}]) == (False, None, None, None)


def test_ml_so_reembolso_carrega_o_claim_e_o_reembolso():
    info = logistica_meli._so_reembolso(logistica_meli._ReembolsoML(True, Decimal("10"), None, "x"), "5578079870")
    assert info.fonte == "ml" and info.status is None and info.tracking is None
    assert info.return_id == "5578079870" and info.reembolso is True and info.reembolso_valor == Decimal("10")


# --- Cruzamento com o extrato (sync) --------------------------------------

def _info(**kw) -> ReturnInfo:
    base = {"fonte": "shopee", "status": "PROCESSING", "tracking": None, "carrier": None, "created_at": None, "updated_at": None}
    base.update(kw)
    return ReturnInfo(**base)


def test_extrato_desconto_manda_sobre_o_caso_em_analise():
    row = DevolucaoRastreio(pedido_bling="1")
    ext = svc._Extrato("shopee", Decimal("833.00"), None, None, datetime(2026, 9, 17, tzinfo=UTC))
    svc._aplicar_reembolso(row, _info(reembolso=False), ext)
    assert row.reembolso_auto is True and row.reembolso_valor_auto == Decimal("833.00")
    assert row.reembolso_em_auto == datetime(2026, 9, 17, tzinfo=UTC)
    assert "extrato" in (row.reembolso_detalhe_auto or "")


def test_compensacao_da_shopee_vira_nao():
    row = DevolucaoRastreio(pedido_bling="1")
    ext = svc._Extrato("shopee", Decimal("779"), Decimal("779"), datetime(2026, 9, 10, tzinfo=UTC), None)
    svc._aplicar_reembolso(row, _info(status="ACCEPTED", reembolso=True, reembolso_valor=Decimal("779")), ext)
    assert row.reembolso_auto is False
    assert "compensou" in (row.reembolso_detalhe_auto or "")


def test_sem_extrato_vale_o_caso_e_sem_nada_nao_mexe():
    row = DevolucaoRastreio(pedido_bling="1", reembolso_auto=True, reembolso_valor_auto=Decimal("5"))
    svc._aplicar_reembolso(row, _info(reembolso=None), None)  # plataforma não disse nada
    assert row.reembolso_auto is True and row.reembolso_valor_auto == Decimal("5")
    em = datetime(2026, 9, 16, tzinfo=UTC)
    svc._aplicar_reembolso(row, _info(fonte="tiktok", reembolso=True, reembolso_valor=Decimal("744"), reembolso_em=em, reembolso_detalhe="TikTok"), None)
    assert (row.reembolso_auto, row.reembolso_valor_auto, row.reembolso_em_auto, row.reembolso_detalhe_auto) == (True, Decimal("744"), em, "TikTok")
    svc._aplicar_reembolso(row, _info(reembolso=False), None)
    assert row.reembolso_auto is False and row.reembolso_valor_auto is None


def test_compensacao_shopee_lida_do_escrow_guardado():
    raw = {"escrow": {"order_income": {"order_adjustment": [
        {"adjustment_reason": "Logistics Related Compensation", "amount": 779, "date": 1789000000},
        {"adjustment_reason": "Ajuste após reembolso aprovado", "amount": -779, "date": 1789000100},
    ]}}}
    total, em = svc._compensacao_shopee(raw)
    assert total == Decimal("779") and em == datetime.fromtimestamp(1789000000, tz=UTC)
    assert svc._compensacao_shopee({"escrow": {"order_income": {}}}) == (None, None)
    assert svc._compensacao_shopee(None) == (None, None)


async def test_sync_grava_o_reembolso_e_cria_linha_so_com_extrato(db, monkeypatch):
    """Rodada do sync: o caso do TikTok diz reembolso pago (grava valor/data);
    um pedido Shopee sem caso conhecido mas com desconto no extrato ganha linha
    com Sim — pacote voltando pela SPX/cancelamento também tira dinheiro."""
    p_tt, p_sh = f"7{uuid4().hex[:6]}", f"8{uuid4().hex[:6]}"
    db.add_all([
        Logistica(pedido_bling=p_tt, plataforma="TikTok", pedido_marketplace=f"mk-{p_tt}", conta="x"),
        Logistica(pedido_bling=p_sh, plataforma="Shopee", pedido_marketplace=f"mk-{p_sh}", conta="x"),
        MarketplaceOrderFinancial(
            platform=IntegrationPlatform.SHOPEE, external_order_id=f"mk-{p_sh}", pedido_bling=p_sh,
            status="posted", refund_amount=Decimal("-470.00"), raw={"escrow": {}},
            fetched_at=datetime(2026, 9, 17, 10, 0, tzinfo=UTC),
        ),
    ])
    await db.commit()

    em = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)

    async def _tiktok(session, linhas):
        return {p_tt: _info(fonte="tiktok", status="RETURN_OR_REFUND_REQUEST_COMPLETE", reembolso=True,
                            reembolso_valor=Decimal("744"), reembolso_em=em, reembolso_detalhe="Caso concluído no TikTok")}

    async def _nada(session, linhas, **kw):
        return {}

    async def _register(numbers):
        return {"ok": list(numbers), "sem_quota": False}

    async def _fetch(numbers):
        return {"info": {}, "desconhecidos": []}

    monkeypatch.setattr(logistica_tiktok, "returns_por_pedido", _tiktok, raising=False)
    monkeypatch.setattr(logistica_shopee, "returns_por_pedido", _nada, raising=False)
    monkeypatch.setattr(svc.logistica_track, "register", _register)
    monkeypatch.setattr(svc.logistica_track, "fetch_detalhado", _fetch)

    out = await svc.run(db, pedidos=[p_tt, p_sh])

    assert out["reembolsos"] == 2
    rows = {r.pedido_bling: r for r in (await db.execute(select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling.in_([p_tt, p_sh])))).scalars().all()}
    assert rows[p_tt].reembolso_auto is True and rows[p_tt].reembolso_valor_auto == Decimal("744") and rows[p_tt].reembolso_em_auto == em
    assert rows[p_sh].reembolso_auto is True and rows[p_sh].reembolso_valor_auto == Decimal("470.00")
    assert rows[p_sh].reembolso_em_auto == datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
    assert rows[p_sh].devolucao_status_auto is None  # sem caso: só o reembolso
