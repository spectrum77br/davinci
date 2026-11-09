"""SKU excluído no Bling zera os anúncios (09/11/2026).

Pedido do Eduardo: "esses SKUs são excluídos do Bling e no DaVinci não faz
mais nada com o SKU [...] continua vendendo com o estoque antigo, que está
travado no DaVinci. O certo mesmo era: se tá excluído, zera o estoque."

Antes: produto apagado no Bling (404) sem ativo com o mesmo SKU levantava
RuntimeError e os anúncios ficavam SKIPPED para sempre com o número antigo;
na lixeira (situação E) publicava o saldo dele; e o excluído com estoque 10+
nem entrava na varredura da madrugada.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    IntegrationPlatform,
    Product,
    ProductLink,
    User,
    UserRole,
    UserStatus,
)
from app.services import estoque_familia
from app.services import refresh_bling_stock as refresh
from app.services import sync_orchestrator as module
from app.services.marketplaces.base import SyncResult, SyncStatus
from app.services.marketplaces.bling import BlingClient

# ── Bling: o que conta como excluído ─────────────────────────────────────────


class _Resp:
    def __init__(self, status: int, data):
        self.status_code = status
        self._data = data
        self.request = httpx.Request("GET", "https://bling.test")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "erro",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self):
        return {"data": self._data}


class _BlingHttp(BlingClient):
    """BlingClient com o HTTP trocado: `produtos` = id → detalhe (ou 404);
    `busca` = lista do /produtos?codigo= (ou uma exceção)."""

    def __init__(self, produtos: dict, busca=None):
        self.produtos = produtos
        self.busca = busca if busca is not None else []

    async def get_product(self, bling_product_id: int) -> dict:
        p = self.produtos.get(bling_product_id)
        if p is None:
            req = httpx.Request("GET", "https://bling.test")
            raise httpx.HTTPStatusError(
                "404", request=req, response=httpx.Response(404, request=req)
            )
        return p

    async def _request(self, method, path, **kwargs):
        assert path == "/produtos"
        if isinstance(self.busca, Exception):
            raise self.busca
        return _Resp(200, self.busca)


def _detalhe(pid: int, situacao: str, saldo: int, codigo: str = "x1") -> dict:
    return {
        "id": pid,
        "codigo": codigo,
        "nome": "n",
        "situacao": situacao,
        "estoque": {"saldoVirtualTotal": saldo},
    }


@pytest.mark.asyncio
async def test_apagado_sem_ativo_vira_excluido_com_zero():
    smart = await _BlingHttp({}).get_product_stock_smart(10, sku="x1")
    assert smart["excluido"] is True
    assert smart["stock"] == 0


@pytest.mark.asyncio
async def test_apagado_com_ativo_do_mesmo_codigo_troca_o_vinculo():
    c = _BlingHttp({20: _detalhe(20, "A", 9)}, busca=[{"id": 20, "codigo": "X1 ", "situacao": "A"}])
    smart = await c.get_product_stock_smart(10, sku="x1")
    assert smart["found_via"] == "sku_search"
    assert smart["bling_product_id"] == 20 and smart["stock"] == 9
    assert not smart.get("excluido")


@pytest.mark.asyncio
async def test_codigo_parecido_nao_salva_o_excluido():
    """O filtro `codigo` do Bling pode trazer x1.ci para x1: não serve."""
    c = _BlingHttp(
        {20: _detalhe(20, "A", 9, "x1.ci")}, busca=[{"id": 20, "codigo": "x1.ci", "situacao": "A"}]
    )
    smart = await c.get_product_stock_smart(10, sku="x1")
    assert smart["excluido"] is True


@pytest.mark.asyncio
async def test_erro_na_busca_nao_zera():
    c = _BlingHttp({}, busca=httpx.ConnectTimeout("timeout"))
    with pytest.raises(httpx.ConnectTimeout):
        await c.get_product_stock_smart(10, sku="x1")


@pytest.mark.asyncio
async def test_na_lixeira_vira_excluido_guardando_o_saldo():
    smart = await _BlingHttp({10: _detalhe(10, "E", 7)}).get_product_stock_smart(10, sku="x1")
    assert smart["excluido"] is True
    assert smart["stock"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("situacao", ["I", "A"])
async def test_inativo_e_ativo_nao_sao_excluidos(situacao):
    smart = await _BlingHttp({10: _detalhe(10, situacao, 4)}).get_product_stock_smart(10, sku="x1")
    assert not smart.get("excluido")
    assert smart["stock"] == 4


def test_saldo_publicavel_de_excluido_e_zero():
    p = Product(id=uuid4(), sku="x1", stock=40, situacao="E")
    import asyncio

    assert asyncio.run(estoque_familia.saldo_publicavel(None, p)) == 0


# ── Envio: o anúncio vai a zero ──────────────────────────────────────────────


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, h):
        self.h = h
        self.commit = AsyncMock()

    async def get(self, model, row_id):
        return (self.h.products if model is Product else self.h.integrations).get(row_id)

    async def execute(self, statement):
        params = statement.compile().params
        product_id = params.get("product_id_1")
        if not isinstance(product_id, list) and product_id in self.h.products:
            return _Rows([lk for lk in self.h.links if lk.product_id == product_id])
        if "lower_1" in params:  # outro produto ativo com o mesmo SKU?
            return _Rows(self.h.outro_ativo)
        return _Rows([])

    def add(self, row):
        self.h.logs.append(row)


class _Bling(BlingClient):
    def __init__(self, h):
        self.h = h

    async def get_product_stock_smart(self, external_id, *, sku):
        r = self.h.smart
        if isinstance(r, Exception):
            raise r
        return dict(r)


class _Loja:
    def __init__(self):
        self.calls = []

    async def update_stock(self, link, qty, *, force, **kwargs):
        self.calls.append((link.platform, qty, force))
        if (
            qty == 0
            and (link.stock or 0) > 0
            and not force
            and link.platform
            in (
                IntegrationPlatform.ML,
                IntegrationPlatform.MAGALU,
            )
        ):
            return SyncResult(status=SyncStatus.SKIPPED, error_code="b1_guard_zero_block")
        return SyncResult(status=SyncStatus.OK, qty_after=qty)


class _H:
    def __init__(self, monkeypatch):
        self.products, self.links, self.integrations, self.logs = {}, [], {}, []
        self.outro_ativo: list = []
        self.smart: dict | Exception = {}
        self.bling, self.loja = _Bling(self), _Loja()
        self.session = _Session(self)
        self.user_id = uuid4()

        async def client(_orch, integration):
            return self.bling if integration.platform == IntegrationPlatform.BLING else self.loja

        @asynccontextmanager
        async def session_scope():
            yield _Session(self)

        monkeypatch.setattr(module.SyncOrchestrator, "_client", client)
        monkeypatch.setattr(module.SyncOrchestrator, "_emit_link_alerts", AsyncMock())
        monkeypatch.setattr(module, "session_scope", session_scope)

    def link(self, product, platform, stock):
        integ = SimpleNamespace(
            id=uuid4(), platform=platform, archived_at=None, vacation_mode=False
        )
        self.integrations[integ.id] = integ
        link = ProductLink(
            id=uuid4(),
            product_id=product.id,
            user_id=self.user_id,
            integration_id=integ.id,
            platform=platform,
            external_id=str(len(self.links) + 1),
            stock=stock,
        )
        self.links.append(link)
        return link

    def produto(self, *, situacao="A", stock=0, bling=True, lojas=(IntegrationPlatform.ML,)):
        p = Product(
            id=uuid4(),
            user_id=self.user_id,
            sku=f"x{len(self.products)}",
            stock=stock,
            situacao=situacao,
        )
        self.products[p.id] = p
        if bling:
            self.link(p, IntegrationPlatform.BLING, stock)
        return p, [self.link(p, plat, 30) for plat in lojas]

    async def run(self, mode, *, force=False):
        orch = module.SyncOrchestrator(self.session, user_id=self.user_id, force=force)
        if mode == "parallel":
            await orch.run_parallel(list(self.products))
        else:
            await orch.run(list(self.products.values()))


TODAS = (
    IntegrationPlatform.ML,
    IntegrationPlatform.MAGALU,
    IntegrationPlatform.SHOPEE,
    IntegrationPlatform.AMAZON,
    IntegrationPlatform.TIKTOK,
)


@pytest.fixture
def h(monkeypatch):
    return _H(monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_apagado_no_bling_zera_todos_os_anuncios(h, mode):
    p, lojas = h.produto(stock=25, lojas=TODAS)
    h.smart = {"stock": 0, "raw": {}, "found_via": "excluido", "excluido": True}
    await h.run(mode)  # varredura: sem force
    assert sorted(h.loja.calls, key=str) == sorted(
        [(pl, 0, pl in (IntegrationPlatform.ML, IntegrationPlatform.MAGALU)) for pl in TODAS],
        key=str,
    )
    assert p.situacao == "E" and p.stock == 0
    assert all(lk.stock == 0 and lk.last_sync_status.value == "ok" for lk in lojas)
    bling = next(lk for lk in h.links if lk.platform == IntegrationPlatform.BLING)
    assert bling.last_error.startswith("sku_excluido: apagado no Bling")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_na_lixeira_com_saldo_publica_zero(h, mode):
    p, (ml,) = h.produto(stock=3)
    h.smart = {"stock": 7, "raw": {"situacao": "E"}, "found_via": "excluido", "excluido": True}
    await h.run(mode)
    assert h.loja.calls == [(IntegrationPlatform.ML, 0, True)]
    assert p.stock == 7 and p.situacao == "E" and ml.stock == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_excluido_sem_vinculo_bling_zera_pelo_davinci(h, mode):
    p, (ml,) = h.produto(situacao="E", stock=12, bling=False)
    await h.run(mode)
    assert h.loja.calls == [(IntegrationPlatform.ML, 0, True)]
    assert ml.stock == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_falha_no_bling_nao_zera(h, mode):
    p, (ml,) = h.produto(situacao="E", stock=12)
    h.smart = RuntimeError("Bling fora")
    await h.run(mode, force=True)
    assert h.loja.calls == []
    assert ml.stock == 30 and p.situacao == "E"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_excluido_que_voltou_ativo_no_bling_publica_de_novo(h, mode):
    p, _ = h.produto(situacao="E", stock=0)
    h.smart = {"stock": 9, "raw": {"id": 1, "situacao": "A"}, "found_via": "direct"}
    await h.run(mode)
    assert p.situacao == "A"
    assert h.loja.calls == [(IntegrationPlatform.ML, 9, False)]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_recriado_com_o_mesmo_sku_publica_o_novo(h, mode):
    p, _ = h.produto(situacao="E", stock=0)
    h.smart = {"stock": 6, "raw": {"id": 99}, "found_via": "sku_search", "bling_product_id": 99}
    await h.run(mode)
    assert p.situacao == "A" and p.bling_product_id == 99
    assert h.loja.calls == [(IntegrationPlatform.ML, 6, False)]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_recriado_mas_o_sku_ja_e_de_outro_produto_segue_zerado(h, mode):
    p, _ = h.produto(situacao="E", stock=0)
    h.outro_ativo = [uuid4()]
    h.smart = {"stock": 6, "raw": {"id": 99}, "found_via": "sku_search", "bling_product_id": 99}
    await h.run(mode, force=True)
    assert p.situacao == "E"
    assert h.loja.calls == [(IntegrationPlatform.ML, 0, True)]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_ativo_comum_nao_muda(h, mode):
    p, _ = h.produto(stock=0)
    h.smart = {"stock": 14, "raw": {"id": 1, "situacao": "A"}, "found_via": "direct"}
    await h.run(mode)
    assert p.situacao == "A"
    assert h.loja.calls == [(IntegrationPlatform.ML, 14, False)]


# ── Varredura da madrugada e refresh do Bling (banco) ───────────────────────


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:skx-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"skx-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _prod(user: User, sku: str, situacao: str, stock: int, bpid: int | None = None) -> Product:
    return Product(
        user_id=user.id,
        sku=sku,
        name=sku,
        stock=stock,
        min_stock=0,
        situacao=situacao,
        formato="S",
        bling_product_id=bpid,
    )


@pytest.mark.asyncio
async def test_varredura_pega_excluido_com_estoque_alto(db: AsyncSession, user: User, monkeypatch):
    from app import worker

    sfx = uuid.uuid4().hex[:6]
    excluido = _prod(user, f"zz-e-{sfx}", "E", 40)
    alto = _prod(user, f"zz-a-{sfx}", "A", 40)
    baixo = _prod(user, f"zz-b-{sfx}", "A", 3)
    db.add_all([excluido, alto, baixo])
    job = BackgroundJob(
        type=BackgroundJobType.SYNC_ALL,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={"trigger": "teste"},
    )
    db.add(job)
    await db.commit()

    @asynccontextmanager
    async def scope():
        yield db

    vistos: list = []

    async def capturar(self, pids, *, only_link_ids=None, **kw):
        vistos.extend(pids)
        return module.OrchestratorReport()

    monkeypatch.setattr(worker, "session_scope", scope)
    monkeypatch.setattr(module.SyncOrchestrator, "run_with_retry", capturar)
    await worker.sync_all_run({}, str(job.id), str(user.id), None)
    assert excluido.id in vistos and baixo.id in vistos
    assert alto.id not in vistos


class _ClienteReconcilia:
    def __init__(self, busca):
        self.busca = busca

    async def get_product(self, bpid):
        req = httpx.Request("GET", "https://bling.test")
        raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))

    async def find_active_product_by_sku(self, sku, *, estrito=False, exato=False):
        assert estrito and exato
        if isinstance(self.busca, Exception):
            raise self.busca
        return self.busca


async def _reconciliar(db, user, cliente, monkeypatch):
    p = _prod(user, f"zz-r-{uuid.uuid4().hex[:6]}", "A", 15, bpid=int(uuid.uuid4().int % 10**12))
    db.add(p)
    job = BackgroundJob(type=BackgroundJobType.SYNC_ALL, created_by=user.id)
    db.add(job)
    await db.commit()
    zerar = AsyncMock(return_value=1)
    monkeypatch.setattr(refresh, "_enfileirar_zeragem", zerar)
    monkeypatch.setattr(refresh, "_RECONCILE_SLEEP", 0)
    summary = {"reconciled_excluido": 0, "reconciled_healed": 0}
    await refresh._reconcile_excluidos(
        db, [cliente], {int(p.bling_product_id): p}, {1}, job, summary
    )
    await db.refresh(p)
    return p, zerar, summary


@pytest.mark.asyncio
async def test_refresh_que_rebaixa_para_excluido_zera_na_hora(
    db: AsyncSession, user: User, monkeypatch
):
    p, zerar, summary = await _reconciliar(db, user, _ClienteReconcilia(None), monkeypatch)
    assert p.situacao == "E"
    assert [x.id for x in zerar.await_args.args[1]] == [p.id]
    assert summary["zeragem_enfileirada"] == 1


@pytest.mark.asyncio
async def test_refresh_com_erro_na_busca_nao_rebaixa(db: AsyncSession, user: User, monkeypatch):
    p, zerar, _ = await _reconciliar(
        db, user, _ClienteReconcilia(httpx.ConnectTimeout("429")), monkeypatch
    )
    assert p.situacao == "A"
    assert zerar.await_args.args[1] == []


@pytest.mark.asyncio
async def test_zeragem_enfileira_o_envio_do_produto(db: AsyncSession, user: User, monkeypatch):
    p = _prod(user, f"zz-q-{uuid.uuid4().hex[:6]}", "E", 0, bpid=5)
    db.add(p)
    await db.flush()
    from app.models import Company, Integration, LinkSyncStatus, Marketplace, Store, StoreStatus
    from app.security.cipher import encrypt_json

    company = Company(
        razao_social="X",
        cnpj=uuid.uuid4().hex[:14],
        apelido=f"x-{uuid.uuid4().hex[:6]}",
        responsavel_id=user.id,
    )
    db.add(company)
    await db.flush()
    store = Store(company_id=company.id, marketplace=Marketplace.ML, status=StoreStatus.ACTIVE)
    db.add(store)
    await db.flush()
    integ = Integration(
        user_id=user.id,
        store_id=store.id,
        platform=IntegrationPlatform.ML,
        name="ml",
        credentials=encrypt_json({}),
    )
    db.add(integ)
    await db.flush()
    vivo = ProductLink(
        user_id=user.id,
        product_id=p.id,
        integration_id=integ.id,
        store_id=store.id,
        platform=IntegrationPlatform.ML,
        external_id="MLB1",
        stock=30,
        last_sync_status=LinkSyncStatus.OK,
    )
    fatal = ProductLink(
        user_id=user.id,
        product_id=p.id,
        integration_id=integ.id,
        store_id=store.id,
        platform=IntegrationPlatform.ML,
        external_id="MLB2",
        stock=30,
        last_sync_status=LinkSyncStatus.FATAL,
    )
    db.add_all([vivo, fatal])
    await db.commit()

    pool = SimpleNamespace(enqueue_job=AsyncMock(return_value=SimpleNamespace(job_id="arq-1")))
    import app.worker_pool as wp

    monkeypatch.setattr(wp, "get_arq_sync_pool", AsyncMock(return_value=pool))
    assert await refresh._enfileirar_zeragem(db, [p], user.id) == 1
    nome, job_id, uid, pid, link_ids = pool.enqueue_job.await_args.args
    assert (nome, uid, pid, link_ids) == (
        "sync_product_run",
        str(p.user_id),
        str(p.id),
        [str(vivo.id)],
    )
    job = (
        await db.execute(select(BackgroundJob).where(BackgroundJob.id == uuid.UUID(job_id)))
    ).scalar_one()
    assert job.type == BackgroundJobType.SYNC_PRODUCT and job.payload["trigger"] == "sku_excluido"
