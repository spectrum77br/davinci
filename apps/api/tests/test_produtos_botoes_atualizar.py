"""Produtos › botão ATUALIZAR (17/11/2026, services/produtos_botoes).

Lê os produtos novos do Bling e as listas de anúncios (tabela `listings`) das
contas montadas no servidor e, no fim, roda o Vincular nas contas lidas,
segurando a rodada do Flex do começo da gravação das listas até o fim do
Vincular (decisão 1 do dono). Só lê dos marketplaces (clientes falsos aqui).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Product,
    ProductLink,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services import auto_link as al
from app.services import produtos_botoes as pb
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.job_details import load_job_details
from app.services.marketplaces import amazon as amazon_mod
from app.services.marketplaces import ml as ml_mod
from app.services.marketplaces import shopee as shopee_mod
from app.services.marketplaces import tiktok as tt_mod

# ───────────────────────────── montagem ──────────────────────────────────────

_CREDS = {
    IntegrationPlatform.ML: {"access_token": "x", "user_id": 1, "expires_at": 9999999999},
    IntegrationPlatform.SHOPEE: {"access_token": "x", "shop_id": 1, "expires_at": 9999999999},
    IntegrationPlatform.TIKTOK: {
        "app_key": "k", "app_secret": "s", "access_token": "t", "shop_cipher": "c",
    },
}


async def _dono(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:dono-{uuid.uuid4().hex[:6]}@x",
        email=f"dono-{uuid.uuid4().hex[:6]}@x",
        name="Eduardo",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _conta(
    db: AsyncSession,
    dono: User,
    plataforma: IntegrationPlatform,
    nome: str,
    *,
    arquivada: bool = False,
    marca: str | None = None,
) -> Integration:
    from datetime import UTC, datetime

    creds = dict(_CREDS.get(plataforma, {"access_token": "x", "expires_at": 9999999999}))
    if marca:
        creds["marca"] = marca  # o cliente falso escolhe o que devolve por aqui
    integ = Integration(
        user_id=dono.id,
        platform=plataforma,
        name=nome,
        credentials=encrypt_json(creds),
        archived_at=datetime.now(UTC) if arquivada else None,
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _job(db: AsyncSession, dono: User, contas: list[Integration], **extra) -> BackgroundJob:
    job = BackgroundJob(
        type=BackgroundJobType.IMPORT_LISTINGS,
        status=BackgroundJobStatus.PENDING,
        created_by=dono.id,
        payload={
            "trigger": "botao_atualizar",
            "integration_ids": [str(c.id) for c in contas],
            **extra,
        },
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


def _item_ml(ext: str, sku: str, *, status: str = "active", var: str | None = None,
             tipo: str | None = "gold_special") -> dict[str, Any]:
    return {
        "external_id": ext, "variation_id": var, "sku": sku, "title": f"anúncio {ext}",
        "price": 1000, "stock": 3, "status": status, "listing_type": tipo,
        "raw": {"id": ext, "variations": [{"id": var}] if var else []},
    }


@pytest.fixture
def lojas(monkeypatch):
    """Clientes falsos (só leitura). `lojas['ml'][marca]` = itens do ML daquela
    conta (o mesmo falso serve a lista E o Vincular, como na vida real);
    item 'ERRO' levanta. Idem shopee/tiktok/amazon."""
    dados: dict[str, dict[str, Any]] = {"ml": {}, "shopee": {}, "tiktok": {}, "amazon": {}}
    chamadas: list[tuple[str, str]] = []

    def _marca(cli) -> str:
        return str(cli.creds.get("marca") or "")

    async def _ml(self, **kw):
        chamadas.append(("ml", _marca(self)))
        for it in dados["ml"].get(_marca(self), []):
            if it == "ERRO":
                raise RuntimeError("ml_search_failed status=401 body=invalid_token")
            if callable(it):
                await it()
                continue
            yield it

    async def _shopee(self, **kw):
        chamadas.append(("shopee", _marca(self)))
        for it in dados["shopee"].get(_marca(self), []):
            yield it

    async def _tiktok(self, page_size: int = 20, page_token: str | None = None):
        chamadas.append(("tiktok", _marca(self)))
        return list(dados["tiktok"].get(_marca(self), [])), None

    async def _amazon(self, **kw):
        chamadas.append(("amazon", _marca(self)))
        for it in dados["amazon"].get(_marca(self), []):
            yield it

    monkeypatch.setattr(ml_mod.MercadoLivreClient, "list_listings", _ml)
    monkeypatch.setattr(shopee_mod.ShopeeClient, "list_listings", _shopee)
    monkeypatch.setattr(tt_mod.TikTokClient, "search_products", _tiktok)
    monkeypatch.setattr(amazon_mod.AmazonClient, "list_listings", _amazon)
    monkeypatch.setattr(al, "AUTOLINK_BACKOFF_SECONDS", [0.0, 0.0])
    monkeypatch.setattr(pb, "FLEX_ESPERA_MAX_S", 0.3)
    monkeypatch.setattr(pb, "FLEX_ESPERA_INTERVALO_S", 0.05)
    return {"dados": dados, "chamadas": chamadas}


async def _detalhes(db: AsyncSession, job_id) -> list[dict]:
    itens, _ = await load_job_details(db, job_id)
    return itens


async def _trava_livre(chave: int) -> bool:
    async with session_scope() as s:
        return bool(
            (
                await s.execute(
                    text("SELECT pg_try_advisory_xact_lock(:ns, :k)"),
                    {"ns": SYNC_NAMESPACE, "k": chave},
                )
            ).scalar()
        )


async def _trava_presa(chave: int) -> bool:
    """Alguém segura a trava (olha o pg_locks, sem tentar pegá-la — duas
    sondas ao mesmo tempo com o try-lock se atrapalhariam)."""
    async with session_scope() as s:
        return bool(
            (
                await s.execute(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                        "AND granted AND database = (SELECT oid FROM pg_database "
                        "WHERE datname = current_database()) "
                        "AND classid::bigint = :ns AND objid::bigint = :k AND objsubid = 2"
                    ),
                    {"ns": SYNC_NAMESPACE, "k": chave},
                )
            ).scalar()
        )


# ───────────────────────────── endpoint ─────────────────────────────────────


def _pool_falso() -> AsyncMock:
    pool = AsyncMock()
    pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "x"}))
    return pool


@pytest.mark.asyncio
async def test_atualizar_sem_produtos_edit_da_403(
    client: AsyncClient, make_user: Callable, auth_as: Callable
):
    u = await make_user(role=UserRole.USER, permissions={"produtos": {"view": True}})
    auth_as(u)
    r = await client.post("/api/produtos/atualizar", json={})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_atualizar_monta_as_contas_no_servidor(
    client: AsyncClient, db: AsyncSession, auth_as: Callable
):
    """Corpo vazio: o servidor escolhe. Entram ML/Shopee/TikTok/Magalu/
    Carrefour não arquivadas; Amazon fica para o Vincular do fim; Temu e
    Amazon aparecem como 'sem leitura'; arquivada e Bling ficam de fora."""
    dono = await _dono(db)
    auth_as(dono)
    lidas = [
        await _conta(db, dono, IntegrationPlatform.ML, "ml-a"),
        await _conta(db, dono, IntegrationPlatform.SHOPEE, "sh-a"),
        await _conta(db, dono, IntegrationPlatform.TIKTOK, "tt-a"),
        await _conta(db, dono, IntegrationPlatform.MAGALU, "mg-a"),
        await _conta(db, dono, IntegrationPlatform.CARREFOUR, "cf-a"),
    ]
    amazon = await _conta(db, dono, IntegrationPlatform.AMAZON, "amz-a")
    await _conta(db, dono, IntegrationPlatform.TEMU, "temu-a")
    await _conta(db, dono, IntegrationPlatform.ML, "ml-arquivada", arquivada=True)
    await _conta(db, dono, IntegrationPlatform.BLING, "bling")

    pool = _pool_falso()
    with patch("app.routers.produtos_atualizar.get_arq_sync_pool", return_value=pool):
        r = await client.post("/api/produtos/atualizar", json={})
    assert r.status_code == 201, r.text
    args = pool.enqueue_job.await_args.args
    assert args[0] == "atualizar_run"
    assert sorted(args[2]) == sorted(str(c.id) for c in lidas)

    job = await db.get(BackgroundJob, uuid.UUID(r.json()["job_id"]))
    assert job.type == BackgroundJobType.IMPORT_LISTINGS
    assert job.status == BackgroundJobStatus.PENDING
    assert job.total == len(lidas) + 1
    assert job.payload["trigger"] == "botao_atualizar"
    assert sorted(job.payload["integration_ids"]) == sorted(str(c.id) for c in lidas)
    assert job.payload["vincular_amazon_ids"] == [str(amazon.id)]
    assert job.payload["sem_leitura"] == ["amazon", "temu"]


@pytest.mark.asyncio
async def test_atualizar_de_usuario_de_equipe_so_le_as_contas_da_equipe(
    client: AsyncClient, db: AsyncSession, make_user: Callable, auth_as: Callable
):
    from app.models.pricing import StoreInfo

    dono = await _dono(db)
    londres = await _conta(db, dono, IntegrationPlatform.ML, "ml-londres")
    israel = await _conta(db, dono, IntegrationPlatform.ML, "ml-israel")
    for integ, equipe in ((londres, 101), (israel, 102)):
        db.add(StoreInfo(user_id=dono.id, platform="ml", account_name=integ.name,
                         integration_id=integ.id, sales_team=equipe))
    await db.commit()
    vendedor = await make_user(
        role=UserRole.USER, permissions={"produtos": {"view": True, "edit": True}}
    )
    vendedor.sales_teams = [101]
    await db.commit()
    auth_as(vendedor)

    pool = _pool_falso()
    with patch("app.routers.produtos_atualizar.get_arq_sync_pool", return_value=pool):
        r = await client.post("/api/produtos/atualizar", json={})
    assert r.status_code == 201
    assert pool.enqueue_job.await_args.args[2] == [str(londres.id)]

    # Equipe sem nenhuma conta com lista → 400 sem_contas.
    await db.execute(BackgroundJob.__table__.delete())
    vendedor.sales_teams = [103]
    await db.commit()
    with patch("app.routers.produtos_atualizar.get_arq_sync_pool", return_value=_pool_falso()):
        r = await client.post("/api/produtos/atualizar", json={})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "sem_contas"


@pytest.mark.asyncio
async def test_atualizar_409_com_massa_de_outro_usuario(
    client: AsyncClient, db: AsyncSession, make_user: Callable, auth_as: Callable
):
    """A trava é global: a varredura das 10h (em nome do dono) bloqueia o
    Atualizar de outra pessoa, e o 409 diz o quê, quem e desde quando."""
    from datetime import UTC, datetime

    dono = await _dono(db)
    await _conta(db, dono, IntegrationPlatform.ML, "ml-a")
    varredura = BackgroundJob(
        type=BackgroundJobType.AUTO_LINK, status=BackgroundJobStatus.RUNNING,
        created_by=dono.id, payload={"trigger": "varredura_diaria"},
        started_at=datetime.now(UTC),
    )
    db.add(varredura)
    await db.commit()
    outro = await make_user(
        role=UserRole.USER, permissions={"produtos": {"view": True, "edit": True}}
    )
    auth_as(outro)

    pool = _pool_falso()
    with patch("app.routers.produtos_atualizar.get_arq_sync_pool", return_value=pool):
        r = await client.post("/api/produtos/atualizar", json={})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "sync_already_running"
    assert d["job_id"] == str(varredura.id)
    assert d["tipo"] == "auto_link" and d["gatilho"] == "varredura_diaria"
    assert d["quem"] == "Eduardo" and d["desde"]
    pool.enqueue_job.assert_not_awaited()


# ───────────────────────────── o job ────────────────────────────────────────


@pytest.mark.asyncio
async def test_uma_conta_com_erro_nao_derruba_as_outras(db: AsyncSession, lojas):
    dono = await _dono(db)
    boa = await _conta(db, dono, IntegrationPlatform.ML, "ml-boa", marca="boa")
    ruim = await _conta(db, dono, IntegrationPlatform.ML, "ml-vortan", marca="ruim")
    lojas["dados"]["ml"] = {"boa": [_item_ml("MLB1", "dg053.sp")], "ruim": ["ERRO"]}
    job = await _job(db, dono, [boa, ruim])

    await pb.run_atualizar(job.id, [boa.id, ruim.id])

    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.finished_at is not None
    contas = {d["nome"]: d for d in await _detalhes(db, job.id) if d.get("etapa") == "conta"}
    assert contas["ml-boa"]["resultado"] == "ok" and contas["ml-boa"]["novos"] == 1
    assert contas["ml-vortan"]["resultado"] == "falha"
    assert "invalid_token" in contas["ml-vortan"]["erro"]
    assert contas["ml-vortan"]["dica"] == "reconectar a conta em Integrações"
    a = job.result["anuncios"]
    assert (a["contas"], a["ok"], a["falhas"], a["puladas"], a["novos"]) == (2, 1, 1, 0, 1)
    assert job.processed == 3 and job.total == 3  # 2 contas + o Bling
    # O Vincular do fim também rodou nas duas (a ruim fica pendente, sem derrubar).
    assert job.result["vincular"]["ok_integrations"] == 1
    assert job.result["vincular"]["pending_integrations"] == 1


@pytest.mark.asyncio
async def test_trava_da_shopee_ocupada_pula_as_contas_shopee(db: AsyncSession, lojas):
    from app.services.listings_import import _SHOPEE_SWEEP_LOCK_KEY

    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    sh = await _conta(db, dono, IntegrationPlatform.SHOPEE, "sh-a", marca="a")
    lojas["dados"]["ml"] = {"a": [_item_ml("MLB1", "x1")]}
    lojas["dados"]["shopee"] = {"a": [_item_ml("999", "x2")]}
    job = await _job(db, dono, [ml, sh])

    async with session_scope() as rodada_shopee:
        await rodada_shopee.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :k)"),
            {"ns": SYNC_NAMESPACE, "k": _SHOPEE_SWEEP_LOCK_KEY},
        )
        await pb.run_atualizar(job.id, [ml.id, sh.id])

    await db.refresh(job)
    contas = {d["nome"]: d for d in await _detalhes(db, job.id) if d.get("etapa") == "conta"}
    assert contas["sh-a"]["resultado"] == "pulada"
    assert contas["sh-a"]["erro"] == pb.MSG_SHOPEE_OCUPADA
    assert contas["ml-a"]["resultado"] == "ok"
    assert ("shopee", "a") not in lojas["chamadas"]
    assert job.result["anuncios"]["puladas"] == 1


@pytest.mark.asyncio
async def test_trava_pbln_ocupada_pula_o_bling(db: AsyncSession, lojas):
    from app.services.bling_produtos_novos import _PRODUTOS_NOVOS_LOCK_KEY

    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    lojas["dados"]["ml"] = {"a": [_item_ml("MLB1", "x1")]}
    job = await _job(db, dono, [ml])

    async with session_scope() as tick:
        await tick.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :k)"),
            {"ns": SYNC_NAMESPACE, "k": _PRODUTOS_NOVOS_LOCK_KEY},
        )
        await pb.run_atualizar(job.id, [ml.id])

    await db.refresh(job)
    assert job.result["bling"]["pulado"] == "rodada_automatica_em_andamento"
    assert job.result["anuncios"]["ok"] == 1
    assert job.status == BackgroundJobStatus.SUCCEEDED
    bling = [d for d in await _detalhes(db, job.id) if d.get("etapa") == "bling"]
    assert len(bling) == 1 and bling[0]["pulado"] == "rodada_automatica_em_andamento"


@pytest.mark.asyncio
async def test_lista_nao_cria_nem_muda_vinculo(db: AsyncSession, lojas, monkeypatch):
    """A leitura das listas só faz upsert em `listings`: sem _link_by_sku e sem
    promoção para product_links. Quem mexe em vínculo é o Vincular do fim
    (aqui desligado para medir só a lista)."""
    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    amazon = await _conta(db, dono, IntegrationPlatform.AMAZON, "amz-a")
    db.add(Product(user_id=dono.id, sku="dg053.sp", name="Mala", stock=5))
    await db.commit()
    lojas["dados"]["ml"] = {"a": [_item_ml("MLB1", "dg053.sp")]}
    job = await _job(db, dono, [ml], vincular_amazon_ids=[str(amazon.id)])
    links_antes = (await db.execute(select(func.count(ProductLink.id)))).scalar()

    vinculou: list[list] = []

    async def _vincular(job_id, ids, summary, **kw):
        vinculou.append(list(ids))
        kw["por_conta"].update({i: {"result": "ok"} for i in ids})

    monkeypatch.setattr(al, "vincular_contas", _vincular)
    await pb.run_atualizar(job.id, [ml.id])

    assert (await db.execute(select(func.count(ProductLink.id)))).scalar() == links_antes
    linha = (await db.execute(select(Listing))).scalar_one()
    assert linha.external_id == "MLB1" and linha.product_id is None
    # O Vincular do ML roda ANTES da lista (dentro da trava do Flex); o das
    # Amazon do escopo, no fim, já sem a trava.
    assert vinculou == [[ml.id], [amazon.id]]


@pytest.mark.asyncio
async def test_vincular_do_fim_liga_a_variacao_nova(db: AsyncSession, lojas):
    """Decisão 1: a variação nova do ML chega à lista E ganha vínculo no mesmo
    clique (antes de o robô do Flex ver a lista)."""
    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    p = Product(user_id=dono.id, sku="dg053.sp", name="Mala P", stock=5)
    db.add(p)
    await db.commit()
    lojas["dados"]["ml"] = {"a": [_item_ml("MLB7", "dg053.sp", var="v1")]}
    job = await _job(db, dono, [ml])

    await pb.run_atualizar(job.id, [ml.id])

    await db.refresh(job)
    link = (await db.execute(select(ProductLink))).scalar_one()
    assert (link.external_id, link.variation_id, link.product_id) == ("MLB7", "v1", p.id)
    v = job.result["vincular"]
    assert v["created"] == 1 and v["ok_integrations"] == 1
    assert v["mudancas"][0]["acao"] == "criado" and v["mudancas"][0]["para"] == "dg053.sp"
    # O espelho do Vincular acerta o produto mostrado na aba Anúncios.
    assert v["lista_anuncios"]["passou_a_ter_produto"] == 1
    linha = (await db.execute(select(Listing))).scalar_one()
    assert linha.product_id == p.id
    assert any(d.get("etapa") == "vincular" for d in await _detalhes(db, job.id))


@pytest.mark.asyncio
async def test_tipo_do_anuncio_do_ml_nao_muda(db: AsyncSession, lojas, monkeypatch):
    """Decisão 2 (Margem): o Atualizar não mexe em listings.listing_type do ML.
    Linha que já existe fica com o gravado; linha NOVA do ML fica NULL."""
    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    db.add(
        Listing(
            user_id=dono.id, integration_id=ml.id, platform=IntegrationPlatform.ML,
            external_id="MLB1", sku="x1", title="velho", listing_type="gold_pro",
            status=ListingStatus.ACTIVE,
        )
    )
    await db.commit()
    lojas["dados"]["ml"] = {
        "a": [
            _item_ml("MLB1", "x1", tipo="gold_special"),
            _item_ml("MLB2", "x2", tipo="gold_pro"),
        ]
    }

    async def _sem_vincular(job_id, ids, summary, **kw):
        kw["por_conta"].update({i: {"result": "ok"} for i in ids})

    monkeypatch.setattr(al, "vincular_contas", _sem_vincular)
    job = await _job(db, dono, [ml])
    await pb.run_atualizar(job.id, [ml.id])

    rows = {r.external_id: r for r in (await db.execute(select(Listing))).scalars()}
    for r in rows.values():
        await db.refresh(r)
    assert rows["MLB1"].listing_type == "gold_pro"  # preservado
    assert rows["MLB1"].title == "anúncio MLB1"  # o resto foi atualizado
    assert rows["MLB2"].listing_type is None  # linha nova do ML: NULL


@pytest.mark.asyncio
async def test_margem_continua_escolhendo_a_mesma_linha_do_sku(db: AsyncSession):
    """Achado da revisão (decisão 2): a view da Margem escolhe a linha do SKU
    por updated_at DESC. O Atualizar não pode trocar a escolhida (a leitura do
    ML não tem ordem) nem pôr na frente a linha nova de tipo NULL."""
    from app.config import get_settings
    from app.services.listings_import import _upsert_listing

    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a")

    async def _escolhida() -> str | None:
        sc = get_settings().database_schema
        r = await db.execute(
            text(
                f'SELECT listing_type FROM "{sc}".listings WHERE integration_id = :i '  # noqa: S608
                "AND sku = 'rev9.sp' AND platform::text = 'ml' "
                "ORDER BY updated_at DESC NULLS LAST LIMIT 1"
            ),
            {"i": ml.id},
        )
        return r.scalar()

    def n(ext: str, tipo: str) -> dict:
        return {"external_id": ext, "sku": "rev9.sp", "title": "t", "status": "active",
                "listing_type": tipo, "raw": {"id": ext}}

    # importação antiga: o clássico gravado por último é o que a Margem usa
    await _upsert_listing(db, user_id=dono.id, integ=ml, norm=n("MLB1", "gold_pro"))
    await db.commit()
    await asyncio.sleep(0.05)
    await _upsert_listing(db, user_id=dono.id, integ=ml, norm=n("MLB2", "gold_special"))
    await db.commit()
    antes = await _escolhida()
    assert antes == "gold_special"
    # o Atualizar lê na ordem inversa e acha um anúncio NOVO do mesmo SKU
    for ext, tipo in (("MLB2", "gold_special"), ("MLB1", "gold_pro"), ("MLB3", "gold_pro")):
        await asyncio.sleep(0.05)
        r = await _upsert_listing(
            db, user_id=dono.id, integ=ml, norm=n(ext, tipo),
            criar_encerrado=False, manter_tipo_ml=True,
        )
        await db.commit()
    assert r == "created"
    assert await _escolhida() == antes
    novo = (await db.execute(select(Listing).where(Listing.external_id == "MLB3"))).scalar_one()
    assert novo.listing_type is None


@pytest.mark.asyncio
async def test_rodada_repetida_com_job_terminado_nao_faz_nada(db: AsyncSession, lojas):
    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    lojas["dados"]["ml"] = {"a": [_item_ml("MLB1", "x1")]}
    job = await _job(db, dono, [ml])
    await pb.run_atualizar(job.id, [ml.id])
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    fim, detalhes = job.finished_at, len(await _detalhes(db, job.id))
    lojas["chamadas"].clear()

    await pb.run_atualizar(job.id, [ml.id])  # o arq rodando de novo

    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED and job.finished_at == fim
    assert len(await _detalhes(db, job.id)) == detalhes
    assert lojas["chamadas"] == []


@pytest.mark.asyncio
async def test_rodada_repetida_com_job_preso_recomeca_do_zero(db: AsyncSession, lojas):
    """Worker morto no meio (job RUNNING, sem fim): a rodada nova apaga os
    detalhes da anterior — a barra não passa de 100% — e refaz tudo."""
    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    lojas["dados"]["ml"] = {"a": [_item_ml("MLB1", "x1")]}
    job = await _job(db, dono, [ml])
    job.status = BackgroundJobStatus.RUNNING
    job.processed = 7
    await db.commit()
    from app.services.job_details import append_job_detail

    await append_job_detail(db, job.id, {"etapa": "conta", "nome": "da rodada morta"})
    await db.commit()

    await pb.run_atualizar(job.id, [ml.id])

    await db.refresh(job)
    contas = [d for d in await _detalhes(db, job.id) if d.get("etapa") == "conta"]
    assert [d["nome"] for d in contas] == ["ml-a"]
    assert job.processed == 2 and job.status == BackgroundJobStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_job_interrompido_vira_failed_e_solta_as_travas(db: AsyncSession, lojas):
    """Timeout/reinício do arq chega como CancelledError: o job vira FAILED
    'interrompido' e a trava do Flex é solta."""
    from app.services.flex_motor import _MOTOR_LOCK_KEY

    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    entrou = asyncio.Event()

    async def _trava_na_leitura():
        entrou.set()
        await asyncio.sleep(30)

    lojas["dados"]["ml"] = {"a": [_item_ml("MLB1", "x1"), _trava_na_leitura]}
    job = await _job(db, dono, [ml])

    tarefa = asyncio.create_task(pb.run_atualizar(job.id, [ml.id]))
    await asyncio.wait_for(entrou.wait(), timeout=10)
    assert not await _trava_livre(_MOTOR_LOCK_KEY)  # segurando durante a lista
    tarefa.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarefa

    await db.refresh(job)
    assert job.status == BackgroundJobStatus.FAILED
    assert job.error == "interrompido (timeout/reinício)"
    assert job.finished_at is not None
    assert await _trava_livre(_MOTOR_LOCK_KEY)
    # cortado no Vincular (antes da lista): a lista do ML não foi gravada
    assert (await db.execute(select(func.count(Listing.id)))).scalar() == 0


@pytest.mark.asyncio
async def test_worker_sem_contas_vira_failed(db: AsyncSession):
    from app.worker import atualizar_run

    dono = await _dono(db)
    job = await _job(db, dono, [])
    await atualizar_run({}, str(job.id), [])
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.FAILED and job.error == "sem_contas"


# ───────────────────── decisão 1: a trava do Flex ───────────────────────────


@pytest.mark.asyncio
async def test_flex_rodando_o_tempo_todo_nao_grava_ml_nem_shopee(db: AsyncSession, lojas):
    """A rodada do Flex não soltou dentro do prazo: as listas do ML e da
    Shopee NÃO são gravadas (contas puladas com o aviso) e o resto segue."""
    from app.services.flex_motor import _MOTOR_LOCK_KEY

    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    sh = await _conta(db, dono, IntegrationPlatform.SHOPEE, "sh-a", marca="a")
    tt = await _conta(db, dono, IntegrationPlatform.TIKTOK, "tt-a", marca="a")
    lojas["dados"]["ml"] = {"a": [_item_ml("MLB1", "x1", var="v1")]}
    lojas["dados"]["shopee"] = {"a": [_item_ml("999", "x2")]}
    lojas["dados"]["tiktok"] = {
        "a": [{"product_id": "P1", "title": "t", "status": "ACTIVATE",
               "skus": [{"id": "S1", "seller_sku": "x3", "stock": 1}]}]
    }
    job = await _job(db, dono, [ml, sh, tt])

    async with session_scope() as rodada_flex:
        await rodada_flex.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :k)"),
            {"ns": SYNC_NAMESPACE, "k": _MOTOR_LOCK_KEY},
        )
        await pb.run_atualizar(job.id, [ml.id, sh.id, tt.id])

    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.result["flex"]["conseguiu"] is False
    contas = {d["nome"]: d for d in await _detalhes(db, job.id) if d.get("etapa") == "conta"}
    for nome in ("ml-a", "sh-a"):
        assert contas[nome]["resultado"] == "pulada"
        assert contas[nome]["erro"] == pb.MSG_FLEX_OCUPADO
    assert contas["tt-a"]["resultado"] == "ok"
    plataformas = {r.platform for r in (await db.execute(select(Listing))).scalars()}
    assert plataformas == {IntegrationPlatform.TIKTOK}
    # ML e Shopee não foram nem lidas (nem a lista, nem o Vincular).
    assert {p for p, _ in lojas["chamadas"]} == {"tiktok"}


@pytest.mark.asyncio
async def test_espera_a_rodada_do_flex_terminar_e_segue(db: AsyncSession, lojas, monkeypatch):
    from app.services.flex_motor import _MOTOR_LOCK_KEY

    monkeypatch.setattr(pb, "FLEX_ESPERA_MAX_S", 10.0)
    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    lojas["dados"]["ml"] = {"a": [_item_ml("MLB1", "x1")]}
    job = await _job(db, dono, [ml])
    pegou = asyncio.Event()

    async def _rodada_do_flex():
        async with session_scope() as s:
            await s.execute(
                text("SELECT pg_advisory_xact_lock(:ns, :k)"),
                {"ns": SYNC_NAMESPACE, "k": _MOTOR_LOCK_KEY},
            )
            pegou.set()
            await asyncio.sleep(1.2)

    flex = asyncio.create_task(_rodada_do_flex())
    await pegou.wait()
    await pb.run_atualizar(job.id, [ml.id])
    await flex

    await db.refresh(job)
    assert job.result["flex"]["conseguiu"] is True
    assert job.result["flex"]["esperou_s"] >= 1
    assert job.result["anuncios"]["ok"] == 1


@pytest.mark.asyncio
async def test_segura_o_flex_do_comeco_da_lista_ate_o_fim_do_vincular(
    db: AsyncSession, lojas, monkeypatch
):
    """Com a trava livre, o Atualizar a pega ANTES da primeira lista e só
    solta depois do Vincular e do espelho: a rodada do Flex chamada nesse meio
    (cron, botão, aprovação, gancho — todos passam por rodar_motor) volta
    'ocupado', sem erro. Depois do fim, a trava está livre de novo."""
    from app.services import flex_config, flex_motor
    from app.services.flex_motor import _MOTOR_LOCK_KEY

    monkeypatch.setattr(flex_config, "modo", lambda valor=None: "observar")
    monkeypatch.setattr(flex_config, "contas", lambda valor=None: frozenset({uuid.uuid4()}))

    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    db.add(Product(user_id=dono.id, sku="x1", name="X", stock=1))
    await db.commit()
    vistos: list[tuple[str, bool, Any]] = []

    async def _confere():
        livre = await _trava_livre(_MOTOR_LOCK_KEY)
        motor = await flex_motor.rodar_motor(origem="cron")
        vistos.append(("leitura", livre, motor.get("ocupado")))

    lojas["dados"]["ml"] = {"a": [_confere, _item_ml("MLB1", "x1", var="v1")]}

    espelho_original = pb.espelhar_vinculos_na_lista

    async def _espelho(session, ids):
        vistos.append(("espelho", await _trava_livre(_MOTOR_LOCK_KEY), None))
        return await espelho_original(session, ids)

    monkeypatch.setattr(pb, "espelhar_vinculos_na_lista", _espelho)
    job = await _job(db, dono, [ml])
    await pb.run_atualizar(job.id, [ml.id])

    # 1ª leitura = o Vincular (antes da lista: revisão de 17/11/2026); 2ª = a
    # lista; por fim o espelho. Em todas a trava estava presa e a rodada do
    # Flex voltou ocupada.
    assert vistos == [
        ("leitura", False, True),
        ("leitura", False, True),
        ("espelho", False, None),
    ]
    await db.refresh(job)
    assert job.result["flex"] == {"esperou_s": 0, "conseguiu": True}
    assert job.result["vincular"]["created"] == 1
    assert await _trava_livre(_MOTOR_LOCK_KEY)


async def _variacoes_sem_vinculo(db: AsyncSession) -> list[tuple[str, str]]:
    """(anúncio, variação) que está na lista do ML e não tem vínculo — o que o
    robô do Flex desligaria."""
    saida = []
    for lst in (await db.execute(select(Listing))).scalars():
        await db.refresh(lst)
        for v in (lst.raw_data or {}).get("variations") or []:
            tem = (
                await db.execute(
                    select(func.count(ProductLink.id)).where(
                        ProductLink.external_id == lst.external_id,
                        ProductLink.variation_id == str(v.get("id")),
                    )
                )
            ).scalar()
            if not tem:
                saida.append((lst.external_id, str(v.get("id"))))
    return saida


@pytest.mark.asyncio
async def test_cancelado_entre_contas_nao_deixa_variacao_sem_vinculo(db: AsyncSession, lojas):
    """Achado da revisão: publicação/timeout no meio deixava a lista do ML
    gravada sem o Vincular. Agora o Vincular vem antes: cortado em qualquer
    ponto, a lista fica a velha ou já vinculada."""
    from app.services.flex_motor import _MOTOR_LOCK_KEY

    dono = await _dono(db)
    a = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    b = await _conta(db, dono, IntegrationPlatform.ML, "ml-b", marca="b")
    db.add(Product(user_id=dono.id, sku="x1", name="X", stock=1))
    await db.commit()
    entrou = asyncio.Event()

    async def _b_lenta():
        entrou.set()
        await asyncio.sleep(30)

    lojas["dados"]["ml"] = {"a": [_item_ml("MLB1", "x1", var="v1")], "b": [_b_lenta]}
    job = await _job(db, dono, [a, b])
    tarefa = asyncio.create_task(pb.run_atualizar(job.id, [a.id, b.id]))
    await asyncio.wait_for(entrou.wait(), timeout=10)
    await asyncio.sleep(0.5)  # a conta 'a' termina o que tinha para fazer
    tarefa.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarefa
    assert await _variacoes_sem_vinculo(db) == []
    assert await _trava_livre(_MOTOR_LOCK_KEY)


@pytest.mark.asyncio
async def test_vincular_pendente_nao_grava_a_lista_da_conta(db: AsyncSession, lojas):
    """O Vincular da conta esgota as tentativas: a lista dela NÃO é gravada
    (o Flex continua com a velha) e a conta aparece com o erro e a dica."""
    dono = await _dono(db)
    a = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    boa = await _conta(db, dono, IntegrationPlatform.ML, "ml-boa", marca="boa")
    db.add(Product(user_id=dono.id, sku="x1", name="X", stock=1))
    await db.commit()

    async def _429():
        raise RuntimeError("ml 429 too many requests")

    lojas["dados"]["ml"] = {
        "a": [_item_ml("MLB1", "x1", var="v1"), _429],
        "boa": [_item_ml("MLB2", "x1", var="v2")],
    }
    job = await _job(db, dono, [a, boa])
    await pb.run_atualizar(job.id, [a.id, boa.id])

    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.result["vincular"]["pending_integrations"] == 1
    assert job.error.startswith("Vincular incompleto: 1 conta(s) pendente(s)")
    linhas = {r.external_id for r in (await db.execute(select(Listing))).scalars()}
    assert linhas == {"MLB2"}
    assert await _variacoes_sem_vinculo(db) == []
    contas = {d["nome"]: d for d in await _detalhes(db, job.id) if d.get("etapa") == "conta"}
    assert contas["ml-a"]["resultado"] == "falha"
    assert "429" in contas["ml-a"]["erro"] and "não foi gravada" in contas["ml-a"]["erro"]
    assert contas["ml-boa"]["resultado"] == "ok"
    # a lista da conta pendente nem foi lida (só as tentativas do Vincular)
    assert lojas["chamadas"].count(("ml", "a")) == al.AUTOLINK_MAX_TRIES


@pytest.mark.asyncio
async def test_trava_do_flex_so_no_que_o_flex_le(db: AsyncSession, lojas, monkeypatch):
    """Achado da revisão: a trava do Flex ficava presa no Atualizar inteiro.
    Agora a lista do TikTok e o Vincular do TikTok e da Amazon rodam com ela
    LIVRE; só o Vincular e a lista do ML/Shopee (e o espelho) a seguram."""
    from app.services.flex_motor import _MOTOR_LOCK_KEY

    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    tt = await _conta(db, dono, IntegrationPlatform.TIKTOK, "tt-a", marca="a")
    amz = await _conta(db, dono, IntegrationPlatform.AMAZON, "amz-a", marca="a")
    db.add(Product(user_id=dono.id, sku="x1", name="X", stock=1))
    await db.commit()
    vistos: list[tuple[str, bool]] = []

    async def _confere_ml():
        vistos.append(("ml", not await _trava_presa(_MOTOR_LOCK_KEY)))

    async def _tiktok(self, page_size: int = 20, page_token: str | None = None):
        vistos.append(("tiktok", not await _trava_presa(_MOTOR_LOCK_KEY)))
        return [], None

    async def _amazon(self, **kw):
        vistos.append(("amazon", not await _trava_presa(_MOTOR_LOCK_KEY)))
        if False:  # pragma: no cover — gerador vazio
            yield {}

    monkeypatch.setattr(tt_mod.TikTokClient, "search_products", _tiktok)
    monkeypatch.setattr(amazon_mod.AmazonClient, "list_listings", _amazon)
    lojas["dados"]["ml"] = {"a": [_confere_ml, _item_ml("MLB1", "x1", var="v1")]}
    job = await _job(db, dono, [ml, tt], vincular_amazon_ids=[str(amz.id)])
    await pb.run_atualizar(job.id, [ml.id, tt.id])

    assert ("ml", False) in vistos and ("ml", True) not in vistos
    assert {v for p, v in vistos if p == "tiktok"} == {True}
    assert {v for p, v in vistos if p == "amazon"} == {True}
    await db.refresh(job)
    assert job.result["vincular"]["ok_integrations"] >= 2
    fases = [d["fase"] for d in await _detalhes(db, job.id) if d.get("etapa") == "fase"]
    assert fases == ["listas", "vincular", "listas_flex", "vincular"]


@pytest.mark.asyncio
async def test_conta_que_passa_do_prazo_vira_falha(db: AsyncSession, lojas, monkeypatch):
    """Achado da revisão: uma conta travada segurava o job (e a trava do Flex)
    até o timeout do arq. Com o prazo por conta, ela vira 'falha' e o resto
    segue."""
    monkeypatch.setattr(pb, "LISTA_TIMEOUT_S", 0.3)
    dono = await _dono(db)
    mg = await _conta(db, dono, IntegrationPlatform.MAGALU, "mg-a")
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    lojas["dados"]["ml"] = {"a": [_item_ml("MLB1", "x1")]}

    async def _magalu_travada(self, **kw):
        await asyncio.sleep(30)
        yield {}

    from app.services.marketplaces import magalu as magalu_mod

    monkeypatch.setattr(magalu_mod.MagaluClient, "list_listings", _magalu_travada)
    monkeypatch.setattr(al, "_link_dispatch", AsyncMock(return_value=al._stats()))
    job = await _job(db, dono, [mg, ml])
    await asyncio.wait_for(pb.run_atualizar(job.id, [mg.id, ml.id]), timeout=20)

    await db.refresh(job)
    contas = {d["nome"]: d for d in await _detalhes(db, job.id) if d.get("etapa") == "conta"}
    assert contas["mg-a"]["resultado"] == "falha"
    assert "passou de" in contas["mg-a"]["erro"]
    assert contas["mg-a"]["dica"] == pb.DICA_LENTO
    assert contas["ml-a"]["resultado"] == "ok"


@pytest.mark.asyncio
async def test_tiktok_pagina_lenta_vira_erro_e_teto_de_paginas(monkeypatch):
    tt = tt_mod.TikTokClient(dict(_CREDS[IntegrationPlatform.TIKTOK]))

    async def _lenta(page_size: int = 20, page_token: str | None = None):
        await asyncio.sleep(5)
        return [], None

    monkeypatch.setattr(tt, "search_products", _lenta)
    with pytest.raises(TimeoutError, match="passou de"):
        async for _ in tt.list_listings(page_timeout=0.1):
            pass

    async def _sem_fim(page_size: int = 20, page_token: str | None = None):
        return [{"product_id": "P", "title": "t", "status": "ACTIVATE",
                 "skus": [{"id": "S", "seller_sku": "x", "stock": 1}]}], "mais"

    monkeypatch.setattr(tt, "search_products", _sem_fim)
    lidos = [x async for x in tt.list_listings(max_pages=3)]
    assert len(lidos) == 3 and tt.listagem_completa is False


@pytest.mark.asyncio
async def test_bling_pulado_vira_texto_no_erro():
    st, erro = pb._status_final(
        {"anuncios": {"ok": 0}, "bling": {"pulado": "rodada_automatica_em_andamento"}}
    )
    assert st == BackgroundJobStatus.FAILED
    assert "rodada_automatica" not in erro and "rodada automática" in erro


# ───────────────────── listas: encerrado, TikTok ────────────────────────────


@pytest.mark.asyncio
async def test_encerrado_desconhecido_nao_e_criado_e_conhecido_e_atualizado(
    db: AsyncSession, lojas
):
    from app.services.listings_import import importar_anuncios_conta

    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml-a", marca="a")
    db.add(
        Listing(
            user_id=dono.id, integration_id=ml.id, platform=IntegrationPlatform.ML,
            external_id="MLB-VELHO", sku="x1", title="t", status=ListingStatus.ACTIVE,
        )
    )
    await db.commit()
    lojas["dados"]["ml"] = {
        "a": [
            _item_ml("MLB-VELHO", "x1", status="closed"),
            _item_ml("MLB-MORTO", "x2", status="closed", var="v1"),
            _item_ml("MLB-MORTO", "x3", status="closed", var="v2"),
            _item_ml("MLB-NOVO", "x4", var="v1"),
            _item_ml("MLB-NOVO", "x5", var="v2"),
        ]
    }
    r = await importar_anuncios_conta(db, ml)

    # Cada anúncio conta uma vez (o leitor manda uma linha por variação).
    assert (r["created"], r["updated"], r["skipped"]) == (1, 1, 1)
    rows = {x.external_id: x for x in (await db.execute(select(Listing))).scalars()}
    assert set(rows) == {"MLB-VELHO", "MLB-NOVO"}
    await db.refresh(rows["MLB-VELHO"])
    assert rows["MLB-VELHO"].status == ListingStatus.CLOSED


def test_tiktok_status_e_pagina_de_100():
    import inspect

    from app.services.marketplaces.tiktok import _tiktok_status_to_listing

    assert _tiktok_status_to_listing("ACTIVATE") == "active"
    # congelado e desativado pelo TikTok: mortos para o Vincular → encerrado
    assert _tiktok_status_to_listing("FREEZE") == "closed"
    assert _tiktok_status_to_listing("DELETED") == "closed"
    assert _tiktok_status_to_listing("PLATFORM_DEACTIVATED") == "closed"
    assert _tiktok_status_to_listing("SELLER_DEACTIVATED") == "paused"
    assert _tiktok_status_to_listing("LIVE") == "active"
    padrao = inspect.signature(tt_mod.TikTokClient.list_listings).parameters["page_size"]
    assert padrao.default == 100


@pytest.mark.asyncio
async def test_lista_do_tiktok_entra(db: AsyncSession, lojas):
    from app.services.listings_import import _client_for_listings, importar_anuncios_conta

    dono = await _dono(db)
    tt = await _conta(db, dono, IntegrationPlatform.TIKTOK, "tt-a", marca="a")
    assert isinstance(_client_for_listings(db, tt), tt_mod.TikTokClient)
    lojas["dados"]["tiktok"] = {
        "a": [
            {"product_id": "P1", "title": "t", "status": "ACTIVATE",
             "skus": [{"id": "S1", "seller_sku": "x1", "stock": 2}]},
            {"product_id": "P2", "title": "t", "status": "FREEZE",
             "skus": [{"id": "S2", "seller_sku": "x2", "stock": 0}]},
            {"product_id": "P3", "title": "t", "status": "DELETED",
             "skus": [{"id": "S3", "seller_sku": "x3", "stock": 0}]},
        ]
    }
    db.add(
        Listing(
            user_id=dono.id, integration_id=tt.id, platform=IntegrationPlatform.TIKTOK,
            external_id="P4", sku="x4", title="t", status=ListingStatus.ACTIVE,
        )
    )
    await db.commit()
    lojas["dados"]["tiktok"]["a"].append(
        {"product_id": "P4", "title": "t", "status": "PLATFORM_DEACTIVATED",
         "skus": [{"id": "S4", "seller_sku": "x4", "stock": 0}]}
    )
    r = await importar_anuncios_conta(db, tt)
    # congelado/excluído/desativado que o DaVinci não conhecia não vira linha;
    # o que já existia é atualizado (encerrado)
    assert (r["created"], r["updated"], r["skipped"]) == (1, 1, 2)
    rows = {x.external_id: x for x in (await db.execute(select(Listing))).scalars()}
    for x in rows.values():
        await db.refresh(x)
    assert {k: v.status for k, v in rows.items()} == {
        "P1": ListingStatus.ACTIVE,
        "P4": ListingStatus.CLOSED,
    }


@pytest.mark.asyncio
async def test_jobs_do_flex_que_esperam_a_trava_saem_sem_erro_na_ultima_tentativa(
    db: AsyncSession, monkeypatch
):
    """Enquanto o Atualizar segura a trava, o gancho do pedido Flex e a
    aprovação tentam de novo a cada 1 min; na ÚLTIMA tentativa saem sem erro
    (antes: 'max retries exceeded' no arq) e deixam para a varredura — a
    aprovação continua valendo e o desligar sai na rodada seguinte do cron."""
    from arq import Retry

    from app import worker
    from app.services import flex_motor

    async def _ocupado(**kw):
        return {"ocupado": True, "rodou": False}

    async def _aprovado_ocupado(iid, ext, *, por=None, modo=None):
        return {"ocupado": True, "aplicado": False}

    monkeypatch.setattr(flex_motor, "rodar_motor", _ocupado)
    monkeypatch.setattr(flex_motor, "aplicar_aprovado", _aprovado_ocupado)

    with pytest.raises(Retry):
        await worker.flex_reavaliar_run({"job_try": 1})
    r = await worker.flex_reavaliar_run({"job_try": worker._FLEX_REAVALIAR_TENTATIVAS})
    assert r["fica_para_a_varredura"] is True

    iid = str(uuid.uuid4())
    with pytest.raises(Retry):
        await worker.flex_aprovado_run({"job_try": 9}, iid, "MLB1")
    r = await worker.flex_aprovado_run(
        {"job_try": worker._FLEX_APROVADO_TENTATIVAS}, iid, "MLB1"
    )
    assert r["fica_para_a_varredura"] is True
    aprovado = next(
        f for f in worker.WorkerSettings.functions
        if getattr(f, "name", None) == "flex_aprovado_run"
    )
    assert aprovado.max_tries == worker._FLEX_APROVADO_TENTATIVAS
    assert worker.WorkerSettings.max_tries == worker._FLEX_REAVALIAR_TENTATIVAS

    # O botão "Sincronizar" da tela Flex (achado da revisão): ocupado não sai
    # mais calado — tenta de novo em 1 min, até a última, que sai sem erro.
    chamou: list[dict] = []

    async def _ocupado_manual(**kw):
        chamou.append(kw)
        return {"ocupado": True, "rodou": False}

    monkeypatch.setattr(flex_motor, "rodar_motor", _ocupado_manual)
    with pytest.raises(Retry):
        await worker.flex_motor_run({"job_try": 1}, None)
    r = await worker.flex_motor_run({"job_try": worker._FLEX_MOTOR_RUN_TENTATIVAS}, None)
    assert r["fica_para_a_varredura"] is True
    assert chamou[0]["reler_contas"] is True and chamou[0]["origem"] == "manual"
    botao = next(
        f for f in worker.WorkerSettings.functions
        if getattr(f, "name", None) == "flex_motor_run"
    )
    assert botao.max_tries == worker._FLEX_MOTOR_RUN_TENTATIVAS
