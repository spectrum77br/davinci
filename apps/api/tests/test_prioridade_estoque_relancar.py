"""Robô de prioridade de estoque — relançamento do estoque no Bling após a troca.

Eduardo (14/09): "ele está mudando mas continua tirando estoque do saldo de
ra ao invés de tirar do f105 de sp". O Bling reserva o estoque dos itens na
entrada do pedido e não move a reserva no PUT; depois da troca o robô agora
ESTORNA e RELANÇA o estoque do pedido. Cobre:
  * caminho feliz: PUT → estornar → lançar, na ordem; espelho local trocado;
  * estorno 4xx (nada lançado) é tolerado e o lançar ainda acontece;
  * lançar falhando conta em relancar_falhas sem desfazer a troca;
  * PUT falhando não chama estoque nenhum;
  * o helper nunca levanta.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder
from app.services import nf_emissao_gerar
from app.services import prioridade_estoque as prio

BLING_ID = 26870211378


class _Resp:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text


class FakeBling:
    def __init__(self, *, put_ok=True, estorno=204, lancar=204):
        self.calls: list[str] = []
        self.put_ok, self.estorno, self.lancar = put_ok, estorno, lancar
        self.put_body: dict | None = None

    async def find_active_product_by_sku(self, sku):
        self.calls.append(f"find:{sku}")
        return {"id": 16703448890, "sku": sku, "name": "Kit F105 SP", "stock": 10}

    async def get_order(self, bling_id):
        self.calls.append("get_order")
        return {
            "id": bling_id, "numero": "297146", "situacao": {"id": 6, "valor": 0},
            "contato": {"id": 1}, "loja": {"id": 2}, "observacoes": "",
            "itens": [{"codigo": "dg019.ra+a001.ra", "quantidade": 1, "produto": {"id": 1}}],
        }

    async def update_order(self, bling_id, body):
        self.calls.append("put")
        if not self.put_ok:
            raise RuntimeError("erro 67")
        self.put_body = body
        return body

    async def estornar_estoque_pedido(self, bling_id):
        self.calls.append("estornar")
        if isinstance(self.estorno, Exception):
            raise self.estorno
        return _Resp(self.estorno, "" if self.estorno < 300 else "estoque não lançado")

    async def lancar_estoque_pedido(self, bling_id):
        self.calls.append("lancar")
        return _Resp(self.lancar, "" if self.lancar < 300 else "sem saldo")


async def _pedido(db: AsyncSession) -> None:
    db.add(BlingOrder(
        numero="297146", bling_id=BLING_ID, loja="5001", item_index=0,
        item_codigo="dg019.ra+a001.ra", item_quantidade=1, situacao="6",
        data=datetime.now(UTC),
    ))
    await db.commit()


def _arma(monkeypatch, client: FakeBling) -> None:
    async def _mapa(session):
        return {"dg019": "sp"}

    async def _client(session):
        return client

    monkeypatch.setattr(prio, "_mapa_prioridades", _mapa)
    monkeypatch.setattr(nf_emissao_gerar, "_bling_client_opt", _client)


@pytest.mark.asyncio
async def test_troca_e_relanca_estoque_na_ordem(db: AsyncSession, monkeypatch):
    await _pedido(db)
    client = FakeBling()
    _arma(monkeypatch, client)

    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["297146"])
    await db.commit()

    assert resumo["trocados"] == 1 and resumo["relancados"] == 1 and resumo["relancar_falhas"] == 0
    assert client.calls[-3:] == ["put", "estornar", "lancar"]
    assert client.put_body["itens"][0]["codigo"] == "dg019.sp+a001.sp"
    row = (await db.execute(select(BlingOrder).where(BlingOrder.numero == "297146"))).scalar_one()
    assert row.item_codigo == "dg019.sp+a001.sp"


@pytest.mark.asyncio
async def test_estorno_sem_estoque_lancado_e_tolerado(db: AsyncSession, monkeypatch):
    await _pedido(db)
    client = FakeBling(estorno=400)
    _arma(monkeypatch, client)
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["297146"])
    assert resumo["trocados"] == 1 and resumo["relancados"] == 1
    assert client.calls[-2:] == ["estornar", "lancar"]


@pytest.mark.asyncio
async def test_lancar_falhando_conta_sem_desfazer_a_troca(db: AsyncSession, monkeypatch):
    await _pedido(db)
    client = FakeBling(lancar=400)
    _arma(monkeypatch, client)
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["297146"])
    await db.commit()
    assert resumo["trocados"] == 1 and resumo["relancados"] == 0 and resumo["relancar_falhas"] == 1
    row = (await db.execute(select(BlingOrder).where(BlingOrder.numero == "297146"))).scalar_one()
    assert row.item_codigo == "dg019.sp+a001.sp"


@pytest.mark.asyncio
async def test_put_falhando_nao_mexe_no_estoque(db: AsyncSession, monkeypatch):
    await _pedido(db)
    client = FakeBling(put_ok=False)
    _arma(monkeypatch, client)
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["297146"])
    assert resumo["falhas"] == 1 and resumo["trocados"] == 0
    assert "estornar" not in client.calls and "lancar" not in client.calls


@pytest.mark.asyncio
async def test_relancar_nunca_levanta():
    client = FakeBling(estorno=RuntimeError("bling fora"))
    assert await prio.relancar_estoque_pedido(client, BLING_ID, "297146") is False
