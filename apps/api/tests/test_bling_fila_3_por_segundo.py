"""Fila de chamadas ao Bling: no máximo 3 por segundo por conta (21/10/2026).

O Bling V3 aceita 3 requisições por segundo por conta (developer.bling.com.br/
limites). O DaVinci deixava 5/s num balde por segundo e, no minuto :20, o
Bling recusava ~190 chamadas em 5 min (a entrada da devolução 297390 foi uma
delas). A fila espaça cada chamada 1/3 s da anterior, somando api e workers.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from app.services.marketplaces import bling as bling_mod

pytestmark = pytest.mark.asyncio


async def _rodar(n: int, conta: str) -> list[float]:
    marcas: list[float] = []

    async def uma():
        await bling_mod._acquire_bling_rate_slot(conta)
        marcas.append(time.monotonic())

    await asyncio.gather(*(uma() for _ in range(n)))
    return sorted(marcas)


async def test_conta_principal_nunca_mais_de_3_em_um_segundo():
    # Conta principal a 2,5/s (folga para robôs de fora e para a rede).
    marcas = await _rodar(9, bling_mod.BLING_CONTA_PRINCIPAL)
    for i in range(len(marcas) - 3):
        assert marcas[i + 3] - marcas[i] >= 1.1, marcas
    assert marcas[-1] - marcas[0] >= 3.0


async def test_contas_de_nota_no_ritmo_de_antes():
    # Contas de nota fiscal: 5/s (o export síncrono foi dimensionado para isso).
    marcas = await _rodar(6, f"notas-teste-{uuid.uuid4().hex[:6]}")
    for i in range(len(marcas) - 1):
        assert marcas[i + 1] - marcas[i] >= 0.18, marcas
    assert marcas[-1] - marcas[0] < 1.5


async def test_contas_diferentes_tem_filas_separadas():
    a, b = f"teste-a-{uuid.uuid4().hex[:6]}", f"teste-b-{uuid.uuid4().hex[:6]}"
    inicio = time.monotonic()
    ma, mb = await asyncio.gather(_rodar(3, a), _rodar(3, b))
    # Uma conta não espera a outra.
    assert max(ma[-1], mb[-1]) - inicio < 1.0


async def test_sem_fila_nao_espera():
    inicio = time.monotonic()
    await bling_mod._acquire_bling_rate_slot(f"teste-{uuid.uuid4().hex[:8]}")
    assert time.monotonic() - inicio < 0.2
