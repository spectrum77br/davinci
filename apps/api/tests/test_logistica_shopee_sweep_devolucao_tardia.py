"""Devolução da Shopee aberta MUITO depois da venda (caso real 19/09: Shopee
260605AEK4ND5R / Bling 279446, celular vendido em 05/06, devolução por
"garantia de 90 dias" aberta em 03/09).

A venda já tinha saído da Logística (Entregue > 90 dias) e a ingestão só olha
60 dias; o sweep baixava a devolução na lista da loja e descartava por não ter
linha. A loja só soube pelo Seller Center com o pacote de volta e 1 dia de
prazo. Agora:

- o sweep casa a devolução com linha de QUALQUER idade (não só 45 dias);
- venda viva sem linha é recriada do espelho do Bling (`recriar_linhas_do_bling`);
- a faxina dos 90 dias não apaga Entregue velho com devolução viva.

Client falso (sem HTTP) + banco de teste."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import BlingOrder, Logistica, SituacaoBling, StoreInfo, UserRole
from app.services import logistica_ingest, logistica_rules, logistica_shopee

pytestmark = pytest.mark.asyncio

VENDA_VELHA = "260605AEK4ND5R"
VENDA_NOVA = "260901TXTS23H9"
LOJA = "205945426"
ENTREGUE, RESOLVIDO = "83953", "545902"
JUNHO = datetime(2026, 6, 5, 3, tzinfo=UTC)


class FakeShopeeSweep:
    """Só o que o sweep usa: status vivo em lote e a lista de devoluções da
    loja (modo `update_time_from/to`)."""

    def __init__(self, returns: list[dict]):
        self._returns = returns
        self.status_pedidos: list[list[str]] = []

    async def get_order_status_map(self, order_sns):
        self.status_pedidos.append([str(s) for s in order_sns])
        return {str(s): {"status": "COMPLETED", "update_time": 1789525794} for s in order_sns}

    async def get_return_list(self, *, update_time_from=None, update_time_to=None, **_):
        assert update_time_from is not None
        return list(self._returns)


def _patch(monkeypatch, fake: FakeShopeeSweep) -> None:
    async def _integ(session, conta):
        return conta

    monkeypatch.setattr(logistica_shopee, "_shopee_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_shopee, "_build_shopee_client", lambda s, i, *, lock=None: fake)


def _devolucao(sn: str, status: str, *, update_time: int = 1789655792) -> dict:
    return {
        "order_sn": sn,
        "return_sn": f"R-{sn}",
        "status": status,
        "create_time": update_time - 100,
        "update_time": update_time,
    }


def _pedido(numero: str, numeroloja: str, *, situacao: str, data: datetime) -> BlingOrder:
    return BlingOrder(
        id=uuid4(),
        numero=numero,
        numeroloja=numeroloja,
        bling_id=int(numero),
        situacao=situacao,
        loja=LOJA,
        data=data,
        item_index=0,
        item_codigo="F109 Atv",
        item_descricao="Uranyx Fossibot F109",
        item_quantidade=1,
    )


async def _seed_bling(db, make_user) -> None:
    admin = await make_user(role=UserRole.ADMIN)
    db.add_all(
        [
            StoreInfo(user_id=admin.id, platform="shopee", account_name="atv", bling_store_id=LOJA),
            SituacaoBling(id=int(ENTREGUE), nome="Entregue"),
            SituacaoBling(id=int(RESOLVIDO), nome="Resolvido"),
        ]
    )
    await db.commit()


def _linha(numeroloja: str, *, data: date, **campos) -> Logistica:
    campos.setdefault("meli_status", {"order_status": "COMPLETED"})
    return Logistica(
        plataforma="Shopee", conta="atv", pedido_marketplace=numeroloja, data=data,
        status_bling="Entregue", **campos,
    )


async def test_sweep_recria_linha_da_venda_velha_com_devolucao_viva(db, make_user, monkeypatch):
    """Venda de 05/06 (sem linha) com devolução PROCESSING na lista da loja:
    o sweep recria a linha do espelho do Bling e já carimba a devolução —
    a assinatura rende "Devolução solicitada" e o id vai pro recarregar."""
    await _seed_bling(db, make_user)
    db.add(_pedido("279446", VENDA_VELHA, situacao=ENTREGUE, data=JUNHO))
    # Linha recente da mesma conta: é ela que faz o sweep olhar a conta.
    db.add(_linha(VENDA_NOVA, data=date.today(), pedido_bling="293584"))
    await db.commit()
    fake = FakeShopeeSweep([_devolucao(VENDA_VELHA, "PROCESSING")])
    _patch(monkeypatch, fake)

    out = await logistica_shopee.sweep_pos_venda(db)

    nova = (
        await db.execute(select(Logistica).where(Logistica.pedido_marketplace == VENDA_VELHA))
    ).scalar_one()
    assert out["recriadas"] == 1 and out["returns"] == 1
    assert nova.id in out["ids"]
    assert (nova.pedido_bling, nova.plataforma, nova.conta, nova.status_bling, nova.data) == (
        "279446", "Shopee", "atv", "Entregue", date(2026, 6, 5)
    )
    assert nova.meli_status == {"return_status": "PROCESSING"}
    assert logistica_rules.assinatura_para("Shopee", nova.meli_status) == "Devolução solicitada"
    # O status vivo em lote continua só pras linhas da janela (a recriada
    # ganha o order_status no re-enrich do recarregar).
    assert fake.status_pedidos == [[VENDA_NOVA]]


async def test_sweep_nao_recria_venda_lancada_nem_devolucao_encerrada(db, make_user, monkeypatch):
    """Resolvido/Perdimento a equipe já lançou (a devolução fica ACCEPTED pra
    sempre na Shopee) e devolução CANCELLED de venda velha não tem o que
    acompanhar: nenhuma linha nasce."""
    await _seed_bling(db, make_user)
    db.add_all(
        [
            _pedido("279446", VENDA_VELHA, situacao=ENTREGUE, data=JUNHO),
            _pedido("279106", "2606033TQ2R6JJ", situacao=RESOLVIDO, data=JUNHO),
            _linha(VENDA_NOVA, data=date.today(), pedido_bling="293584"),
        ]
    )
    await db.commit()
    fake = FakeShopeeSweep(
        [
            _devolucao(VENDA_VELHA, "CANCELLED"),
            _devolucao("2606033TQ2R6JJ", "ACCEPTED"),
            _devolucao("DESCONHECIDA", "PROCESSING"),  # nem existe no Bling
        ]
    )
    _patch(monkeypatch, fake)

    out = await logistica_shopee.sweep_pos_venda(db)

    assert out["recriadas"] == 0 and out["returns"] == 0
    vendas = (await db.execute(select(Logistica.pedido_marketplace))).scalars().all()
    assert vendas == [VENDA_NOVA]


async def test_sweep_casa_devolucao_com_linha_fora_da_janela_de_45_dias(db, make_user, monkeypatch):
    """Linha de 60 dias (ainda na aba: Entregue < 90) ganha o return_status
    mesmo fora da janela do status vivo — antes a devolução era descartada e
    a regra nunca disparava."""
    await _seed_bling(db, make_user)
    velha = _linha(VENDA_VELHA, data=date.today() - timedelta(days=60), pedido_bling="279446")
    db.add_all([velha, _linha(VENDA_NOVA, data=date.today(), pedido_bling="293584")])
    await db.commit()
    fake = FakeShopeeSweep([_devolucao(VENDA_VELHA, "REQUESTED")])
    _patch(monkeypatch, fake)

    out = await logistica_shopee.sweep_pos_venda(db)

    await db.refresh(velha)
    assert out["recriadas"] == 0 and out["returns"] == 1 and velha.id in out["ids"]
    assert velha.meli_status["return_status"] == "REQUESTED"
    assert fake.status_pedidos == [[VENDA_NOVA]]


async def test_recriar_linhas_do_bling_ignora_quem_ja_tem_linha(db, make_user):
    """Idempotente: venda que já está na aba não duplica."""
    await _seed_bling(db, make_user)
    db.add_all(
        [
            _pedido("279446", VENDA_VELHA, situacao=ENTREGUE, data=JUNHO),
            _linha(VENDA_VELHA, data=JUNHO.date(), pedido_bling="279446"),
        ]
    )
    await db.commit()

    novas = await logistica_ingest.recriar_linhas_do_bling(db, "shopee", [VENDA_VELHA, " ", ""])

    assert novas == []
    assert len((await db.execute(select(Logistica))).scalars().all()) == 1


async def test_cleanup_segura_entregue_velho_com_devolucao_viva(db):
    """Entregue > 90 dias sai — salvo com devolução viva na assinatura. Caso
    encerrado (CANCELLED) volta a sair; TikTok concluído também."""
    velho = date.today() - timedelta(days=120)
    def _ret(st: str) -> dict:
        return {"order_status": "COMPLETED", "return_status": st}

    viva = _linha("V1", data=velho, pedido_bling="1", meli_status=_ret("PROCESSING"))
    aceita = _linha("V2", data=velho, pedido_bling="2", meli_status=_ret("ACCEPTED"))
    cancelada = _linha("V3", data=velho, pedido_bling="3", meli_status=_ret("CANCELLED"))
    sem_caso = _linha("V4", data=velho, pedido_bling="4")
    tiktok_ok = Logistica(
        plataforma="TikTok", conta="mini", pedido_marketplace="T1", pedido_bling="5", data=velho,
        status_bling="Entregue", meli_status=_ret("RETURN_OR_REFUND_REQUEST_COMPLETE"),
    )
    recente = _linha("V6", data=date.today() - timedelta(days=10), pedido_bling="6")
    db.add_all([viva, aceita, cancelada, sem_caso, tiktok_ok, recente])
    await db.commit()

    removed = await logistica_ingest.cleanup_finalizados(db)

    ficaram = sorted((await db.execute(select(Logistica.pedido_bling))).scalars().all())
    assert removed == 3
    assert ficaram == ["1", "2", "6"]
