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


@pytest.mark.asyncio
async def test_completo_reativa_produto_ativo_no_bling(db: AsyncSession, cenario):
    """dg078.pi+a020.pi (29/09): ativo no Bling, 'I' no DaVinci — sumia das telas."""
    dono, montar = cenario
    db.add(Product(user_id=dono.id, sku="dg078.pi+a020.pi", name="C3 + Óculos", stock=70,
                   bling_product_id=555, situacao="I"))
    await db.commit()
    montar([{"id": 555, "codigo": "dg078.pi+a020.pi", "nome": "C3 + Óculos", "situacao": "A"}])
    resumo = await novos.importar_produtos_novos(db, completo=True, pausa=0)
    assert resumo["reativados"] == 1 and resumo["criados"] == 0
    prod = (await db.execute(select(Product).where(Product.bling_product_id == 555))).scalar_one()
    await db.refresh(prod)
    assert prod.situacao == "A"


@pytest.mark.asyncio
async def test_pagina_recusada_pelo_bling_e_tentada_de_novo(db: AsyncSession, cenario):
    """A 1ª importação de 29/09 caiu num 429 da lista inteira (o custo diário
    listava ao mesmo tempo)."""
    from app.services.marketplaces.bling import BlingCloudflareError

    _dono, montar = cenario
    bling = montar([{"id": 666, "codigo": "dg1035.pi", "nome": "C2", "situacao": "A"}])
    original = bling._request
    falhas = {"n": 2}

    async def _com_429(metodo, caminho, params=None, **kw):
        if falhas["n"]:
            falhas["n"] -= 1
            raise BlingCloudflareError("status=429 cf_html=False")
        return await original(metodo, caminho, params=params, **kw)

    bling._request = _com_429
    resumo = await novos.importar_produtos_novos(db, completo=True, pausa=0)  # pausa 0: sem espera
    assert resumo["criados"] == 1


async def _ativo(db, dono, sku, bling_id, situacao="A", stock=5):
    db.add(Product(user_id=dono.id, sku=sku, name=sku, stock=stock,
                   bling_product_id=bling_id, situacao=situacao))
    await db.commit()


@pytest.mark.asyncio
async def test_completo_inativa_quem_saiu_da_lista_do_bling(db: AsyncSession, cenario):
    """Revisão 29/09: nada rebaixava A→I — inativado no Bling seguia ativo aqui
    e, com a soma para todas as linhas, emprestaria estoque aos irmãos."""
    dono, montar = cenario
    await _ativo(db, dono, "dg210.pi", 777, stock=15)
    montar([{"id": 111, "codigo": "a009.ci", "nome": "Fonte", "situacao": "A"}])
    resumo = await novos.importar_produtos_novos(db, completo=True, pausa=0)
    assert resumo["inativados"] == 1
    prod = (await db.execute(select(Product).where(Product.bling_product_id == 777))).scalar_one()
    await db.refresh(prod)
    assert prod.situacao == "I"


@pytest.mark.asyncio
async def test_lista_curta_do_bling_nao_inativa_nada(db: AsyncSession, cenario):
    """Se a lista do Bling vier curta demais (menos de 90% dos ativos daqui),
    é anomalia: não inativa ninguém."""
    dono, montar = cenario
    for i in range(10):
        await _ativo(db, dono, f"x{i}.pi", 900 + i)
    montar([{"id": 111, "codigo": "a009.ci", "nome": "Fonte", "situacao": "A"}])
    resumo = await novos.importar_produtos_novos(db, completo=True, pausa=0)
    assert resumo["inativados"] == 0
    assert (await db.execute(select(Product).where(Product.situacao == "I"))).first() is None


@pytest.mark.asyncio
async def test_rapido_nunca_inativa(db: AsyncSession, cenario):
    """Os 'últimos incluídos' não são a lista inteira — o tick de 15 min não
    pode concluir que alguém saiu do Bling."""
    dono, montar = cenario
    await _ativo(db, dono, "dg210.pi", 777)
    montar([{"id": 111, "codigo": "a009.ci", "nome": "Fonte", "situacao": "A"}])
    resumo = await novos.importar_produtos_novos(db, completo=False, pausa=0)
    assert "inativados" not in resumo


@pytest.mark.asyncio
async def test_reativacao_so_pelo_id_do_bling(db: AsyncSession, cenario):
    """Cópia velha com o mesmo código mas outro id (produto apagado e recriado
    no Bling) não é reativada."""
    dono, montar = cenario
    await _ativo(db, dono, "dg300.pi", 1234, situacao="E", stock=40)
    montar([{"id": 5678, "codigo": "dg300.pi", "nome": "Novo", "situacao": "A"}])
    resumo = await novos.importar_produtos_novos(db, completo=True, pausa=0)
    assert resumo["reativados"] == 0
    velho = (await db.execute(select(Product).where(Product.bling_product_id == 1234))).scalar_one()
    assert velho.situacao == "E"


# ---- 17/11/2026: botão Atualizar (só cria; trava 'pbln'; IntegrityError) -------


@pytest.mark.asyncio
async def test_botao_atualizar_so_cria_nao_reativa_nem_inativa(db: AsyncSession, cenario):
    """alterar_situacao=False (o botão): lê a lista inteira e só cria o que
    falta — o 'I' que está ativo no Bling continua 'I' e o 'A' que sumiu do
    Bling continua 'A'. O cron diário continua reativando/inativando."""
    dono, montar = cenario
    await _ativo(db, dono, "dg078.pi", 555, situacao="I")
    await _ativo(db, dono, "dg210.pi", 777)  # ativo aqui, fora da lista do Bling
    montar([
        {"id": 111, "codigo": "a009.ci", "nome": "Fonte", "situacao": "A"},
        {"id": 555, "codigo": "dg078.pi", "nome": "C3", "situacao": "A"},
        {"id": 888, "codigo": "novo.sp", "nome": "Novo", "situacao": "A"},
    ])
    resumo = await novos.importar_produtos_novos(
        db, completo=True, pausa=0, alterar_situacao=False
    )
    assert resumo["criados"] == 1 and resumo["skus_criados"] == ["novo.sp"]
    assert resumo["reativados"] == 0 and "inativados" not in resumo
    por_id = {
        p.bling_product_id: p
        for p in (await db.execute(select(Product))).scalars()
    }
    for p in por_id.values():
        await db.refresh(p)
    assert por_id[555].situacao == "I"
    assert por_id[777].situacao == "A"


@pytest.mark.asyncio
async def test_produto_criado_ao_mesmo_tempo_conta_como_ja_existia(
    db: AsyncSession, cenario, monkeypatch
):
    """O webhook de estoque (ou a devolução) criou o mesmo produto no meio:
    o IntegrityError do índice de SKU desfaz só aquele e a lista segue."""
    from sqlalchemy.exc import IntegrityError

    _dono, montar = cenario
    montar([
        {"id": 901, "codigo": "corrida.sp", "nome": "Corrida", "situacao": "A"},
        {"id": 902, "codigo": "depois.sp", "nome": "Depois", "situacao": "A"},
    ])
    original = bling_product_create.run_auto_create_product_from_bling

    async def _corrida(session, *, bling_product_id, user_id):
        if bling_product_id == 901:
            raise IntegrityError("INSERT", {}, Exception("uq_products_user_id_sku"))
        return await original(session, bling_product_id=bling_product_id, user_id=user_id)

    monkeypatch.setattr(bling_product_create, "run_auto_create_product_from_bling", _corrida)
    resumo = await novos.importar_produtos_novos(db, completo=True, pausa=0)
    assert resumo["ja_existia"] == 1 and resumo["criados"] == 1
    assert resumo["skus_criados"] == ["depois.sp"]


@pytest.mark.asyncio
async def test_trava_pbln_uma_rodada_por_vez(db: AsyncSession, cenario, monkeypatch):
    """O tick de 15 min, o completo e o botão dividem a trava 'pbln': com ela
    ocupada a rodada sai na hora ('pulado'); livre, roda normal. Os dois crons
    passam pela trava."""
    from sqlalchemy import text as sa_text

    from app import worker
    from app.db import session_scope
    from app.services.advisory_lock import SYNC_NAMESPACE

    _dono, montar = cenario
    montar([{"id": 222, "codigo": "a009.cd", "nome": "Fonte", "situacao": "A"}])
    async with session_scope() as outra:
        await outra.execute(
            sa_text("SELECT pg_advisory_xact_lock(:ns, :k)"),
            {"ns": SYNC_NAMESPACE, "k": novos._PRODUTOS_NOVOS_LOCK_KEY},
        )
        assert await novos.importar_produtos_novos_com_trava(completo=False, pausa=0) == {
            "pulado": "rodada_automatica_em_andamento"
        }
    resumo = await novos.importar_produtos_novos_com_trava(completo=False, pausa=0)
    assert resumo["criados"] == 1

    chamadas: list[dict] = []
    respostas = [
        {"pulado": "rodada_automatica_em_andamento"},  # o tick
        {"pulado": "rodada_automatica_em_andamento"},  # o completo, 1ª vez
        {"lidos": 1, "criados": 0},  # o completo, depois de esperar
    ]

    async def _espiao(**kw):
        chamadas.append(kw)
        return respostas.pop(0)

    monkeypatch.setattr(novos, "importar_produtos_novos_com_trava", _espiao)
    monkeypatch.setattr(worker, "_BLING_COMPLETO_ESPERA_S", 0.0)
    await worker.produtos_novos_bling_tick({})
    # O completo diário não desiste com a trava ocupada (17/11/2026: só ele
    # reativa/inativa — o Atualizar não): espera e tenta de novo.
    await worker.produtos_novos_bling_completo({})
    assert chamadas == [{"completo": False}, {"completo": True}, {"completo": True}]

    # Ocupada o tempo todo: tenta o número de vezes e sai sem erro.
    chamadas.clear()

    async def _sempre_ocupada(**kw):
        chamadas.append(kw)
        return {"pulado": "rodada_automatica_em_andamento"}

    monkeypatch.setattr(novos, "importar_produtos_novos_com_trava", _sempre_ocupada)
    monkeypatch.setattr(worker, "_BLING_COMPLETO_TENTATIVAS", 3)
    await worker.produtos_novos_bling_completo({})
    assert chamadas == [{"completo": True}] * 3
