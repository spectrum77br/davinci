"""Produto criado no Bling entra no DaVinci sem esperar movimento de estoque.

Eduardo, 29/09/2026: "em devoluções não está aparecendo para colocar no a009.cd,
sendo que ele está ativo". Só o webhook de ESTOQUE criava produto novo; criado
com estoque 0 e sem venda, nunca chegava (147 ativos faltando).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Integration,
    IntegrationPlatform,
    Product,
    ProductLink,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services import bling_product_create
from app.services import bling_produtos_novos as novos


class _Resp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": self._data}


class BlingFalso:
    def __init__(self, produtos: list[dict]):
        self.produtos = produtos
        self.detalhes = {p["id"]: p for p in produtos}
        self.chamadas: list[dict] = []

    async def _request(self, metodo, caminho, params=None, **kw):
        self.chamadas.append(params or {})
        pagina = (params or {}).get("pagina", 1)
        return _Resp(self.produtos if pagina == 1 else [])

    async def list_products(self):
        for p in self.produtos:
            yield p

    async def get_product(self, bling_id: int) -> dict[str, Any]:
        p = self.detalhes[bling_id]
        return {"id": p["id"], "codigo": p["codigo"], "nome": p["nome"], "situacao": p["situacao"],
                "formato": "S", "estoque": {"saldoVirtualTotal": p.get("estoque", 0)}}


@pytest.fixture
async def cenario(db: AsyncSession, monkeypatch):
    u = User(open_id=f"email:dono-{uuid.uuid4().hex[:6]}@x", email=f"dono-{uuid.uuid4().hex[:6]}@x",
             name="dono", role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.flush()
    db.add(Integration(user_id=u.id, platform=IntegrationPlatform.BLING, name="bling",
                       credentials=encrypt_json({"access_token": "x"})))
    db.add(Product(user_id=u.id, sku="a009.ci", name="Fonte Motorola", stock=194,
                   bling_product_id=111))
    await db.commit()

    def _montar(produtos):
        bling = BlingFalso(produtos)

        async def _cliente(session):
            return bling

        monkeypatch.setattr(bling_product_create, "_bling_client_for_user", _cliente)
        return bling

    return u, _montar


@pytest.mark.asyncio
async def test_produto_novo_sem_estoque_entra_com_vinculo_do_bling(db: AsyncSession, cenario):
    dono, montar = cenario
    montar([
        {"id": 111, "codigo": "a009.ci", "nome": "Fonte Motorola", "situacao": "A"},
        {"id": 222, "codigo": "a009.cd", "nome": "Fonte Motorola", "situacao": "A", "estoque": 0},
        {"id": 333, "codigo": "velho.pi", "nome": "Inativo", "situacao": "I"},
    ])
    resumo = await novos.importar_produtos_novos(db, completo=False, pausa=0)
    assert resumo["criados"] == 1 and resumo["falhas"] == 0

    novo = (await db.execute(select(Product).where(Product.sku == "a009.cd"))).scalar_one()
    assert novo.bling_product_id == 222 and novo.situacao == "A" and novo.user_id == dono.id
    vinculo = (await db.execute(
        select(ProductLink).where(ProductLink.product_id == novo.id)
    )).scalar_one()
    assert vinculo.platform == IntegrationPlatform.BLING and vinculo.external_id == "222"
    # inativo não entra
    assert (await db.execute(select(Product).where(Product.sku == "velho.pi"))).first() is None


@pytest.mark.asyncio
async def test_rodar_de_novo_nao_duplica(db: AsyncSession, cenario):
    _dono, montar = cenario
    montar([{"id": 222, "codigo": "a009.cd", "nome": "Fonte", "situacao": "A"}])
    await novos.importar_produtos_novos(db, completo=False, pausa=0)
    resumo = await novos.importar_produtos_novos(db, completo=False, pausa=0)
    assert resumo["criados"] == 0 and resumo["faltando"] == 0
    assert len((await db.execute(select(Product).where(Product.sku == "a009.cd"))).all()) == 1


@pytest.mark.asyncio
async def test_rapido_le_ultimos_incluidos_e_completo_le_todos(db: AsyncSession, cenario):
    _dono, montar = cenario
    bling = montar([{"id": 444, "codigo": "dg1035.pi", "nome": "C2", "situacao": "A"}])
    await novos.importar_produtos_novos(db, completo=False, pausa=0)
    assert bling.chamadas and bling.chamadas[0].get("criterio") == 1
    resumo = await novos.importar_produtos_novos(db, completo=True, pausa=0)
    assert resumo["lidos"] == 1


def test_agendado_fora_dos_minutos_de_pico():
    from app.worker import WorkerSettings

    crons = {c.name: c for c in WorkerSettings.cron_jobs}
    assert crons["cron:produtos_novos_bling_tick"].minute == {7, 22, 37, 52}
    assert crons["cron:produtos_novos_bling_completo"].hour == 15
