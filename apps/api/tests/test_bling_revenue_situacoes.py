"""bling_revenue só conta situação 15 (Em andamento) e 83953 (Entregue).

Whitelist (não blacklist) — qualquer situação custom nova NÃO infla o
faturamento acidentalmente. Companion do fix `cee6f9b`+ — antes contava
tudo menos 12/6, inflando o agregado.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.marketing import bling_revenue as br


class _FakeClient:
    """async generator que devolve pedidos com várias situações."""

    def __init__(self, pedidos: list[dict]) -> None:
        self._pedidos = pedidos

    async def iter_pedidos_vendas(
        self, *, data_inicial, data_final, id_situacao, id_loja,
    ):
        for p in self._pedidos:
            yield p


class _FakeSession:
    async def commit(self) -> None:
        return None


class _FakeIntegration:
    id = "fake-integ"


def _mk(situacao_id: int, valor: float, dia: str = "2026-06-01") -> dict:
    return {
        "situacao": {"id": situacao_id},
        "dataEmissao": dia,
        "totalVenda": valor,
    }


@pytest.mark.asyncio
async def test_so_15_e_83953_contam_no_faturamento(monkeypatch):
    pedidos = [
        _mk(15, 100.0),       # Em andamento → conta
        _mk(15, 100.0),       # Em andamento → conta
        _mk(83953, 200.0),    # Entregue → conta
        _mk(83965, 150.0),    # Enviado Etiqueta → NÃO conta
        _mk(12, 50.0),        # Cancelado → NÃO conta
        _mk(83957, 80.0),     # Aguardando Devolução → NÃO conta
        _mk(83963, 60.0),     # Devolvido Estoque → NÃO conta
        _mk(545901, 30.0),    # Sucata → NÃO conta
        _mk(6, 40.0),         # Em digitação → NÃO conta
    ]

    async def _fake_loja(_session, _integ):
        return 99999

    async def _fake_client(_session):
        return _FakeClient(pedidos)

    monkeypatch.setattr(br, "resolve_bling_loja_id", _fake_loja)
    monkeypatch.setattr(br, "_resolve_bling_client", _fake_client)

    result = await br.get_bling_revenue(
        _FakeSession(), _FakeIntegration(),
        start=date(2026, 6, 1), end=date(2026, 6, 1),
    )
    assert result is not None
    # 2×100 (15) + 1×200 (83953) = 400 ; os outros 7 ignorados.
    assert result.total == 400.0
    assert result.order_count == 3
    assert result.by_day == {date(2026, 6, 1): 400.0}


@pytest.mark.asyncio
async def test_situacao_ausente_ou_invalida_nao_conta(monkeypatch):
    """Situação null ou string não numérica também NÃO conta (whitelist
    estrita) — antes do fix, pedido sem situação válida escapava do filtro."""
    pedidos = [
        _mk(15, 100.0),                  # válido → conta
        {"situacao": None, "dataEmissao": "2026-06-01", "totalVenda": 50.0},
        {"situacao": {}, "dataEmissao": "2026-06-01", "totalVenda": 50.0},
        {"situacao": {"id": "xyz"}, "dataEmissao": "2026-06-01", "totalVenda": 50.0},
    ]

    async def _fake_loja(_session, _integ):
        return 1

    async def _fake_client(_session):
        return _FakeClient(pedidos)

    monkeypatch.setattr(br, "resolve_bling_loja_id", _fake_loja)
    monkeypatch.setattr(br, "_resolve_bling_client", _fake_client)

    result = await br.get_bling_revenue(
        _FakeSession(), _FakeIntegration(),
        start=date(2026, 6, 1), end=date(2026, 6, 1),
    )
    assert result is not None
    assert result.total == 100.0
    assert result.order_count == 1
