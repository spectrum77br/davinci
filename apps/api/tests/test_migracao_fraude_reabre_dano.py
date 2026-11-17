"""A 0492 tira da Observação do pedido só o recado "dano/defeito" da Fraude
automática (16/11/2026, 307238): o robô volta a olhar o pedido. Dados FABRICADOS,
roda a migration de verdade no schema de teste."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import DevolucaoRastreio

pytestmark = pytest.mark.asyncio
_SCHEMA = get_settings().database_schema


def _mig():
    p = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0492_devolucao_fraude_reabre_dano.py"
    )
    spec = importlib.util.spec_from_file_location("m0492", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


async def test_tira_so_o_recado_de_dano(db: AsyncSession) -> None:
    dano = (
        'Fraude automática (16/11 16:37): o comprador alega "O item não corresponde à descrição" '
        "— o motivo que o comprador escolheu é dano/defeito — o vídeo da embalagem não rebate; "
        "o sistema não responde esse caso sozinho (responder à mão)."
    )
    outro = (
        "Fraude automática (16/11 16:37): a própria loja aceitou o reembolso no Seller Center "
        "— conferir se é pra contestar; o sistema não responde esse caso sozinho (responder à mão)."
    )
    db.add_all(
        [
            DevolucaoRastreio(pedido_bling="890001", observacao=f"recado da equipe\n{dano}"),
            DevolucaoRastreio(pedido_bling="890002", observacao=dano),
            DevolucaoRastreio(pedido_bling="890003", observacao=f"recado\n{outro}"),
        ]
    )
    await db.commit()
    mod = _mig()
    assert mod.down_revision == "0491_flex_modelos_shopee"
    n = await db.run_sync(lambda s: mod.reabrir(s.connection(), _SCHEMA))
    await db.commit()
    assert n == 2
    db.expire_all()
    assert (await db.get(DevolucaoRastreio, "890001")).observacao == "recado da equipe"
    assert (await db.get(DevolucaoRastreio, "890002")).observacao is None
    # Outro motivo de "responder à mão" fica.
    assert (await db.get(DevolucaoRastreio, "890003")).observacao == f"recado\n{outro}"
