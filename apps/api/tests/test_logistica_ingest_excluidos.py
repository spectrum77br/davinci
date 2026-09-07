"""`cleanup_excluidos`: pedido EXCLUÍDO no Bling sai da aba Logística, e a
venda reimportada com número novo herda observação/chamado/divergência da
linha velha (caso real 01/09: Shopee 260901V0YYRXR4 → 293636 excluído +
293657 vivo, aparecia em dobro)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import BlingOrder
from app.models.logistica import Logistica
from app.services import logistica_ingest as svc

pytestmark = pytest.mark.asyncio


def _pedido(*, numero: str, numeroloja: str, situacao: str) -> BlingOrder:
    return BlingOrder(
        id=uuid4(),
        numero=numero,
        numeroloja=numeroloja,
        bling_id=int(numero),
        situacao=situacao,
        loja="77",
        data=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        item_index=0,
        item_codigo="i232.sa",
        item_descricao="Apple Watch SE 3",
        item_quantidade=1,
    )


def _linha(*, numero: str, numeroloja: str, **campos: str) -> Logistica:
    return Logistica(
        id=uuid4(),
        pedido_bling=numero,
        pedido_marketplace=numeroloja,
        plataforma="Shopee",
        conta="jlas",
        status_bling="Em aberto",
        meli_status={},
        **campos,
    )


async def test_cleanup_excluidos_apaga_e_herda(db):
    venda = "260901V0YYRXR4"
    db.add_all(
        [
            _pedido(numero="293636", numeroloja=venda, situacao="excluido"),
            _pedido(numero="293657", numeroloja=venda, situacao="83953"),
            _pedido(numero="300001", numeroloja="ORFAO1", situacao="excluido"),
            _pedido(numero="300002", numeroloja="VIVO2", situacao="6"),
        ]
    )
    db.add_all(
        [
            _linha(numero="293636", numeroloja=venda, observacao="cliente reclamou", chamado="C-1"),
            _linha(numero="293657", numeroloja=venda),
            _linha(numero="300001", numeroloja="ORFAO1", observacao="sem substituto"),
            _linha(numero="300002", numeroloja="VIVO2", observacao="fica"),
        ]
    )
    await db.commit()

    removidos = await svc.cleanup_excluidos(db)
    assert removidos == 2

    linhas = {
        row.pedido_bling: row
        for row in (await db.execute(select(Logistica))).scalars().all()
    }
    assert set(linhas) == {"293657", "300002"}
    # a linha nova da mesma venda herdou o que o operador tinha escrito
    assert linhas["293657"].observacao == "cliente reclamou"
    assert linhas["293657"].chamado == "C-1"
    # quem não tem nada a ver segue intacto
    assert linhas["300002"].observacao == "fica"

    # idempotente: rodar de novo não apaga mais nada
    assert await svc.cleanup_excluidos(db) == 0


async def test_cleanup_excluidos_nao_sobrescreve_o_que_a_nova_ja_tem(db):
    venda = "2609SHOPEE"
    db.add_all(
        [
            _pedido(numero="400001", numeroloja=venda, situacao="excluido"),
            _pedido(numero="400002", numeroloja=venda, situacao="6"),
        ]
    )
    db.add_all(
        [
            _linha(numero="400001", numeroloja=venda, observacao="velha", divergencia="Sim"),
            _linha(numero="400002", numeroloja=venda, observacao="nova"),
        ]
    )
    await db.commit()

    assert await svc.cleanup_excluidos(db) == 1
    nova = (
        await db.execute(select(Logistica).where(Logistica.pedido_bling == "400002"))
    ).scalar_one()
    assert nova.observacao == "nova"  # o que já estava preenchido fica
    assert nova.divergencia == "Sim"  # o que estava vazio herda
