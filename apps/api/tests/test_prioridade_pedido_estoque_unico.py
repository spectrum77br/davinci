"""Robô de prioridade num pedido igual ao 298787 (Vinicius, 23/09/2026).

O pedido caiu com o A17 Branco em .ci e o A17 Preto em .sp. Com a regra
"pedido num estoque só", o robô põe os dois no mesmo estoque num PUT só,
avisa nas Observações e compensa o kit como sempre. Desligada, nada muda.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BlingOrder, MargemAudit
from app.services import nf_emissao_gerar
from app.services import prioridade_estoque as prio

IDS = {
    "dg054.ci+a001.ci": 900, "dg054.sp+a001.sp": 901,
    "dg052.ci+a001.ci": 910, "dg052.sp+a001.sp": 911,
    "dg054.ci": 101, "dg054.sp": 201, "dg052.ci": 102, "dg052.sp": 202,
    "a001.ci": 103, "a001.sp": 203,
}


class FakeBling:
    def __init__(self, numero: str, itens: list[tuple[str, int]]):
        self.numero, self.itens = numero, itens
        self.puts: list[dict] = []
        self.movs: list[tuple[str, int, int]] = []

    async def find_active_product_by_sku(self, sku):
        pid = IDS.get(sku.lower())
        return {"id": pid, "sku": sku, "name": f"Produto {sku}", "stock": 50} if pid else None

    async def get_order(self, bling_id):
        return {"id": bling_id, "numero": self.numero, "situacao": {"id": 6},
                "contato": {"id": 1}, "loja": {"id": 2}, "observacoes": "",
                "itens": [{"id": 70 + i, "codigo": c, "quantidade": q, "valor": 824.5,
                           "produto": {"id": IDS[c]}}
                          for i, (c, q) in enumerate(self.itens)]}

    async def update_order(self, bling_id, body):
        self.puts.append(body)
        return body

    async def update_stock_by_id(self, pid, qty, *, operation, observacao=None, **kw):
        self.movs.append((operation, pid, qty))
        return {"id": 1}


ITENS = [("dg054.ci+a001.ci", 1), ("dg052.sp+a001.sp", 1)]


async def _prepara(db: AsyncSession, monkeypatch, numero: str) -> FakeBling:
    for i, (codigo, qtd) in enumerate(ITENS):
        db.add(BlingOrder(numero=numero, bling_id=int(numero), loja="5001", item_index=i,
                          item_codigo=codigo, item_quantidade=qtd, situacao="6",
                          data=datetime.now(UTC)))
    await db.commit()
    client = FakeBling(numero, ITENS)

    async def _mapa(session):
        return {"dg054": "ci"}  # o Branco tem prioridade CI; o Preto não tem

    async def _client(session):
        return client

    monkeypatch.setattr(prio, "_mapa_prioridades", _mapa)
    monkeypatch.setattr(nf_emissao_gerar, "_bling_client_opt", _client)
    return client


@pytest.mark.asyncio
async def test_pedido_misturado_vai_todo_pro_estoque_da_prioridade(db: AsyncSession, monkeypatch):
    client = await _prepara(db, monkeypatch, "298787")

    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["298787"])
    await db.commit()

    assert resumo["trocados"] == 1 and resumo["pedidos_estoque_unico"] == 1
    assert len(client.puts) == 1
    body = client.puts[0]
    assert [i["codigo"] for i in body["itens"]] == ["dg054.ci+a001.ci", "dg052.ci+a001.ci"]
    assert body["itens"][1]["produto"] == {"id": 910}
    assert "pedido todo no estoque CI" in body["observacoes"]
    assert "dg052.sp+a001.sp -> dg052.ci+a001.ci" in body["observacoes"]

    db.expire_all()
    codigos = (await db.execute(
        select(BlingOrder.item_codigo).where(BlingOrder.numero == "298787")
        .order_by(BlingOrder.item_index)
    )).scalars().all()
    assert codigos == ["dg054.ci+a001.ci", "dg052.ci+a001.ci"]
    audit = (await db.execute(
        select(MargemAudit).where(MargemAudit.pedido_bling == "298787")
    )).scalars().all()
    assert [(a.valor_antigo, a.valor_novo, a.origem) for a in audit] == [
        ("dg052.sp+a001.sp", "dg052.ci+a001.ci", "prioridade_estoque")
    ]
    # Kit trocado editando o item: a compensação de sempre (entra no SP, sai do CI).
    assert sorted(client.movs) == [("E", 202, 1), ("E", 203, 1), ("S", 102, 1), ("S", 103, 1)]


@pytest.mark.asyncio
async def test_segunda_passada_nao_mexe_mais(db: AsyncSession, monkeypatch):
    client = await _prepara(db, monkeypatch, "298788")
    await prio.aplicar_prioridade_estoque(db, numeros=["298788"])
    await db.commit()
    client.itens = [("dg054.ci+a001.ci", 1), ("dg052.ci+a001.ci", 1)]

    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["298788"])
    assert resumo["trocados"] == 0 and len(client.puts) == 1


@pytest.mark.asyncio
async def test_desligada_o_robo_faz_o_de_sempre(db: AsyncSession, monkeypatch):
    """Sem a regra, o Branco já está na prioridade e o Preto não tem prioridade:
    ninguém troca e o pedido segue dividido — é o que aconteceu no 298787."""
    monkeypatch.setattr(get_settings(), "prioridade_pedido_estoque_unico", False)
    client = await _prepara(db, monkeypatch, "298789")

    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["298789"])
    assert resumo["trocados"] == 0 and client.puts == []
