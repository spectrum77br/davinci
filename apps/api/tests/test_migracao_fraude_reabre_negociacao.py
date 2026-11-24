"""A 0506 troca o recado "negociação" da Fraude automática (leitura errada do
PENDING_RESPOND da Shopee, 24/11/2026) nos pedidos ABERTOS: o robô volta a ler o
caso. Dados FABRICADOS, roda a migration de verdade no schema de teste."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import DevolucaoRastreio
from app.services import devolucao_fraude_auto as auto

pytestmark = pytest.mark.asyncio
_SCHEMA = get_settings().database_schema


def _mig():
    p = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0506_devolucao_fraude_reabre_negociacao.py"
    )
    spec = importlib.util.spec_from_file_location("m0506", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


async def test_tira_a_marca_so_do_recado_de_negociacao_nos_abertos(db: AsyncSession) -> None:
    mod = _mig()
    # O recado exatamente como o robô gravava (19/11, 307496).
    neg = f"{auto.MARCA} (19/11 10:27): {auto._POR_SITUACAO[auto.NEGOCIACAO]}; {auto.NAO_RESPONDE}."
    assert mod.VELHO in neg
    pedido = f"{auto.MARCA} (18/11 19:17): {auto.VIDEO_PEDIDO}."
    outro = (
        f"{auto.MARCA} (19/11 13:57): a própria loja aceitou o reembolso no Seller Center "
        f"— conferir se é pra contestar; {auto.NAO_RESPONDE}."
    )
    db.add_all(
        [
            DevolucaoRastreio(pedido_bling="890601", observacao=f"{pedido}\n{neg}"),
            DevolucaoRastreio(pedido_bling="890602", observacao=f"recado\n{outro}"),
            DevolucaoRastreio(
                pedido_bling="890603", observacao=neg, resolvido_at=datetime.now(UTC)
            ),
        ]
    )
    await db.commit()
    assert mod.down_revision == "0505_alertas_por_equipe"
    n = await db.run_sync(lambda s: mod.reabrir(s.connection(), _SCHEMA))
    await db.commit()
    assert n == 1
    db.expire_all()
    a = (await db.get(DevolucaoRastreio, "890601")).observacao
    assert a.startswith(pedido) and mod.NOVO in a
    # Sem a marca de "já lido" nem o cartão "responder à mão": o robô lê de novo.
    assert auto.NAO_RESPONDE not in a and auto.CARTAO_A_MAO not in a
    # Outro motivo fica; resolvido não é mexido.
    assert (await db.get(DevolucaoRastreio, "890602")).observacao == f"recado\n{outro}"
    assert (await db.get(DevolucaoRastreio, "890603")).observacao == neg
