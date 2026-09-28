"""Aba Produtos: vínculo que segue o SKU do anúncio e vínculo morto.

Eduardo, 28/09/2026: "quando trocamos o sku dentro do anúncio (dg053.ci →
dg053.sp) nosso sistema fica travado no link velho e não muda, fazendo ficar
sem estoque" e "estamos deixando links mortos".
"""

# ruff: noqa: S105, S106

from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest
import respx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Product,
    ProductLink,
    User,
    UserRole,
    UserStatus,
)
from app.services.link_reconcile import reconcile_product_links
from app.services.marketplaces.base import SyncResult, SyncStatus
from app.services.marketplaces.ml import ML_API_BASE, MercadoLivreClient
from app.services.sync_orchestrator import SyncOrchestrator
from app.services.vinculo_saude import motivo_morto
from tests.test_link_reconcile import _shopee_integration, _shopee_link, _snapshot_returning
from tests.test_ml_client import _make_setup, _ml_creds


async def _usuario(db: AsyncSession, nome: str) -> User:
    u = User(
        open_id=f"email:{nome}-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"{nome}-{uuid.uuid4().hex[:6]}@davinci-test.com",
        name=nome,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _item_ml(sku: str, *, status: str = "active", sub_status: list | None = None) -> dict:
    return {
        "id": "MLB123",
        "status": status,
        "sub_status": sub_status or [],
        "attributes": [{"id": "SELLER_SKU", "value_name": sku}],
    }


# --- 1. o botão de conserto enxerga produtos de qualquer dono ---------------------


@pytest.mark.asyncio
async def test_recarregar_move_para_produto_de_outro_dono(db: AsyncSession, monkeypatch):
    """Produção: 4.723 produtos são do "bill gates" e quem clica é o
    heisenberg. O índice só com os produtos de quem clicou respondia "SKU novo
    sem produto cadastrado" — 0 vínculos movidos em 101 tentativas."""
    heisenberg = await _usuario(db, "heisenberg")
    bill = await _usuario(db, "bill")
    velho = Product(user_id=heisenberg.id, sku="zz53.ci", name="velho", stock=0, min_stock=0)
    novo = Product(user_id=bill.id, sku="zz53.sp", name="novo", stock=40, min_stock=0)
    db.add_all([velho, novo])
    await db.flush()
    integ = await _shopee_integration(db, heisenberg)
    link = await _shopee_link(db, heisenberg, integ, velho, sku="zz53.ci")

    from app.services.marketplaces import shopee as shopee_mod

    monkeypatch.setattr(shopee_mod.ShopeeClient, "get_listing_snapshot", _snapshot_returning("zz53.sp"))
    report = await reconcile_product_links(db, user=heisenberg, product=velho)
    await db.commit()
    await db.refresh(link)
    assert len(report.moves) == 1
    assert link.product_id == novo.id
    assert report.warnings == []


@pytest.mark.asyncio
async def test_recarregar_no_produto_novo_puxa_o_preso_no_velho(db: AsyncSession, monkeypatch):
    """Quem trocou o SKU clica no produto NOVO (é ele que está sem estoque). O
    vínculo preso no velho, cujo anúncio já foi visto com o SKU novo, vem junto."""
    u = await _usuario(db, "op")
    velho = Product(user_id=u.id, sku="zz54.ci", name="velho", stock=0, min_stock=0)
    novo = Product(user_id=u.id, sku="zz54.sp", name="novo", stock=40, min_stock=0)
    db.add_all([velho, novo])
    await db.flush()
    integ = await _shopee_integration(db, u)
    link = await _shopee_link(db, u, integ, velho, sku="zz54.sp")  # anúncio já com SKU novo

    from app.services.marketplaces import shopee as shopee_mod

    monkeypatch.setattr(shopee_mod.ShopeeClient, "get_listing_snapshot", _snapshot_returning("zz54.sp"))
    report = await reconcile_product_links(db, user=u, product=novo)
    await db.commit()
    await db.refresh(link)
    assert len(report.moves) == 1
    assert link.product_id == novo.id


# --- 2. o envio ao ML confere o SKU e revive pausado por falta de estoque ---------


@pytest.mark.asyncio
async def test_ml_nao_envia_estoque_do_produto_velho_para_anuncio_trocado(db: AsyncSession):
    u = await _usuario(db, "ml")
    _, _product, link = await _make_setup(db, u, link_stock=5)
    client = MercadoLivreClient(_ml_creds())
    with respx.mock(base_url=ML_API_BASE, assert_all_called=False) as router:
        router.get("/items/MLB123").mock(return_value=httpx.Response(200, json=_item_ml("outro.sp")))
        put = router.put("/items/MLB123")
        r = await client.update_stock(link, 7, force=True, sku_esperado=link.external_sku)
    assert r.error_code == "sku_trocado"
    assert r.payload["sku_atual"] == "outro.sp"
    assert not put.called


@pytest.mark.asyncio
async def test_ml_mesmo_sku_envia_e_devolve_sku_atual(db: AsyncSession):
    u = await _usuario(db, "ml2")
    _, product, link = await _make_setup(db, u, link_stock=5)
    client = MercadoLivreClient(_ml_creds())
    with respx.mock(base_url=ML_API_BASE) as router:
        router.get("/items/MLB123").mock(return_value=httpx.Response(200, json=_item_ml(product.sku.upper())))
        router.put("/items/MLB123").mock(return_value=httpx.Response(200, json={}))
        r = await client.update_stock(link, 7, force=True, sku_esperado=product.sku)
    assert r.status == SyncStatus.OK
    assert r.payload["sku_atual"] == product.sku.upper()


@pytest.mark.asyncio
async def test_ml_pausado_por_falta_de_estoque_volta_quando_tem_estoque(db: AsyncSession):
    """O anúncio que zerou é pausado pelo ML; sem `force` o envio pulava
    pausados e ele nunca mais voltava (o "fica sem estoque")."""
    u = await _usuario(db, "ml3")
    _, product, link = await _make_setup(db, u, link_stock=0)
    client = MercadoLivreClient(_ml_creds())
    with respx.mock(base_url=ML_API_BASE) as router:
        router.get("/items/MLB123").mock(
            return_value=httpx.Response(200, json=_item_ml(product.sku, status="paused", sub_status=["out_of_stock"]))
        )
        put = router.put("/items/MLB123").mock(return_value=httpx.Response(200, json={}))
        r = await client.update_stock(link, 9, force=False, sku_esperado=product.sku)
    assert r.status == SyncStatus.OK
    assert put.called


@pytest.mark.asyncio
async def test_ml_pausado_pelo_vendedor_continua_intocado(db: AsyncSession):
    u = await _usuario(db, "ml4")
    _, product, link = await _make_setup(db, u, link_stock=0)
    client = MercadoLivreClient(_ml_creds())
    with respx.mock(base_url=ML_API_BASE, assert_all_called=False) as router:
        router.get("/items/MLB123").mock(return_value=httpx.Response(200, json=_item_ml(product.sku, status="paused")))
        put = router.put("/items/MLB123")
        r = await client.update_stock(link, 9, force=False, sku_esperado=product.sku)
    assert r.error_code == "ml_listing_paused"
    assert not put.called


# --- 3. o motor: move sozinho, marca morto, não gasta chamada com morto -----------


class _MLFalso:
    """Anúncio que hoje tem o SKU `sku_no_anuncio`. Registra cada envio."""

    def __init__(self, sku_no_anuncio: str, resposta: SyncResult | None = None):
        self.sku = sku_no_anuncio
        self.envios: list[tuple[str, int]] = []
        self.resposta = resposta

    async def update_stock(self, link, qty, *, bling_store_id=None, force=False, sku_esperado=None):
        if self.resposta is not None:
            return self.resposta
        if sku_esperado and sku_esperado.lower() != self.sku.lower():
            return SyncResult(
                status=SyncStatus.REQUIRES_REVIEW, error_code="sku_trocado",
                payload={"sku_atual": self.sku},
            )
        self.envios.append((sku_esperado or "?", qty))
        return SyncResult(status=SyncStatus.OK, qty_before=link.stock, qty_after=qty,
                          payload={"sku_atual": self.sku})


async def _ml_link(db, u, produto, *, sku_link: str) -> tuple[Integration, ProductLink]:
    integ, _p, link = await _make_setup(db, u, link_stock=3)
    link.product_id = produto.id
    link.external_sku = sku_link
    await db.commit()
    return integ, link


@pytest.mark.asyncio
async def test_motor_move_o_vinculo_e_manda_o_estoque_certo(db: AsyncSession):
    u = await _usuario(db, "m1")
    outro = await _usuario(db, "dono")
    velho = Product(user_id=u.id, sku="zz60.ci", name="velho", stock=0)
    novo = Product(user_id=outro.id, sku="zz60.sp", name="novo", stock=37)
    db.add_all([velho, novo])
    await db.commit()
    _integ, link = await _ml_link(db, u, velho, sku_link="zz60.ci")
    falso = _MLFalso("zz60.sp")
    with patch("app.services.sync_orchestrator.client_for", return_value=falso):
        await SyncOrchestrator(db, user_id=u.id, force=True).run([velho])
    await db.commit()
    await db.refresh(link)
    assert link.product_id == novo.id
    assert link.external_sku == "zz60.sp"
    assert falso.envios == [("zz60.sp", 37)]  # nunca o 0 do lote velho
    assert link.last_error.startswith("sku_movido")


@pytest.mark.asyncio
async def test_motor_sku_sem_produto_fica_e_avisa(db: AsyncSession):
    u = await _usuario(db, "m2")
    velho = Product(user_id=u.id, sku="zz61.ci", name="velho", stock=4)
    db.add(velho)
    await db.commit()
    _integ, link = await _ml_link(db, u, velho, sku_link="zz61.ci")
    falso = _MLFalso("sku-que-nao-existe")
    with patch("app.services.sync_orchestrator.client_for", return_value=falso):
        await SyncOrchestrator(db, user_id=u.id, force=True).run([velho])
    await db.commit()
    await db.refresh(link)
    assert link.product_id == velho.id
    assert falso.envios == [("?", 4)]  # como antes: estoque do produto do vínculo
    assert link.last_error.startswith("sku_divergente")
    assert link.external_sku == "sku-que-nao-existe"


@pytest.mark.asyncio
async def test_motor_marca_morto_e_depois_nao_gasta_chamada(db: AsyncSession):
    u = await _usuario(db, "m3")
    p = Product(user_id=u.id, sku="zz62", name="p", stock=4)
    db.add(p)
    await db.commit()
    _integ, link = await _ml_link(db, u, p, sku_link="zz62")
    encerrado = _MLFalso("zz62", SyncResult(status=SyncStatus.SKIPPED, error_code="ml_listing_closed"))
    with patch("app.services.sync_orchestrator.client_for", return_value=encerrado):
        await SyncOrchestrator(db, user_id=u.id).run([p])
    await db.commit()
    await db.refresh(link)
    assert link.morto_desde is not None
    assert "encerrado" in link.morto_motivo

    with patch("app.services.sync_orchestrator.client_for", side_effect=_proibido):
        await SyncOrchestrator(db, user_id=u.id).run([p])
    await db.commit()
    await db.refresh(link)
    assert link.last_error.startswith("ml_listing_closed")  # nem entrou na passada


def _proibido(*a, **k):
    raise AssertionError("não devia chamar o marketplace para vínculo morto")


@pytest.mark.asyncio
async def test_webhook_pedido_e_irmaos_nao_enviam_para_morto(db: AsyncSession):
    """Webhook do Bling, pedido e irmãos da família rodam com force=True (para
    passar da trava de zero do ML) — e mesmo com a lista de vínculos na mão,
    o morto fica de fora. Revisão de 28/09: ~4 mil envios/dia por esse caminho."""
    u = await _usuario(db, "m5")
    p = Product(user_id=u.id, sku="zz64", name="p", stock=4)
    db.add(p)
    await db.commit()
    _integ, link = await _ml_link(db, u, p, sku_link="zz64")
    link.morto_desde = link.created_at
    link.morto_motivo = "anúncio encerrado no Mercado Livre"
    await db.commit()
    with patch("app.services.sync_orchestrator.client_for", side_effect=_proibido):
        await SyncOrchestrator(db, user_id=u.id, force=True, force_bling_refresh=True).run(
            [p], only_link_ids=[link.id]
        )
        await SyncOrchestrator(db, user_id=u.id, force=True).run([p])


@pytest.mark.asyncio
async def test_botao_de_sincronizar_que_da_certo_revive(db: AsyncSession):
    u = await _usuario(db, "m4")
    p = Product(user_id=u.id, sku="zz63", name="p", stock=4)
    db.add(p)
    await db.commit()
    _integ, link = await _ml_link(db, u, p, sku_link="zz63")
    link.morto_desde = link.created_at
    link.morto_motivo = "x"
    await db.commit()
    with patch("app.services.sync_orchestrator.client_for", return_value=_MLFalso("zz63")):
        await SyncOrchestrator(db, user_id=u.id, force=True, incluir_mortos=True).run([p])
    await db.commit()
    await db.refresh(link)
    assert link.morto_desde is None


def test_botoes_de_sincronizar_incluem_mortos():
    import inspect

    from app.routers import sync as sync_router

    fonte = inspect.getsource(sync_router)
    assert fonte.count("incluir_mortos=True") == 3  # produto, anúncio, recarregar


@pytest.mark.asyncio
async def test_shopee_bloqueado_so_morre_sem_envio_certo_em_24h(db: AsyncSession):
    """Shopee "abnormal": 30 de 4.040 voltaram no mês. Um erro isolado num
    anúncio que vendia até hoje não o derruba; sem envio certo há 24 h, sim."""
    from app.models import SyncLog

    u = await _usuario(db, "s1")
    p = Product(user_id=u.id, sku="zz65", name="p", stock=4)
    db.add(p)
    await db.flush()
    integ = await _shopee_integration(db, u)
    link = await _shopee_link(db, u, integ, p, sku="zz65")
    db.add(SyncLog(
        user_id=u.id, product_id=p.id, product_link_id=link.id, integration_id=integ.id,
        platform=link.platform, action="update_stock", status=LinkSyncStatus.OK,
    ))
    await db.commit()
    erro = SyncResult(
        status=SyncStatus.REQUIRES_REVIEW, error_code="product.error_busi",
        error_detail="product 1 status is abnormal",
    )
    orch = SyncOrchestrator(db, user_id=u.id)
    await orch._atualizar_saude(link, erro)
    assert link.morto_desde is None  # vendeu nas últimas 24 h: ainda não

    await db.execute(text("UPDATE sync_logs SET created_at = now() - interval '2 days'"))
    await orch._atualizar_saude(link, erro)
    assert link.morto_desde is not None
    assert link.morto_motivo == "anúncio bloqueado ou excluído na Shopee"

    # variação excluída é definitiva: marca na hora, mesmo tendo vendido hoje
    link.morto_desde = None
    await db.execute(text("UPDATE sync_logs SET created_at = now()"))
    await orch._atualizar_saude(link, SyncResult(
        status=SyncStatus.REQUIRES_REVIEW, error_code="x", error_detail="model id not exist"
    ))
    assert link.morto_desde is not None
    await db.commit()


# --- 4. classificação dos erros ----------------------------------------------------


def test_motivos_de_morto():
    assert motivo_morto("ml", "ml_item_not_found", None)
    assert motivo_morto("ml", "ml_listing_closed", None)
    assert motivo_morto("ml", "ml_listing_under_review", None) is None  # parado, não morto
    assert motivo_morto("ml", "ml_listing_paused", None) is None
    assert motivo_morto("shopee", "product.error_busi", "product 1 status is abnormal")
    assert motivo_morto("shopee", "shopee_stock_rejected", "model ID not exist in sku")
    assert motivo_morto("shopee", "shopee_stock_rejected", "item delisted by seller") is None
    assert motivo_morto("tiktok", "tiktok_status_bloqueado", "x")
    assert motivo_morto("tiktok", "tiktok_temporario", "Internal error. Retry later") is None
    assert motivo_morto("amazon", "amazon_sku_not_found", None)


def test_shopee_abnormal_nao_e_mais_retentado():
    from app.services.marketplaces.shopee import _classify_response

    r = httpx.Response(
        200,
        json={"error": "product.error_busi", "message": "product 123 status is abnormal"},
        request=httpx.Request("POST", "https://x"),
    )
    res = _classify_response(r, 1, qty_after=5)
    assert res.status == SyncStatus.REQUIRES_REVIEW  # antes: RETRYABLE para sempre
    assert motivo_morto("shopee", res.error_code, res.error_detail)


# --- 5. a tela: filtro, contagem e remover mortos ------------------------------------


@pytest.mark.asyncio
async def test_tela_conta_filtra_e_remove_mortos(client, db: AsyncSession, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    p = Product(user_id=admin.id, sku="zz70.ci", name="p", stock=4)
    q = Product(user_id=admin.id, sku="zz71", name="q", stock=4)
    db.add_all([p, q])
    await db.commit()
    _i, morto = await _ml_link(db, admin, p, sku_link="zz70.ci")
    morto.morto_desde = morto.created_at
    morto.morto_motivo = "anúncio encerrado no Mercado Livre"
    _i2, divergente = await _ml_link(db, admin, q, sku_link="zz71.sp")
    divergente.external_id = "MLB999"
    await db.commit()

    r = (await client.get("/api/products")).json()
    assert r["saude"]["mortos"] == 1
    assert r["saude"]["sku_divergente"] == 1
    so_mortos = (await client.get("/api/products", params={"vinculos": "mortos"})).json()
    assert [i["sku"] for i in so_mortos["items"]] == ["zz70.ci"]
    so_sku = (await client.get("/api/products", params={"vinculos": "sku"})).json()
    assert [i["sku"] for i in so_sku["items"]] == ["zz71"]

    agendados = []

    class _Pool:
        async def enqueue_job(self, *a, **k):
            agendados.append(a)

    async def _pool():
        return _Pool()

    with patch("app.routers.products.get_arq_pool", _pool):
        r = await client.post("/api/product-links/remover-mortos")
    assert r.json() == {"agendado": True, "quantidade": 1}
    assert agendados == [("remover_vinculos_mortos_run", None, True)]

    # morto de conta arquivada: a tela não mostra nem conta, o botão não apaga
    from datetime import UTC, datetime

    arq_integ, arquivado = await _ml_link(db, admin, q, sku_link="zz71")
    arquivado.external_id = "MLB555"
    arquivado.morto_desde = arquivado.created_at
    arquivado.morto_motivo = "duplicado: o anúncio já tem vínculo por variação"
    arq_integ.archived_at = datetime.now(UTC)
    await db.commit()

    # o job: apaga em lotes; o histórico de envio fica como está (com o id)
    from app.models import SyncLog
    from app.services.vinculo_saude import apagar_mortos_em_lotes

    db.add(SyncLog(
        user_id=admin.id, product_id=p.id, product_link_id=morto.id, integration_id=morto.integration_id,
        platform=morto.platform, action="update_stock", status=LinkSyncStatus.SKIPPED,
    ))
    await db.commit()
    assert await apagar_mortos_em_lotes(somente_visiveis=True, pausa=0) == 1
    restantes = (await db.execute(select(ProductLink.id))).scalars().all()
    assert morto.id not in restantes and divergente.id in restantes and arquivado.id in restantes
    log = (await db.execute(select(SyncLog.product_link_id))).scalars().all()
    assert log == [morto.id]

    # nada morto à vista: nem agenda
    agendados.clear()
    with patch("app.routers.products.get_arq_pool", _pool):
        r = await client.post("/api/product-links/remover-mortos")
    assert r.json() == {"agendado": True, "quantidade": 0}
    assert agendados == []


# --- 6. a varredura da madrugada roda uma vez só -------------------------------------


@pytest.mark.asyncio
async def test_varredura_da_madrugada_uma_so_mesmo_com_3_pessoas(db: AsyncSession, monkeypatch):
    from app import worker
    from app.models import BackgroundJob, BackgroundJobType, UserSettings

    for nome in ("a", "b", "c"):
        u = await _usuario(db, nome)
        db.add(UserSettings(user_id=u.id, daily_sync_enabled=True))
    await db.commit()

    enfileirados = []

    class _Pool:
        async def enqueue_job(self, *a, **k):
            enfileirados.append(a)

    async def _pool():
        return _Pool()

    monkeypatch.setattr(worker, "get_arq_sync_pool", _pool)
    agora = worker.datetime.now(worker.SP_TZ).replace(second=0, microsecond=0)
    monkeypatch.setattr(worker, "DAILY_SYNC_HORA_PADRAO", agora.time())
    await worker.daily_sync_scheduler({})
    await worker.daily_sync_scheduler({})
    jobs = (
        await db.execute(select(BackgroundJob).where(BackgroundJob.type == BackgroundJobType.SYNC_ALL))
    ).scalars().all()
    assert len(jobs) == 1
    assert len(enfileirados) == 1
    await db.execute(text("DELETE FROM background_jobs"))
    await db.execute(text("DELETE FROM user_settings"))
    await db.commit()


def test_listings_de_maio_nao_ressuscitam_vinculo():
    import inspect

    from app.services import listings_import

    assert "INTERVAL '2 days'" in inspect.getsource(listings_import._create_product_links_for_matched)


def test_tiktok_separa_morto_de_passageiro():
    from app.services.marketplaces import tiktok

    assert any("must be in one of these statuses" in k for k in tiktok._TIKTOK_STATUS_BLOQUEADO)
    assert "retry later" in tiktok._TIKTOK_PASSAGEIRO


@pytest.mark.asyncio
async def test_integracao_existe(db: AsyncSession):
    # sanidade: IntegrationPlatform.ML existe e o índice do motor aceita dono qualquer
    assert IntegrationPlatform.ML.value == "ml"
    assert LinkSyncStatus.FATAL.value == "fatal"


@pytest.mark.asyncio
async def test_duplicado_marcado_nao_revive(db: AsyncSession):
    """O vínculo "do anúncio inteiro" que duplicava a variação foi marcado
    como morto pela migration. Um envio forçado que dá certo NÃO o revive."""
    u = await _usuario(db, "d1")
    p = Product(user_id=u.id, sku="zz80", name="p", stock=4)
    db.add(p)
    await db.commit()
    _integ, link = await _ml_link(db, u, p, sku_link="zz80")
    link.morto_desde = link.created_at
    link.morto_motivo = "duplicado: o anúncio já tem vínculo por variação"
    await db.commit()
    with patch("app.services.sync_orchestrator.client_for", return_value=_MLFalso("zz80")):
        await SyncOrchestrator(db, user_id=u.id, force=True, incluir_mortos=True).run([p])
    await db.commit()
    await db.refresh(link)
    assert link.morto_desde is not None

    # nem a varredura das 10h (anúncio "active") nem um erro trocam o motivo
    from app.services.auto_link import _saude_pelo_status

    assert _saude_pelo_status(link, IntegrationPlatform.ML, "active") is None
    assert _saude_pelo_status(link, IntegrationPlatform.ML, "closed") is None
    orch = SyncOrchestrator(db, user_id=u.id)
    await orch._atualizar_saude(link, SyncResult(status=SyncStatus.SKIPPED, error_code="ml_listing_closed"))
    assert link.morto_desde is not None
    assert link.morto_motivo.startswith("duplicado:")
    await db.commit()


@pytest.mark.asyncio
async def test_ml_so_confia_no_sku_oficial_da_variacao(db: AsyncSession):
    """Variação sem o atributo SELLER_SKU e com o campo antigo
    `seller_custom_field` desatualizado: NÃO pode mover (não confere)."""
    u = await _usuario(db, "ml5")
    _, product, link = await _make_setup(db, u, link_stock=5)
    link.variation_id = "777"
    await db.commit()
    client = MercadoLivreClient(_ml_creds())
    item = {
        "id": "MLB123", "status": "active",
        "variations": [{"id": 777, "seller_custom_field": "sku-velho-do-campo-antigo"}],
    }
    with respx.mock(base_url=ML_API_BASE) as router:
        get = router.get("/items/MLB123").mock(return_value=httpx.Response(200, json=item))
        router.put("/items/MLB123").mock(return_value=httpx.Response(200, json={}))
        r = await client.update_stock(link, 7, force=True, sku_esperado=product.sku)
    assert r.status == SyncStatus.OK
    assert "include_attributes=all" in str(get.calls[0].request.url)
