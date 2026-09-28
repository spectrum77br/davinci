"""Venda cancelada no ML não pode ficar com o líquido cheio na Margem.

Vinicius, 28/09/2026 — pedido Bling 296576 / ML 2000014989416509 (jlas2
clássico): "no mercado livre ta sem valor nenhum foi cancelado mas no margem
fica valor cheio ainda" (Saldo Plataforma = Efetivo = R$ 2.107,98, 23,7%).
O `total_amount` do pedido não muda quando o ML cancela, e `_fetch_ml` fazia
bruto − comissão − frete sem olhar o cancelamento nem o estorno.

Agora: venda desfeita (cancelada ou estornada por inteiro) → líquido R$ 0,00
(a Margem mostra −100%, escolha do Vinicius); estorno parcial sai do líquido;
`bpp_covered` (o ML pagou o comprador do bolso dele) não mexe no nosso saldo.
A varredura `run_ml_cancelados_resync` relê o que foi gravado com a conta
antiga.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

from app.models import BlingOrder, IntegrationPlatform, Logistica
from app.models.integration import Integration
from app.models.marketplace_financial import MarketplaceOrderFinancial
from app.services import marketplace_financials as mf
from app.services.marketplace_financials import _fetch_ml

PEDIDO = "2000014989416509"
ENVIO = "45000000001"
SELLER = "1593540211"


def _order(
    order_id: str = PEDIDO,
    *,
    status: str = "paid",
    price: str = "2400.00",
    fee: str = "264.12",
    payments: list[dict] | None = None,
    pack_id: str | None = None,
    item_id: str = "MLB4000000001",
    sku: str = "dg090.sp+a001.sp",
) -> dict:
    return {
        "id": int(order_id),
        "status": status,
        "pack_id": int(pack_id) if pack_id else None,
        "total_amount": float(price),
        "paid_amount": float(price),
        "currency_id": "BRL",
        "tags": ["paid"],
        "seller": {"id": int(SELLER)},
        "shipping": {"id": int(ENVIO)},
        "order_items": [
            {
                "item": {"id": item_id, "seller_sku": sku, "title": "Oukitel WP60"},
                "quantity": 1,
                "unit_price": float(price),
                "sale_fee": float(fee),
            }
        ],
        "payments": payments
        if payments is not None
        else [{"status": "approved", "shipping_cost": 0.0, "transaction_amount": float(price)}],
    }


def _estornado(valor: str, detail: str = "bpp_refunded", status: str = "refunded") -> list[dict]:
    return [
        {
            "status": status,
            "status_detail": detail,
            "shipping_cost": 0.0,
            "transaction_amount": 2400.0,
            "transaction_amount_refunded": float(valor),
            "date_last_modified": "2026-09-20T10:00:00.000-04:00",
        }
    ]


class _FakeML:
    creds = {"user_id": SELLER}

    def __init__(
        self, orders: dict[str, dict], *, pack: list[str] | None = None, billing: bool = True
    ):
        self.orders = orders
        self.pack = pack
        self.billing = billing

    async def get_order(self, order_id: str) -> dict:
        return self.orders[str(order_id)]

    async def get_pack(self, pack_id: str) -> dict:
        return {"id": int(pack_id), "orders": [{"id": int(o)} for o in self.pack or []]}

    async def get_billing_order_details(self, order_id: str) -> dict:
        return {"results": [{"details": []}]} if self.billing else {"results": []}

    async def get_order_discounts(self, order_id: str) -> dict:
        return {"details": []}

    async def get_shipment_costs(self, shipping_id: str) -> dict:
        return {"senders": [{"cost": 27.90, "user_id": int(SELLER)}], "receiver": {"cost": 0}}

    async def get_shipment_items(self, shipping_id: str) -> list:
        return [
            {"item_id": o["order_items"][0]["item"]["id"], "order_id": oid, "quantity": 1,
             "dimensions": {"width": 10, "height": 10, "length": 10, "weight": 500}}
            for oid, o in self.orders.items()
        ]

    async def get_free_shipping_options(self, seller_id, item_id: str) -> dict:
        return {"coverage": {"all_country": {"list_cost": 26.00, "currency_id": "BRL"}}}


async def test_venda_de_pe_nao_muda():
    snap = await _fetch_ml(_FakeML({PEDIDO: _order()}), PEDIDO)
    assert snap.net_amount == Decimal("2107.98")  # 2400 − 264,12 − 27,90
    assert snap.refund_amount is None
    assert snap.raw["ml_cancelamento"] == {"desfeitas": [], "estorno": None}
    assert "refund" not in {e.event_type for e in snap.events}


async def test_venda_cancelada_fica_com_liquido_zero():
    """O caso do 296576: cancelada no ML, pagamento estornado ao comprador."""
    order = _order(status="cancelled", payments=_estornado("2400.00"))
    snap = await _fetch_ml(_FakeML({PEDIDO: order}, billing=False), PEDIDO)

    assert snap.net_amount == Decimal("0.00")
    assert snap.gross_amount == Decimal("2400.00")
    assert snap.fee_amount == Decimal("0")
    assert snap.freight_amount == Decimal("0")
    assert snap.discount_amount is None
    assert snap.refund_amount == Decimal("2400.00")
    # Valor final: nada de "aguardando billing" nem retentativa.
    assert snap.status == "posted"
    assert snap.error is None
    assert snap.raw["ml_cancelamento"] == {"desfeitas": [PEDIDO], "estorno": "2400.00"}
    eventos = {e.event_type: e.amount for e in snap.events}
    assert eventos["sale"] == Decimal("2400.00")
    assert eventos["refund"] == Decimal("-2400.00")
    assert "commission_fee" not in eventos and "freight" not in eventos
    # A conta do Raio-X fecha: bruto − taxas − frete − reembolso = líquido.
    assert (
        snap.gross_amount - snap.fee_amount - snap.freight_amount - snap.refund_amount
        == snap.net_amount
    )


async def test_cancelada_sem_pagamento_aprovado_tambem_zera():
    order = _order(
        status="cancelled", payments=[{"status": "cancelled", "transaction_amount": 2400.0}]
    )
    snap = await _fetch_ml(_FakeML({PEDIDO: order}), PEDIDO)
    assert snap.net_amount == Decimal("0.00")


async def test_estorno_total_com_pedido_ainda_nao_cancelado_ja_zera():
    order = _order(status="paid", payments=_estornado("2400.00"))
    snap = await _fetch_ml(_FakeML({PEDIDO: order}), PEDIDO)
    assert snap.net_amount == Decimal("0.00")
    assert snap.raw["ml_cancelamento"]["desfeitas"] == [PEDIDO]


async def test_ml_cobriu_o_estorno_nao_mexe_no_nosso_saldo():
    """bpp_covered = o ML pagou o comprador do bolso dele (285250)."""
    order = _order(status="cancelled", payments=_estornado("2400.00", detail="bpp_covered"))
    snap = await _fetch_ml(_FakeML({PEDIDO: order}), PEDIDO)
    assert snap.net_amount == Decimal("2107.98")
    assert snap.refund_amount is None


async def test_estorno_parcial_sai_do_liquido():
    order = _order(
        status="paid",
        payments=_estornado("300.00", detail="partially_refunded", status="approved"),
    )
    snap = await _fetch_ml(_FakeML({PEDIDO: order}), PEDIDO)
    assert snap.refund_amount == Decimal("300.00")
    assert snap.net_amount == Decimal("1807.98")
    assert snap.raw["ml_cancelamento"] == {"desfeitas": [], "estorno": "300.00"}


async def test_pack_com_um_irmao_cancelado_tira_so_a_parte_dele():
    pack = "2000009999999999"
    irmao = "2000014989416510"
    orders = {
        PEDIDO: _order(PEDIDO, pack_id=pack, price="1000.00", fee="110.00"),
        irmao: _order(
            irmao, status="cancelled", pack_id=pack, price="500.00", fee="55.00",
            item_id="MLB4000000002", sku="b001.24", payments=_estornado("500.00"),
        ),
    }
    client = _FakeML(orders, pack=[PEDIDO, irmao])
    snap = await _fetch_ml(client, PEDIDO, bling_skus={"dg090.sp+a001.sp", "b001.24"})
    # Sem o cancelamento: 1500 − 165 − 27,90 = 1307,10. O irmão cancelado leva
    # o que ele somou (500 − 55).
    assert snap.refund_amount == Decimal("445.00")
    assert snap.net_amount == Decimal("862.10")
    assert snap.raw["ml_cancelamento"]["desfeitas"] == [irmao]


# --- Varredura ------------------------------------------------------------


async def _integ(db, make_user) -> Integration:
    user = await make_user()
    integ = Integration(
        user_id=user.id, platform=IntegrationPlatform.ML, name="jlas2",
        credentials=b"x", status="active",
    )
    db.add(integ)
    await db.flush()
    return integ


def _mof(integ, bling_id: int, *, raw: dict, fetched_at: datetime | None):
    return MarketplaceOrderFinancial(
        platform=IntegrationPlatform.ML,
        integration_id=integ.id,
        bling_id=bling_id,
        pedido_bling=str(bling_id),
        external_order_id=f"ext-{bling_id}",
        status="posted",
        net_amount=Decimal("100.00"),
        raw=raw,
        fetched_at=fetched_at,
    )


async def test_varredura_rele_so_quem_precisa(db, make_user, monkeypatch):
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    integ = await _integ(db, make_user)
    agora = datetime.now(UTC)
    antigo = agora - timedelta(days=2)
    recente = agora - timedelta(hours=1)
    cancelado_no_ml = {"order": {"status": "cancelled"}}
    ja_corrigido = {
        "order": {"status": "cancelled"},
        "ml_cancelamento": {"desfeitas": ["x"], "estorno": "1"},
    }
    visto_de_pe = {
        "order": {"status": "paid"},
        "ml_cancelamento": {"desfeitas": [], "estorno": None},
    }

    db.add_all([
        # 1: gravado com a conta antiga, cancelado no ML → relê.
        _mof(integ, 29657601, raw=cancelado_no_ml, fetched_at=antigo),
        # 2: já relido com a conta nova e zerado → não relê.
        _mof(integ, 29657602, raw=ja_corrigido, fetched_at=antigo),
        # 3: conta antiga, pedido de pé no raw, mas Cancelado no Bling → relê.
        _mof(integ, 29657603, raw={"order": {"status": "paid"}}, fetched_at=antigo),
        # 4: conta nova viu de pé há 2 dias; Logística diz cancelado → relê.
        _mof(integ, 29657604, raw=visto_de_pe, fetched_at=antigo),
        # 5: igual ao 4, mas lido há 1 h → espera o dia seguinte.
        _mof(integ, 29657605, raw=visto_de_pe, fetched_at=recente),
        # 6: conta antiga sem sinal de cancelamento → não relê.
        _mof(integ, 29657606, raw={"order": {"status": "paid"}}, fetched_at=antigo),
    ])
    db.add(BlingOrder(
        bling_id=29657603, numero="29657603", item_codigo="a", item_index=0, situacao="12",
    ))
    for numero in ("29657604", "29657605"):
        db.add(Logistica(
            pedido_bling=numero, plataforma="Mercado Livre",
            meli_status={"order_status": "cancelled"},
        ))
    await db.commit()

    vistos: list[int] = []

    async def _fake_lote(session, bling_ids, *, trigger, limite_s=None):
        vistos.extend(bling_ids)
        assert trigger == mf.ML_CANCELADOS_TRIGGER
        return {"vistos": len(bling_ids), "ok": len(bling_ids), "error": 0}

    monkeypatch.setattr(mf, "_rodar_lote", _fake_lote)
    await mf.run_ml_cancelados_resync(db)

    assert sorted(vistos) == [29657601, 29657603, 29657604]
