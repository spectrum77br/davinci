"""SINCRONIZAR (17/11/2026): o "Sincronizar Todos" renomeado, com os consertos
do orquestrador.

  1. a lista de vínculos acima de 32.767 ids (limite de parâmetros do asyncpg)
     fazia TODO produto dar erro e o job sair 'succeeded' sem enviar nada
     (05/11/2026, job c89ad1cc, 33.731 vínculos) — o filtro agora é em Python;
  2. o job não aparece mais 'concluído' entre as rodadas de nova tentativa;
  3. produto que dá exceção é contado em `result.falhas_produto`;
  4. o produto 'E' escondido pelo ORGANIZAR continua recebendo 0: o
     orquestrador não conhece a tabela de ocultos.

O escopo de equipe e a trava global estão em test_mass_sync_exclusivity.py.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    Integration,
    IntegrationPlatform,
    OrganizarOculto,
    Product,
    ProductLink,
    SyncLog,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services import sync_orchestrator as module
from app.services.marketplaces.base import SyncResult, SyncStatus
from app.services.sync_orchestrator import SyncOrchestrator


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:sinc-{uuid.uuid4().hex[:8]}@davinci-test.com",
        email=f"sinc-{uuid.uuid4().hex[:8]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def shopee(db: AsyncSession, user: User) -> Integration:
    i = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.SHOPEE,
        name="shopee-teste",
        credentials=encrypt_json({"access_token": "tok"}),
    )
    db.add(i)
    await db.commit()
    await db.refresh(i)
    return i


async def _produto(
    db: AsyncSession,
    user: User,
    integ: Integration,
    *,
    situacao: str = "A",
    stock: int = 5,
) -> tuple[Product, ProductLink]:
    """Produto SEM vínculo do Bling (o envio usa o estoque guardado, sem
    chamar o Bling) e com 1 anúncio na Shopee."""
    p = Product(
        user_id=user.id,
        sku=f"sinc-{uuid.uuid4().hex[:6]}",
        name="produto",
        stock=stock,
        situacao=situacao,
    )
    db.add(p)
    await db.flush()
    link = ProductLink(
        user_id=user.id,
        product_id=p.id,
        integration_id=integ.id,
        platform=IntegrationPlatform.SHOPEE,
        external_id=f"ext-{uuid.uuid4().hex[:6]}",
        variation_id="0",
        stock=30,
    )
    db.add(link)
    await db.commit()
    await db.refresh(p)
    await db.refresh(link)
    return p, link


async def _job(db: AsyncSession, user: User) -> BackgroundJob:
    job = BackgroundJob(
        type=BackgroundJobType.SYNC_ALL,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


class _Loja:
    """Cliente falso do marketplace: registra o que recebeu; `respostas` é a
    fila de status (o último se repete)."""

    def __init__(self, respostas: list[SyncStatus] | None = None):
        self.calls: list[tuple[str, int]] = []
        self.respostas = list(respostas or [SyncStatus.OK])

    async def update_stock(self, link, qty, *, bling_store_id=None, force=False, **kw):
        self.calls.append((link.external_id, qty))
        st = self.respostas.pop(0) if len(self.respostas) > 1 else self.respostas[0]
        if st == SyncStatus.OK:
            return SyncResult(status=st, qty_before=link.stock, qty_after=qty)
        return SyncResult(status=st, error_code="timeout", error_detail="falso")


@pytest.fixture
def loja(monkeypatch) -> _Loja:
    lj = _Loja()
    monkeypatch.setattr(module.SyncOrchestrator, "_emit_link_alerts", AsyncMock())
    monkeypatch.setattr(module, "client_for", lambda *a, **kw: lj)
    return lj


async def _logs(db: AsyncSession, job_id) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(SyncLog).where(SyncLog.job_id == job_id)
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_lista_de_33_mil_vinculos_ainda_envia_e_grava_o_log(
    db: AsyncSession, user: User, shopee: Integration, loja: _Loja
):
    """Antes: `ProductLink.id IN (33.001 ids)` estourava o limite de 32.767
    parâmetros do asyncpg em TODO produto; o erro era engolido, nada era
    enviado e o job saía 'succeeded' com 0 sync_logs."""
    p, link = await _produto(db, user, shopee, stock=5)
    job = await _job(db, user)
    ids = [uuid.uuid4() for _ in range(33_000)] + [link.id]

    orch = SyncOrchestrator(db, user_id=user.id, job=job)
    await orch.run_parallel([p.id], only_link_ids=ids)

    assert loja.calls == [(link.external_id, 5)]
    assert await _logs(db, job.id) == 1
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.result["ok"] == 1
    assert job.result["falhas_produto"] == 0


@pytest.mark.asyncio
async def test_filtro_de_vinculos_continua_cortando_o_que_nao_esta_na_lista(
    db: AsyncSession, user: User, shopee: Integration, loja: _Loja
):
    """O filtro em Python faz o mesmo que o IN fazia: vínculo fora da lista
    (outra conta, no sync escopado) não recebe nada."""
    p, dentro = await _produto(db, user, shopee)
    fora = ProductLink(
        user_id=user.id,
        product_id=p.id,
        integration_id=shopee.id,
        platform=IntegrationPlatform.SHOPEE,
        external_id="fora-da-lista",
        variation_id="0",
        stock=30,
    )
    db.add(fora)
    await db.commit()

    orch = SyncOrchestrator(db, user_id=user.id)
    await orch.run_parallel([p.id], only_link_ids=[dentro.id])
    assert loja.calls == [(dentro.external_id, 5)]

    loja.calls.clear()
    await SyncOrchestrator(db, user_id=user.id).run([p], only_link_ids=[dentro.id])
    assert loja.calls == [(dentro.external_id, 5)]


@pytest.mark.asyncio
async def test_job_nao_fica_concluido_entre_as_rodadas_de_nova_tentativa(
    db: AsyncSession, user: User, shopee: Integration, loja: _Loja, monkeypatch
):
    """Antes cada rodada gravava SUCCEEDED/finished_at: o job aparecia
    'concluído' durante a espera de 30 s (a trava de massa abria e outra
    massa podia começar) e o started_at era sobrescrito a cada rodada."""
    monkeypatch.setattr(module, "RESYNC_WAIT_FIRST_SECONDS", 0)
    monkeypatch.setattr(module, "RESYNC_WAIT_SUBSEQUENT_SECONDS", 0)
    loja.respostas = [SyncStatus.RETRYABLE, SyncStatus.OK]
    p, link = await _produto(db, user, shopee)
    job = await _job(db, user)

    original = SyncOrchestrator.run_parallel
    vistos: list[tuple] = []

    async def _espiao(self, *a, **kw):
        r = await original(self, *a, **kw)
        async with SessionLocal() as s:
            j = await s.get(BackgroundJob, job.id)
            vistos.append((j.status, j.finished_at, j.started_at))
        return r

    monkeypatch.setattr(SyncOrchestrator, "run_parallel", _espiao)
    orch = SyncOrchestrator(db, user_id=user.id, job=job)
    await orch.run_with_retry([p.id])

    assert len(vistos) == 2  # rodada inicial + 1 nova tentativa
    assert all(st == BackgroundJobStatus.RUNNING for st, _, _ in vistos)
    assert all(fim is None for _, fim, _ in vistos)
    assert vistos[0][2] is not None and vistos[0][2] == vistos[1][2]
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.finished_at is not None
    assert job.started_at == vistos[0][2]
    assert loja.calls == [(link.external_id, 5), (link.external_id, 5)]


@pytest.mark.asyncio
async def test_produto_que_da_excecao_e_contado_em_falhas_produto(
    db: AsyncSession, user: User, shopee: Integration, loja: _Loja, monkeypatch
):
    bom, link_bom = await _produto(db, user, shopee)
    ruim, _ = await _produto(db, user, shopee)
    job = await _job(db, user)

    original = SyncOrchestrator._process_link

    async def _quebra_no_ruim(self, product, link, **kw):
        if product.id == ruim.id:
            raise RuntimeError("quebrou")
        return await original(self, product, link, **kw)

    monkeypatch.setattr(SyncOrchestrator, "_process_link", _quebra_no_ruim)
    orch = SyncOrchestrator(db, user_id=user.id, job=job)
    await orch.run_with_retry([bom.id, ruim.id])

    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.result["falhas_produto"] == 1
    # quais: a tela lista os SKUs no resumo do Sincronizar
    assert job.result["falhas_amostra"] == [ruim.sku]
    assert job.result["ok"] == 1
    assert loja.calls == [(link_bom.external_id, 5)]


@pytest.mark.asyncio
async def test_rodada_que_quebra_marca_failed_em_vez_de_ficar_running(
    db: AsyncSession, user: User, shopee: Integration, loja: _Loja, monkeypatch
):
    """Com o status final gravado só no fim, um erro fora do laço por produto
    deixaria o job RUNNING (prendendo a trava global até o sinal vencer)."""
    p, _ = await _produto(db, user, shopee)
    job = await _job(db, user)

    async def _quebra(self, *a, **kw):
        raise RuntimeError("banco caiu")

    monkeypatch.setattr(SyncOrchestrator, "run_parallel", _quebra)
    orch = SyncOrchestrator(db, user_id=user.id, job=job)
    with pytest.raises(RuntimeError):
        await orch.run_with_retry([p.id])
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.FAILED
    assert job.finished_at is not None
    assert "banco caiu" in job.error


@pytest.mark.asyncio
async def test_produto_excluido_oculto_pelo_organizar_ainda_recebe_zero(
    db: AsyncSession, user: User, shopee: Integration, loja: _Loja
):
    """O ORGANIZAR só esconde da TELA: o vínculo vivo de um produto 'E'
    oculto continua recebendo o 0 (marca sku_excluido)."""
    p, link = await _produto(db, user, shopee, situacao="E", stock=7)
    lote = uuid.uuid4()
    db.add_all(
        [
            OrganizarOculto(
                tabela="products",
                registro_id=p.id,
                motivo="produto_excluido_com_anuncio",
                lote_id=lote,
                oculto_por=user.id,
            ),
            OrganizarOculto(
                tabela="product_links",
                registro_id=link.id,
                motivo="vinculo_morto",
                lote_id=lote,
                oculto_por=user.id,
            ),
        ]
    )
    await db.commit()
    job = await _job(db, user)

    orch = SyncOrchestrator(db, user_id=user.id, job=job)
    await orch.run_with_retry([p.id])

    assert loja.calls == [(link.external_id, 0)]
    await db.refresh(link)
    assert link.stock == 0
    assert link.last_error.startswith("sku_excluido")
    # O orquestrador nem conhece a tabela de ocultos.
    assert "organizar" not in inspect.getsource(module)


@pytest.mark.asyncio
async def test_sincronizar_continua_enfileirando_com_os_mesmos_argumentos(
    client, make_user, auth_as
):
    """Admin: igual a hoje — null = todas as contas (6º arg None), força e
    include_all_stock passam direto."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    pool = AsyncMock()
    pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "x"}))
    with patch("app.routers.sync.get_arq_sync_pool", return_value=pool):
        r = await client.post(
            "/api/jobs/sync-all",
            json={"integration_ids": None, "product_ids": None,
                  "include_all_stock": True, "force": False},
        )
    assert r.status_code == 201
    args = pool.enqueue_job.await_args.args
    assert args[0] == "sync_all_run"
    assert args[3] is None and args[4] is True and args[5] is None and args[6] is False


@pytest.mark.asyncio
async def test_rodada_repetida_depois_de_interrompida_recomeca_e_trava_ve(
    db: AsyncSession, user: User, shopee: Integration, loja: _Loja, monkeypatch
):
    """Achado da revisão: publicação no meio grava FAILED/finished_at/erro e o
    arq roda o job de novo. A rodada repetida recomeça do zero: a trava global
    de massa a vê enquanto roda e o fim sai sem o erro 'interrompido'."""
    import asyncio

    from app.services.advisory_lock import massa_em_andamento

    p, _ = await _produto(db, user, shopee)
    job = await _job(db, user)

    original = SyncOrchestrator.run_parallel

    async def _cancela(self, *a, **kw):
        raise asyncio.CancelledError()

    monkeypatch.setattr(SyncOrchestrator, "run_parallel", _cancela)
    with pytest.raises(asyncio.CancelledError):
        await SyncOrchestrator(db, user_id=user.id, job=job).run_with_retry([p.id])
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.FAILED and job.finished_at is not None

    vistos = []

    async def _espia(self, *a, **kw):
        self.job.status = BackgroundJobStatus.RUNNING
        await self.session.commit()
        async with SessionLocal() as s2:
            vistos.append(await massa_em_andamento(s2))
        return await original(self, *a, **kw)

    monkeypatch.setattr(SyncOrchestrator, "run_parallel", _espia)
    await SyncOrchestrator(db, user_id=user.id, job=job).run_with_retry([p.id])
    assert vistos and vistos[0] is not None and vistos[0]["job_id"] == str(job.id)
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.error is None and job.finished_at is not None
