"""`logistica_tiktok.sweep_pos_venda` × returns API: grava `return_status` E
`return_type` na assinatura, e "vale o vivo mais recente" continua valendo.

Caso real 294865 (10/09): devolução cancelada pelo cliente + caso SÓ
reembolso um minuto depois. Sem o tipo, o painel dizia "Devolução
solicitada" e o Bling foi pra Aguardando Devolução — sem pacote nenhum vindo.
Client falso (sem HTTP) + linhas Logistica no banco de teste."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.models import Logistica
from app.services import logistica_rules, logistica_tiktok

ORDER = "585930105453577483"


class FakeTikTokSweep:
    """Só o que o sweep usa: `get_order_status_map` (status vivo do pedido) e
    `get_return_list` em modo janela (`update_time_from/to`)."""

    def __init__(self, returns: list[dict], *, status: str = "COMPLETED"):
        self._returns = returns
        self._status = status

    async def get_order_status_map(self, order_ids):
        return {str(o): {"status": self._status, "update_time": 1789525794} for o in order_ids}

    async def get_return_list(self, *, order_ids=None, update_time_from=None, update_time_to=None):
        assert order_ids is None and update_time_from is not None
        return list(self._returns)


def _patch(monkeypatch, fake: FakeTikTokSweep) -> None:
    async def _integ(session, conta):
        return conta

    monkeypatch.setattr(logistica_tiktok, "_tiktok_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_tiktok, "_build_tiktok_client", lambda s, i, *, lock=None: fake)


def _caso(status: str, tipo: str, *, update_time: int, return_id: str) -> dict:
    return {
        "order_id": ORDER,
        "return_id": return_id,
        "return_status": status,
        "return_type": tipo,
        "create_time": update_time - 10,
        "update_time": update_time,
    }


async def _seed(db, meli_status: dict) -> Logistica:
    row = Logistica(
        plataforma="TikTok", conta="mini", pedido_bling="294865", pedido_marketplace=ORDER,
        data=date.today(), status_bling="Entregue", meli_status=meli_status,
    )
    db.add(row)
    await db.commit()
    return (await db.execute(select(Logistica))).scalar_one()


@pytest.mark.asyncio
async def test_grava_status_e_tipo_do_caso_vivo_mais_recente(db, monkeypatch):
    """Devolução cancelada (23:28) + só-reembolso pendente (23:29): vale o
    vivo → assinatura "Reembolso solicitado", não "Devolução solicitada"."""
    row = await _seed(db, {"order_status": "DELIVERED"})
    fake = FakeTikTokSweep(
        [
            _caso("RETURN_OR_REFUND_REQUEST_CANCEL", "RETURN_AND_REFUND", update_time=1789093699, return_id="R1"),
            _caso("RETURN_OR_REFUND_REQUEST_PENDING", "REFUND", update_time=1789093753, return_id="R2"),
        ],
        status="DELIVERED",
    )
    _patch(monkeypatch, fake)

    out = await logistica_tiktok.sweep_pos_venda(db)

    await db.refresh(row)
    assert out["returns"] == 1 and row.id in out["ids"]
    assert row.meli_status == {
        "order_status": "DELIVERED",
        "return_status": "RETURN_OR_REFUND_REQUEST_PENDING",
        "return_type": "REFUND",
    }
    assert logistica_rules.assinatura_para(row.plataforma, row.meli_status) == "Reembolso solicitado"


@pytest.mark.asyncio
async def test_linha_antiga_sem_tipo_ganha_o_tipo_sem_mudar_o_status(db, monkeypatch):
    """Linha gravada antes do tipo existir: o status é o mesmo, mas o sweep
    completa o `return_type` (senão a assinatura ficaria errada pra sempre)."""
    row = await _seed(
        db, {"order_status": "COMPLETED", "return_status": "RETURN_OR_REFUND_REQUEST_COMPLETE"}
    )
    fake = FakeTikTokSweep(
        [_caso("RETURN_OR_REFUND_REQUEST_COMPLETE", "REFUND", update_time=1789525794, return_id="R2")]
    )
    _patch(monkeypatch, fake)

    out = await logistica_tiktok.sweep_pos_venda(db)

    await db.refresh(row)
    assert out["returns"] == 1 and out["order_status"] == 0
    assert row.meli_status["return_type"] == "REFUND"
    assert logistica_rules.assinatura_para("TikTok", row.meli_status) == "Reembolso pago sem devolução"


@pytest.mark.asyncio
async def test_nada_mudou_nao_marca_a_linha(db, monkeypatch):
    row = await _seed(
        db,
        {"order_status": "COMPLETED", "return_status": "BUYER_SHIPPED_ITEM", "return_type": "RETURN_AND_REFUND"},
    )
    fake = FakeTikTokSweep(
        [_caso("BUYER_SHIPPED_ITEM", "RETURN_AND_REFUND", update_time=1789525794, return_id="R1")]
    )
    _patch(monkeypatch, fake)

    out = await logistica_tiktok.sweep_pos_venda(db)

    await db.refresh(row)
    assert out["returns"] == 0 and out["ids"] == []
    assert row.meli_status["return_type"] == "RETURN_AND_REFUND"


@pytest.mark.asyncio
async def test_so_cancelada_volta_pro_status_do_pedido(db, monkeypatch):
    """Cliente abriu e cancelou, e só: `return_status` vira CANCEL e a
    assinatura volta a ser o status do pedido ("Entregue")."""
    row = await _seed(
        db,
        {"order_status": "DELIVERED", "return_status": "AWAITING_BUYER_SHIP", "return_type": "RETURN_AND_REFUND"},
    )
    fake = FakeTikTokSweep(
        [_caso("RETURN_OR_REFUND_REQUEST_CANCEL", "RETURN_AND_REFUND", update_time=1789525794, return_id="R1")],
        status="DELIVERED",
    )
    _patch(monkeypatch, fake)

    out = await logistica_tiktok.sweep_pos_venda(db)

    await db.refresh(row)
    assert row.id in out["ids"]
    assert row.meli_status["return_status"] == "RETURN_OR_REFUND_REQUEST_CANCEL"
    assert logistica_rules.assinatura_para("TikTok", row.meli_status) == "Entregue"
