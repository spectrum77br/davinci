"""Sync do catálogo `situacao_bling` com o módulo Vendas do Bling (Eduardo
15/09: o dropdown "Situação no Bling ao fechar" mostrava situações que o Bling
já apagou, ex. "Enviado Geral CI")."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import SituacaoBling
from app.services import bling_situacoes_sync as sync
from app.services import chamados as chamados_svc

pytestmark = pytest.mark.asyncio

MODULOS = [
    {"id": 98309, "nome": "Compras", "criarSituacoes": True},
    {"id": 98310, "nome": "Vendas", "criarSituacoes": True},
]


class _FakeBling:
    def __init__(self, modulos: list[dict], situacoes: dict[int, list[dict]]):
        self.modulos = modulos
        self.situacoes = situacoes
        self.pedidos: list[int] = []

    async def list_situacoes_modulos(self) -> list[dict]:
        return self.modulos

    async def list_situacoes_modulo(self, id_modulo: int) -> list[dict]:
        self.pedidos.append(id_modulo)
        return self.situacoes.get(id_modulo, [])


async def test_sync_espelha_modulo_vendas(db):
    db.add_all(
        [
            SituacaoBling(id=83960, nome="Problemas"),
            SituacaoBling(id=99001, nome="Enviado Geral CI"),  # apagada no Bling
            SituacaoBling(id=83965, nome="Enviado Etiqueta", ativo=False),  # voltou
            SituacaoBling(id=6, nome="Em aberto (antigo)"),  # renomeada
        ]
    )
    await db.commit()
    fake = _FakeBling(
        MODULOS,
        {
            98310: [
                {"id": 6, "nome": "Em aberto", "idHerdado": 0, "cor": "#000000"},
                {"id": 83960, "nome": "Problemas"},
                {"id": 83965, "nome": "Enviado Etiqueta"},
                {"id": 545902, "nome": "Resolvido"},
                {"id": "x", "nome": "lixo"},  # sem id numérico: ignorada
                {"id": 7, "nome": ""},  # sem nome: ignorada
            ]
        },
    )
    resumo = await sync.sync_situacoes_bling(db, client=fake)
    assert fake.pedidos == [98310]  # só o módulo Vendas
    assert resumo == {
        "modulo": 98310,
        "total": 4,
        "novas": 1,
        "renomeadas": 1,
        "reativadas": 1,
        "inativadas": 1,
    }
    rows = {r.id: r for r in (await db.execute(select(SituacaoBling))).scalars()}
    # apagada no Bling FICA no catálogo (pedidos antigos apontam pra ela), só inativa
    assert rows[99001].ativo is False and rows[99001].nome == "Enviado Geral CI"
    assert rows[6].nome == "Em aberto" and rows[6].ativo is True
    assert rows[83965].ativo is True and rows[545902].ativo is True
    # dropdown dos Chamados: só as vivas
    assert await chamados_svc.situacoes_nomes(db) == [
        "Em aberto",
        "Enviado Etiqueta",
        "Problemas",
        "Resolvido",
    ]

    # segunda passada igual: nada muda
    de_novo = await sync.sync_situacoes_bling(db, client=fake)
    assert (
        de_novo["novas"],
        de_novo["renomeadas"],
        de_novo["reativadas"],
        de_novo["inativadas"],
    ) == (
        0,
        0,
        0,
        0,
    )


async def test_sync_nao_inativa_com_resposta_vazia(db):
    db.add(SituacaoBling(id=83960, nome="Problemas"))
    await db.commit()
    fake = _FakeBling(MODULOS, {98310: []})
    with pytest.raises(sync.SituacoesSyncError) as ei:
        await sync.sync_situacoes_bling(db, client=fake)
    assert ei.value.code == "bling_situacoes_vazio"
    row = (await db.execute(select(SituacaoBling).where(SituacaoBling.id == 83960))).scalar_one()
    assert row.ativo is True


async def test_sync_sem_modulo_vendas(db):
    fake = _FakeBling([{"id": 1, "nome": "Compras"}], {})
    with pytest.raises(sync.SituacoesSyncError) as ei:
        await sync.sync_situacoes_bling(db, client=fake)
    assert ei.value.code == "bling_modulo_vendas_nao_encontrado"
    assert fake.pedidos == []


async def test_modulo_vendas_prefere_nome_exato_e_cai_no_substring():
    assert sync.modulo_vendas([{"id": 2, "nome": "Pedidos de Venda"}])["id"] == 2
    assert (
        sync.modulo_vendas([{"id": 2, "nome": "Pedidos de Venda"}, {"id": 3, "nome": "VENDAS"}])[
            "id"
        ]
        == 3
    )
    assert sync.modulo_vendas([{"id": 1, "nome": "Compras"}, "lixo"]) is None
