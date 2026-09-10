"""Pacote de volta que JÁ CHEGOU tem que aparecer como chegado.

Eduardo, 10/09/2026: "291516 esse pedido esta entregue e a ultima localizacao
nao esta alterada". A Shopee sabia — `reverse_logistics_status =
LOGISTICS_DELIVERY_DONE` — mas isso só existe no DETALHE da devolução, e o
DaVinci lia apenas a lista. A tela caía no status do CASO ("Devolução em
processamento"), que fica assim por dias depois da entrega.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.routers.devolutions import _chegou_em, _com_status_da_devolucao
from app.services.logistica_shopee import _entregue_em_do_detalhe

# 09/09/2026 09:42 BRT, o instante real do caso 291516.
ENTREGUE = datetime(2026, 9, 9, 12, 42, 37, tzinfo=UTC)

pytestmark = pytest.mark.asyncio


def test_le_a_entrega_da_perna_reversa_da_shopee():
    det = {
        "status": "PROCESSING",
        "logistics_status": "LOGISTICS_DELIVERY_DONE",
        "reverse_logistics_status": "LOGISTICS_DELIVERY_DONE",
        "update_time": int(ENTREGUE.timestamp()),
    }
    assert _entregue_em_do_detalhe(det) == ENTREGUE


def test_pacote_a_caminho_nao_conta_como_chegado():
    a_caminho = {
        "status": "PROCESSING",
        "reverse_logistics_status": "LOGISTICS_PICKUP_DONE",
        "update_time": int(ENTREGUE.timestamp()),
    }
    assert _entregue_em_do_detalhe(a_caminho) is None
    assert _entregue_em_do_detalhe({"status": "PROCESSING"}) is None
    assert _entregue_em_do_detalhe(None) is None


def test_chegou_em_usa_o_carimbo_do_marketplace():
    """Antes a Shopee nunca preenchia essa coluna; agora preenche."""
    chegou = _chegou_em(
        plataforma="Shopee",
        meli_status={"return_status": "PROCESSING"},
        status_datas=None,
        devolucao_status_auto="PROCESSING",
        fonte_auto="shopee",
        devolucao_atualizada_em=ENTREGUE,
        pacote_entregue_em=ENTREGUE,
    )
    assert chegou is not None
    assert chegou.isoformat() == "2026-09-09"


def test_sem_carimbo_a_coluna_continua_vazia():
    """Preferimos vazio a mentir — regra de quem escreveu a coluna."""
    assert _chegou_em(
        plataforma="Shopee",
        meli_status={"return_status": "PROCESSING"},
        status_datas=None,
        devolucao_status_auto="PROCESSING",
        fonte_auto="shopee",
        devolucao_atualizada_em=ENTREGUE,
        pacote_entregue_em=None,
    ) is None


def test_localizacao_diz_que_o_pacote_chegou():
    d = _com_status_da_devolucao(
        {"localizacao": "Pedido entregue"},
        localizacao_manual=None,
        lg_plataforma="Shopee",
        lg_meli_status={"return_status": "PROCESSING"},
        status_auto="PROCESSING",
        fonte_auto="shopee",
        localizacao_auto=None,
        pacote_entregue_em=ENTREGUE,
    )
    assert d["localizacao"].startswith("Pacote entregue ao vendedor")
    # A entrega ORIGINAL não some, só muda de coluna.
    assert d["entrega_localizacao"] == "Pedido entregue"


def test_evento_do_17track_continua_mandando_quando_existe():
    """Onde há rastreio físico (código dos Correios), ele é mais específico."""
    d = _com_status_da_devolucao(
        {"localizacao": "Pedido entregue"},
        localizacao_manual=None,
        lg_plataforma="Shopee",
        lg_meli_status={"return_status": "PROCESSING"},
        status_auto="PROCESSING",
        fonte_auto="shopee",
        localizacao_auto="Objeto entregue ao remetente",
        pacote_entregue_em=ENTREGUE,
    )
    assert d["localizacao"].endswith("Objeto entregue ao remetente")


def test_localizacao_manual_continua_mandando_em_tudo():
    d = _com_status_da_devolucao(
        {"localizacao": "escrito na mão"},
        localizacao_manual="escrito na mão",
        lg_plataforma="Shopee",
        lg_meli_status={"return_status": "PROCESSING"},
        status_auto="PROCESSING",
        fonte_auto="shopee",
        localizacao_auto=None,
        pacote_entregue_em=ENTREGUE,
    )
    assert d["localizacao"] == "escrito na mão"


def test_ml_so_conta_a_perna_que_vai_pro_vendedor():
    """O ML parte a devolução em duas: o comprador posta pro galpão dele
    (Cajamar) e depois o ML reenvia pro vendedor. "Entregue" na primeira NÃO
    é o pacote na nossa mão — e a ordem da lista não é estável, então pegar
    o primeiro dava resultado diferente a cada rodada."""
    from app.services.logistica_meli import _entregue_ao_vendedor, _return_shipment

    galpao = {
        "shipment_id": 1,
        "type": "return",
        "destination": {"name": "warehouse"},
        "status": "delivered",
        "tracking_number": "MEL-GALPAO",
    }
    vendedor = {
        "shipment_id": 2,
        "type": "return_from_triage",
        "destination": {"name": "seller_address"},
        "status": "delivered",
        "tracking_number": "MEL-VENDEDOR",
    }

    # Em qualquer ordem, vale a perna do vendedor.
    assert _return_shipment({"shipments": [galpao, vendedor]})["tracking_number"] == "MEL-VENDEDOR"
    assert _return_shipment({"shipments": [vendedor, galpao]})["tracking_number"] == "MEL-VENDEDOR"

    detalhe = {"status_history": {"date_delivered": "2026-09-09T10:00:00-03:00"}}
    assert _entregue_ao_vendedor({"shipments": [galpao, vendedor]}, detalhe) is not None
    # Só o galpão: chegou no Mercado Livre, não em nós.
    assert _entregue_ao_vendedor({"shipments": [galpao]}, detalhe) is None
    # Perna do vendedor ainda a caminho.
    a_caminho = {**vendedor, "status": "shipped"}
    assert _entregue_ao_vendedor({"shipments": [a_caminho]}, detalhe) is None


def test_correios_entregue_no_push_do_17track():
    """Onde o retorno vai pelos Correios (todo o TikTok), o evento físico é a
    prova de chegada."""
    from app.services.logistica_track import entregue_no_push, parse_push_entregues

    assert entregue_no_push({"latest_status": {"status": "Delivered"}}) is True
    assert entregue_no_push({"latest_status": {"status": "InTransit"}}) is False
    # "Expired" encerra o rastreio, mas não é entrega.
    assert entregue_no_push({"latest_status": {"status": "Expired"}}) is False
    assert entregue_no_push({}) is False

    push = {
        "data": {
            "accepted": [
                {"number": "AP1BR", "track_info": {"latest_status": {"status": "Delivered"}}},
                {"number": "AP2BR", "track_info": {"latest_status": {"status": "InTransit"}}},
            ]
        }
    }
    assert parse_push_entregues(push) == {"AP1BR"}


async def test_pull_carimba_entrega_que_o_push_perdeu(db, monkeypatch):
    """O push é evento: entrega ocorrida ANTES de o sistema passar a escutar
    nunca seria carimbada (caso 293437, entregue 09/09 e sem "Chegou em").
    Agora o sync também pergunta o estado dos códigos já registrados."""
    from app.models import DevolucaoRastreio
    from app.services import devolucao_rastreio_sync as svc

    db.add_all([
        DevolucaoRastreio(pedido_bling="293437", rastreio_auto="AP444879986BR"),
        DevolucaoRastreio(pedido_bling="900001", rastreio_auto="AP111111111BR"),
        # SPX da Shopee: não é Correios, nem entra na consulta.
        DevolucaoRastreio(pedido_bling="900002", rastreio_auto="BR2668727206243"),
    ])
    await db.commit()

    perguntados: list[list[str]] = []

    async def fake_fetch(numbers):
        perguntados.append(sorted(numbers))
        return {
            "info": {
                "AP444879986BR": {
                    "localizacao": "Piracicaba/SP — Objeto entregue ao destinatário",
                    "status": "Delivered",
                    "sync_at": ENTREGUE,
                },
                "AP111111111BR": {
                    "localizacao": "Bauru/SP — Objeto em transferência",
                    "status": "InTransit",
                    "sync_at": ENTREGUE,
                },
            },
            "desconhecidos": [],
        }

    monkeypatch.setattr(svc.logistica_track, "fetch_detalhado", fake_fetch)

    resumo = await svc._puxar_correios(db)

    assert perguntados == [["AP111111111BR", "AP444879986BR"]]  # SPX ficou fora
    assert resumo == {"consultados": 2, "entregues": 1, "localizacoes": 2}
    entregue = await db.get(DevolucaoRastreio, "293437")
    a_caminho = await db.get(DevolucaoRastreio, "900001")
    await db.refresh(entregue)
    await db.refresh(a_caminho)
    assert entregue.pacote_entregue_em == ENTREGUE
    assert "entregue ao destinatário" in (entregue.localizacao_auto or "")
    assert a_caminho.pacote_entregue_em is None


async def test_pull_nao_reconsulta_quem_ja_chegou(db, monkeypatch):
    from sqlalchemy import delete

    from app.models import DevolucaoRastreio
    from app.services import devolucao_rastreio_sync as svc

    await db.execute(delete(DevolucaoRastreio))  # isola das linhas do teste anterior
    db.add(DevolucaoRastreio(
        pedido_bling="900003", rastreio_auto="AP222222222BR", pacote_entregue_em=ENTREGUE,
    ))
    await db.commit()

    async def nunca(numbers):  # pragma: no cover - não deve ser chamado
        raise AssertionError("não devia perguntar de novo")

    monkeypatch.setattr(svc.logistica_track, "fetch_detalhado", nunca)

    assert (await svc._puxar_correios(db))["consultados"] == 0
