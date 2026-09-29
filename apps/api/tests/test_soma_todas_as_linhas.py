"""Soma por família ligada para TODAS as linhas (ESTOQUE_FAMILIA_PREFIXOS vazio).

Eduardo, 29/09/2026: "precisamos jogar a soma total, de todos os produtos".
A auditoria do mesmo dia achou o que quebrava com a lista vazia: a varredura
diária tratava "todas" como "desligada" e pulava as famílias de estoque alto.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker
from app.config import get_settings
from app.models import BackgroundJob, BackgroundJobStatus, BackgroundJobType, Product
from app.services import estoque_familia, prioridade_estoque


@pytest.fixture
def soma(monkeypatch):
    cfg = get_settings()

    def _f(ativo=True, prefixos=""):
        monkeypatch.setattr(cfg, "estoque_familia_ativo", ativo)
        monkeypatch.setattr(cfg, "estoque_familia_prefixos", prefixos)
        monkeypatch.setattr(cfg, "estoque_familia_minimo", 5)

    return _f


@pytest.fixture
def espiar_varredura(monkeypatch):
    passadas: list[list] = []

    class OrqFalso:
        def __init__(self, *a, **k):
            pass

        async def run_with_retry(self, pids, only_link_ids=None):
            passadas.append(list(pids))

    monkeypatch.setattr(worker, "SyncOrchestrator", OrqFalso)
    return passadas


async def _p(db, u, sku, stock, situacao="A"):
    p = Product(user_id=u.id, sku=sku, name=sku, stock=stock, situacao=situacao)
    db.add(p)
    await db.flush()
    return p


async def _varredura(db, u):
    job = BackgroundJob(type=BackgroundJobType.SYNC_ALL, status=BackgroundJobStatus.PENDING,
                        created_by=u.id, payload={})
    db.add(job)
    await db.commit()
    await worker.sync_all_run({}, str(job.id), str(u.id), None)


@pytest.mark.asyncio
async def test_varredura_com_soma_para_todas_pega_familia_de_estoque_alto(
    db: AsyncSession, make_user, soma, espiar_varredura
):
    """dg053.ci (700) é de família com o .sp: o número dele depende do irmão,
    então entra mesmo com estoque alto. x500.pi (500) é família de um lote só:
    publica o próprio saldo e segue a regra do estoque baixo (fica de fora)."""
    soma(prefixos="")
    u = await make_user()
    ci = await _p(db, u, "dg053.ci", 700)
    sp = await _p(db, u, "dg053.sp", 900)
    sozinho = await _p(db, u, "x500.pi", 500)
    baixo = await _p(db, u, "x600.pi", 3)
    await _varredura(db, u)
    ids = set(espiar_varredura[0])
    assert {ci.id, sp.id, baixo.id} <= ids
    assert sozinho.id not in ids


@pytest.mark.asyncio
async def test_varredura_com_soma_desligada_so_estoque_baixo(
    db: AsyncSession, make_user, soma, espiar_varredura
):
    soma(ativo=False, prefixos="")
    u = await make_user()
    ci = await _p(db, u, "dg053.ci", 700)
    await _p(db, u, "dg053.sp", 900)
    baixo = await _p(db, u, "x600.pi", 3)
    await _varredura(db, u)
    assert set(espiar_varredura[0]) == {baixo.id}
    assert ci.id not in espiar_varredura[0]


@pytest.mark.asyncio
async def test_produto_excluido_nao_promete_o_estoque_dos_irmaos(db: AsyncSession, make_user, soma):
    soma(prefixos="")
    u = await make_user()
    excluido = await _p(db, u, "dg099.ci", 2, situacao="E")
    await _p(db, u, "dg099.sp", 50)
    ativo = await _p(db, u, "dg099.pi", 3)
    await db.commit()
    assert await estoque_familia.saldo_publicavel(db, excluido) == 2
    assert await estoque_familia.saldo_publicavel(db, ativo) == 53


class _Bling:
    def __init__(self, saldos):
        self.saldos = saldos
        self.consultas: list[str] = []

    async def find_active_product_by_sku(self, sku, estrito=False):
        self.consultas.append(sku.lower())
        if sku.lower() not in self.saldos:
            return None
        return {"id": 1, "sku": sku.lower(), "name": sku, "stock": self.saldos[sku.lower()]}


@pytest.mark.asyncio
async def test_robo_nao_consulta_lote_que_nao_existe(soma):
    """Com a soma para todas, um lote negativo fazia o robô perguntar ao Bling
    pelos 4 irmãos — mesmo os que nem existem como produto."""
    soma(prefixos="")
    bling = _Bling({"a003.pi": -1, "a003.sa": 40})
    alvo, _ = await prioridade_estoque._lote_com_saldo(
        bling, {}, codigo="a003.pi", tag_atual="pi", prioridade=None, qtd=1,
        redirecionar=True, existentes={"a003.pi", "a003.sa"},
    )
    assert alvo == "a003.sa"
    assert set(bling.consultas) == {"a003.pi", "a003.sa"}  # nada de a003.ci/ra/sp
