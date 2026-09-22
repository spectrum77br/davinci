"""Vender num lote tem que re-publicar os anúncios dos IRMÃOS.

Defeito encontrado na revisão de 22/09/2026: o anúncio passou a mostrar o total
da família, mas o webhook do Bling só avisa do produto que mudou e a varredura
diária pula quem tem estoque próprio alto. Sem isto, vender no dg053.sp deixa o
anúncio do dg053.ci preso no total velho até ele mesmo vender alguma coisa.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker
from app.models import BackgroundJob, BackgroundJobStatus, BackgroundJobType, Product
from app.services import estoque_familia


class _Config:
    def __init__(self, ativo=True, prefixos="dg053"):
        self.estoque_familia_ativo = ativo
        self.estoque_familia_prefixos = prefixos
        self.estoque_familia_minimo = 5


async def _produto(db, user, sku, stock=100):
    p = Product(user_id=user.id, sku=sku, name=sku, stock=stock, situacao="A")
    db.add(p)
    await db.flush()
    return p


@pytest.fixture
def espiar_sync(monkeypatch):
    """Troca o orquestrador por um que só anota quais produtos recebeu."""
    passadas: list[list[str]] = []

    class OrqFalso:
        def __init__(self, *a, **k):
            pass

        async def run(self, produtos, only_link_ids=None):
            passadas.append([p.sku for p in produtos])

    monkeypatch.setattr(worker, "SyncOrchestrator", OrqFalso)
    return passadas


async def test_venda_num_lote_republica_os_irmaos(
    db: AsyncSession, make_user, monkeypatch, espiar_sync
):
    monkeypatch.setattr(estoque_familia, "get_settings", lambda: _Config())
    u = await make_user()
    vendido = await _produto(db, u, "dg053.sp", 900)
    await _produto(db, u, "dg053.ci", 217)
    await _produto(db, u, "dg053.ra", 50)
    job = BackgroundJob(
        type=BackgroundJobType.SYNC_PRODUCT,
        status=BackgroundJobStatus.PENDING,
        created_by=u.id,
    )
    db.add(job)
    await db.commit()

    await worker.sync_product_run({}, str(job.id), str(u.id), str(vendido.id), None)

    assert espiar_sync[0] == ["dg053.sp"], "o produto que vendeu vai primeiro"
    assert sorted(espiar_sync[1]) == ["dg053.ci", "dg053.ra"], "os irmãos vêm depois"


async def test_com_a_soma_desligada_so_o_proprio_produto_e_publicado(
    db: AsyncSession, make_user, monkeypatch, espiar_sync
):
    monkeypatch.setattr(estoque_familia, "get_settings", lambda: _Config(ativo=False))
    u = await make_user()
    vendido = await _produto(db, u, "dg053.sp", 900)
    await _produto(db, u, "dg053.ci", 217)
    job = BackgroundJob(
        type=BackgroundJobType.SYNC_PRODUCT,
        status=BackgroundJobStatus.PENDING,
        created_by=u.id,
    )
    db.add(job)
    await db.commit()

    await worker.sync_product_run({}, str(job.id), str(u.id), str(vendido.id), None)
    assert espiar_sync == [["dg053.sp"]]


async def test_linha_fora_da_soma_nao_arrasta_irmaos(
    db: AsyncSession, make_user, monkeypatch, espiar_sync
):
    monkeypatch.setattr(estoque_familia, "get_settings", lambda: _Config(prefixos="dg057"))
    u = await make_user()
    vendido = await _produto(db, u, "dg053.sp", 900)
    await _produto(db, u, "dg053.ci", 217)
    job = BackgroundJob(
        type=BackgroundJobType.SYNC_PRODUCT,
        status=BackgroundJobStatus.PENDING,
        created_by=u.id,
    )
    db.add(job)
    await db.commit()

    await worker.sync_product_run({}, str(job.id), str(u.id), str(vendido.id), None)
    assert espiar_sync == [["dg053.sp"]]


def test_prefixos_da_familia_saem_da_configuracao(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", lambda: _Config(prefixos="dg052,dg053"))
    assert worker._prefixos_familia() == ["dg052", "dg053"]
    monkeypatch.setattr(worker, "get_settings", lambda: _Config(ativo=False))
    assert worker._prefixos_familia() == []
