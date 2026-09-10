"""Loja que para de responder tem que APARECER.

Eduardo, 10/09/2026: a Shopee Vortan ficou 14 h muda (partner key vencida) e
o resumo do job seguia com errors=0 — 32 pedidos parados e ele descobriu
olhando a tela. Lote inteiro sem resposta agora vira aviso no Threema.
"""

from __future__ import annotations

import uuid

import pytest

from app.models import IntegrationPlatform
from app.services import marketplace_shipment_check as svc

pytestmark = pytest.mark.asyncio


class _Integ:
    def __init__(self, nome: str = "Vortan") -> None:
        self.id = uuid.uuid4()
        self.name = nome
        self.platform = IntegrationPlatform.SHOPEE


@pytest.fixture(autouse=True)
def _limpa_estado(monkeypatch):
    svc._lojas_mudas.clear()
    enviados: list[str] = []

    async def fake(texto: str) -> None:
        enviados.append(texto)

    monkeypatch.setattr(svc, "_avisar_threema", fake)
    yield enviados
    svc._lojas_mudas.clear()


async def test_avisa_uma_vez_quando_a_loja_emudece(_limpa_estado):
    enviados = _limpa_estado
    integ = _Integ()

    # Fica muda, mas ainda dentro do limite: ninguém é acordado à toa.
    for _ in range(svc._MUDA_TICKS_PRA_AVISAR - 1):
        await svc._registrar_resposta_da_loja(integ, pedidos=32, responderam=0)
    assert enviados == []

    # No tick do limite, sai UM aviso.
    await svc._registrar_resposta_da_loja(integ, pedidos=32, responderam=0)
    assert len(enviados) == 1
    assert "Vortan" in enviados[0] and "32" in enviados[0]

    # Continuar muda não repete o aviso a cada minuto.
    for _ in range(5):
        await svc._registrar_resposta_da_loja(integ, pedidos=32, responderam=0)
    assert len(enviados) == 1


async def test_avisa_a_volta_e_zera_o_contador(_limpa_estado):
    enviados = _limpa_estado
    integ = _Integ()
    for _ in range(svc._MUDA_TICKS_PRA_AVISAR):
        await svc._registrar_resposta_da_loja(integ, pedidos=10, responderam=0)
    assert len(enviados) == 1

    await svc._registrar_resposta_da_loja(integ, pedidos=10, responderam=10)

    assert len(enviados) == 2
    assert "voltou a responder" in enviados[1]
    assert integ.id not in svc._lojas_mudas


async def test_loja_que_responde_nao_gera_ruido(_limpa_estado):
    enviados = _limpa_estado
    integ = _Integ()

    # Responde parcial (pedido antigo que a Shopee não devolve) não é mudez.
    for _ in range(svc._MUDA_TICKS_PRA_AVISAR + 10):
        await svc._registrar_resposta_da_loja(integ, pedidos=45, responderam=44)
    # Lote vazio porque não havia pedido pra perguntar também não conta.
    for _ in range(svc._MUDA_TICKS_PRA_AVISAR + 10):
        await svc._registrar_resposta_da_loja(integ, pedidos=0, responderam=0)

    assert enviados == []
    assert svc._lojas_mudas == {}
