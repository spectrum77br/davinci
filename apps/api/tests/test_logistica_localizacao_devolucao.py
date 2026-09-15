"""Localização de pedido ML com DEVOLUÇÃO: a coluna descreve a volta do
produto (vem do ML) e a leitura física dos Correios do envio de ida não a
sobrescreve. Caso real: Air Fryer 293519 (Vinicius, 15/09) — a coluna ficava
"Entregue → Jaraguá do Sul/SC" com a devolução já entregue na loja."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.logistica import Logistica
from app.services import logistica_meli, logistica_track_sync


async def _async_none():
    return None


_MELI_DEVOLVIDO = {
    "order_status": "cancelled",
    "ship_status": "delivered",
    "cancel_group": "mediations",
    "return_status": "delivered",
    "claim_stage": "dispute",
    "claim_status": "closed",
    "benefited": "complainant",
}


@pytest.mark.asyncio
async def test_devolucao_passa_por_cima_do_fisico_dos_correios(db: AsyncSession, monkeypatch):
    async def _enr(client, order_id):
        return {
            "meli_status": dict(_MELI_DEVOLVIDO),
            "rastreio": "AD828496989BR",
            "localizacao": "Devolvido → loja (Piracicaba/SP)",
            "localizacao_devolucao": True,
            "datas": {},
        }

    monkeypatch.setattr(logistica_meli, "build_enrichment", _enr)
    monkeypatch.setattr(logistica_meli, "_ml_integration_for_conta", lambda *a, **k: _async_none())
    fisico = "Jaraguá do Sul/SC — Objeto entregue ao destinatário"
    row = Logistica(
        pedido_bling="293519",
        plataforma="Mercado Livre",
        conta="kia",
        pedido_marketplace="2000018212582238",
        data=date.today(),
        rastreio="AD828496989BR",
        localizacao=fisico,
        localizacao_at=datetime.now(UTC),
        divergencia="Mercado Livre: cancelado. Correios: entregue.",
    )
    db.add(row)
    await db.commit()

    await logistica_meli.enrich_row(db, row, client_cache={"kia": object()})
    assert row.localizacao == "Devolvido → loja (Piracicaba/SP)"
    assert row.localizacao_at is None  # não é mais leitura dos Correios
    assert row.divergencia is None  # não há físico da ida pra cruzar
    assert row.meli_status["return_status"] == "delivered"

    # Pull/push do 17track do envio de ida NÃO sobrescreve a devolução; a
    # entrega física ainda é registrada.
    mudou = logistica_track_sync.aplicar_leitura(row, fisico, entregue=True)
    assert mudou is False
    assert row.localizacao == "Devolvido → loja (Piracicaba/SP)"
    assert row.localizacao_at is None
    assert row.entregue_em is not None
    await db.commit()


@pytest.mark.asyncio
async def test_sem_devolucao_o_fisico_dos_correios_continua_mandando(db: AsyncSession, monkeypatch):
    # Guarda antiga intacta: com o físico carimbado, o proxy do ML da IDA não
    # sobrescreve; só a devolução tem esse poder.
    async def _enr(client, order_id):
        return {
            "meli_status": {"order_status": "paid", "ship_status": "delivered"},
            "rastreio": "AD828496989BR",
            "localizacao": "Entregue → Jaraguá do Sul/SC",
            "localizacao_devolucao": False,
            "datas": {},
        }

    monkeypatch.setattr(logistica_meli, "build_enrichment", _enr)
    monkeypatch.setattr(logistica_meli, "_ml_integration_for_conta", lambda *a, **k: _async_none())
    fisico = "Jaraguá do Sul/SC — Objeto entregue ao destinatário"
    carimbo = datetime.now(UTC)
    row = Logistica(
        pedido_bling="293520",
        plataforma="Mercado Livre",
        conta="kia",
        pedido_marketplace="2000018212582239",
        data=date.today(),
        rastreio="AD828496989BR",
        localizacao=fisico,
        localizacao_at=carimbo,
    )
    db.add(row)
    await db.commit()
    await logistica_meli.enrich_row(db, row, client_cache={"kia": object()})
    assert row.localizacao == fisico
    assert row.localizacao_at == carimbo
    await db.commit()


def test_aplicar_leitura_sem_devolucao_segue_normal():
    row = Logistica(
        plataforma="Mercado Livre",
        rastreio="AD828496989BR",
        meli_status={
            "order_status": "paid",
            "ship_status": "shipped",
            "return_status": "cancelled",
        },
    )
    assert logistica_track_sync.aplicar_leitura(row, "Curitiba/PR — Objeto em trânsito") is True
    assert row.localizacao == "Curitiba/PR — Objeto em trânsito"
    assert row.localizacao_at is not None
