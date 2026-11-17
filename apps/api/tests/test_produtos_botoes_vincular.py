"""Produtos › botão VINCULAR (17/11/2026) e o que o ATUALIZAR herda dele.

- filtro da promoção listings → product_links (pré-requisito do Atualizar:
  a lista do ML fica fresca e a linha é UMA por anúncio, o vínculo é por
  variação);
- o Vincular não cria vínculo de anúncio que o marketplace já dá como morto,
  conta os SKUs atualizados e guarda amostras (movidos/criados, SKUs sem
  produto);
- o espelho na aba Anúncios (listings.product_id) pelos vínculos vivos;
- espião no httpx: nem o Atualizar nem o Vincular escrevem no marketplace
  ou no Bling (só GET, e o POST de consulta da busca do TikTok).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.services.auto_link import run_auto_link
from app.services.listings_import import _create_product_links_for_matched
from app.services.marketplaces import ml as ml_mod
from app.services.marketplaces import tiktok as tt_mod

_FUTURO = 9999999999


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
    db: AsyncSession, dono: User, plataforma: IntegrationPlatform, nome: str,
    *, creds: dict | None = None, arquivada: bool = False,
) -> Integration:
    integ = Integration(
        user_id=dono.id,
        platform=plataforma,
        name=nome,
        credentials=encrypt_json(creds or {"access_token": "x", "user_id": 1,
                                            "expires_at": _FUTURO}),
        archived_at=datetime.now(UTC) if arquivada else None,
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _produto(db: AsyncSession, dono: User, sku: str, *, situacao: str = "A") -> Product:
    p = Product(user_id=dono.id, sku=sku, name=sku, stock=1, situacao=situacao)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _job(db: AsyncSession, dono: User, tipo=BackgroundJobType.AUTO_LINK) -> BackgroundJob:
    job = BackgroundJob(type=tipo, status=BackgroundJobStatus.PENDING, created_by=dono.id)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


def _ml(ext: str, sku: str | None, *, var: str | None = None, status: str = "active"):
    return {
        "external_id": ext, "variation_id": var, "sku": sku, "title": ext,
        "stock": 1, "status": status, "listing_type": "gold_special", "raw": {},
    }


def _fake_ml(monkeypatch, itens: list[dict]) -> None:
    async def _ler(self, **kw):
        for it in itens:
            yield it

    monkeypatch.setattr(ml_mod.MercadoLivreClient, "list_listings", _ler)


async def _links(db: AsyncSession) -> dict[tuple[str, str | None], ProductLink]:
    out = {}
    for link in (await db.execute(select(ProductLink))).scalars():
        await db.refresh(link)
        out[(link.external_id, link.variation_id)] = link
    return out


# ─────────────── 3. filtro da promoção listings → product_links ─────────────


@pytest.mark.asyncio
async def test_promocao_so_o_que_a_linha_diz_sem_adivinhar(db: AsyncSession):
    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml")
    ml_arq = await _conta(db, dono, IntegrationPlatform.ML, "ml-arq", arquivada=True)
    sh = await _conta(db, dono, IntegrationPlatform.SHOPEE, "sh")
    tt = await _conta(db, dono, IntegrationPlatform.TIKTOK, "tt")
    amz = await _conta(db, dono, IntegrationPlatform.AMAZON, "amz")
    cf = await _conta(db, dono, IntegrationPlatform.CARREFOUR, "cf")
    mg = await _conta(db, dono, IntegrationPlatform.MAGALU, "mg")
    p = await _produto(db, dono, "dg053.sp")
    outro = await _produto(db, dono, "dg053.ci")

    def _l(integ, ext, sku, *, raw=None, status=ListingStatus.ACTIVE, prod=p):
        return Listing(
            user_id=dono.id, integration_id=integ.id, platform=integ.platform,
            external_id=ext, sku=sku, title=ext, status=status, product_id=prod.id,
            raw_data=raw or {},
        )

    db.add_all([
        # NÃO promovem
        _l(ml, "ML-VAR", "dg053.sp", raw={"variations": [{"id": 1}]}),
        _l(sh, "777", "dg053.sp", raw={"item": {}, "model": {"model_id": 1}}),
        _l(tt, "TT1", "dg053.sp"),
        _l(amz, "AMZ1", "dg053.sp"),
        _l(cf, "CF-SKU-OUTRO", "dg053.ci"),  # Carrefour com product_id velho
        _l(ml, "ML-FECHADO", "dg053.sp", status=ListingStatus.CLOSED),
        _l(ml_arq, "ML-ARQ", "dg053.sp"),
        _l(ml, "ML-SKU-OUTRO", "dg053.ci"),  # product_id velho: aponta p/ dg053.sp
        _l(sh, "778", "dg053.ci"),
        # promovem
        _l(ml, "ML-SIMPLES", "  DG053.SP "),
        _l(sh, "779", "dg053.sp"),
        _l(mg, "dg053-sp", "dg053.sp", raw={"status": "PUBLISHED"}),
        _l(ml, "ML-OUTRO-OK", "dg053.ci", prod=outro),
        # Carrefour: o Vincular não tem leitor dele — esta promoção é o único
        # caminho do vínculo (oferta sem variação), com a régua do SKU
        _l(cf, "CF1", " DG053.SP"),
    ])
    await db.commit()

    criados = await _create_product_links_for_matched(db)
    await db.commit()

    links = await _links(db)
    assert set(links) == {
        ("ML-SIMPLES", None),
        ("779", "0"),  # Shopee sem modelo: a chave '0' do Vincular
        ("dg053-sp", None),
        ("ML-OUTRO-OK", None),
        ("CF1", None),
    }
    assert criados == 5
    assert links[("ML-OUTRO-OK", None)].product_id == outro.id


# ─────────────── 4. VINCULAR: morto não vira vínculo novo ───────────────────


@pytest.mark.asyncio
async def test_tiktok_morto_sem_vinculo_nao_e_criado_com_vinculo_e_marcado(
    db: AsyncSession, monkeypatch
):
    dono = await _dono(db)
    tt = await _conta(db, dono, IntegrationPlatform.TIKTOK, "tt",
                      creds={"app_key": "k", "app_secret": "s", "access_token": "t"})
    a, b, c, d = [await _produto(db, dono, s) for s in ("a.sp", "b.sp", "c.sp", "d.sp")]
    db.add(ProductLink(user_id=dono.id, product_id=d.id, integration_id=tt.id,
                       platform=IntegrationPlatform.TIKTOK, external_id="P4",
                       variation_id="S4", external_sku="d.sp"))
    await db.commit()

    def _p(pid, sid, sku, status):
        return {"product_id": pid, "title": pid, "status": status,
                "skus": [{"id": sid, "seller_sku": sku, "stock": 0}]}

    async def _busca(self, page_size=20, page_token=None):
        return [
            _p("P1", "S1", "a.sp", "DELETED"),
            _p("P2", "S2", "b.sp", "FREEZE"),
            _p("P3", "S3", "c.sp", "ACTIVATE"),
            _p("P4", "S4", "d.sp", "FREEZE"),
        ], None

    monkeypatch.setattr(tt_mod.TikTokClient, "search_products", _busca)
    job = await _job(db, dono)
    await run_auto_link(db, job_id=job.id, integration_ids=[tt.id])

    await db.refresh(job)
    r = job.result
    assert (r["created"], r["mortos_nao_criados"], r["mortos"]) == (1, 2, 1)
    links = await _links(db)
    assert set(links) == {("P3", "S3"), ("P4", "S4")}
    assert links[("P4", "S4")].morto_desde is not None


@pytest.mark.asyncio
async def test_ml_encerrado_sem_vinculo_nao_e_criado_com_vinculo_e_marcado(
    db: AsyncSession, monkeypatch
):
    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml")
    a, b, c = [await _produto(db, dono, s) for s in ("a.sp", "b.sp", "c.sp")]
    db.add(ProductLink(user_id=dono.id, product_id=b.id, integration_id=ml.id,
                       platform=IntegrationPlatform.ML, external_id="MLB2",
                       external_sku="b.sp"))
    await db.commit()
    _fake_ml(monkeypatch, [
        _ml("MLB1", "a.sp", status="closed"),
        _ml("MLB2", "b.sp", status="closed"),
        _ml("MLB3", "c.sp"),
    ])
    job = await _job(db, dono)
    await run_auto_link(db, job_id=job.id, integration_ids=[ml.id])

    await db.refresh(job)
    r = job.result
    assert (r["created"], r["mortos_nao_criados"], r["mortos"]) == (1, 1, 1)
    links = await _links(db)
    assert set(links) == {("MLB2", None), ("MLB3", None)}
    assert links[("MLB2", None)].morto_desde is not None


# ─────────────── 4. contadores, amostras, sufixo/kit, ambíguo ───────────────


@pytest.mark.asyncio
async def test_sku_atualizado_conta_e_movido_vai_nas_mudancas(db: AsyncSession, monkeypatch):
    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "dream")
    ci = await _produto(db, dono, "dg053.ci")
    sp = await _produto(db, dono, "dg053.sp")
    db.add_all([
        # mesmo produto, mas o SKU guardado era o velho: só atualiza o SKU
        ProductLink(user_id=dono.id, product_id=sp.id, integration_id=ml.id,
                    platform=IntegrationPlatform.ML, external_id="MLB5",
                    external_sku="DG053.SP antigo"),
        # o SKU do anúncio virou o de outro produto: MOVE
        ProductLink(user_id=dono.id, product_id=ci.id, integration_id=ml.id,
                    platform=IntegrationPlatform.ML, external_id="MLB6",
                    variation_id="v1", external_sku="dg053.ci"),
    ])
    await db.commit()
    _fake_ml(monkeypatch, [_ml("MLB5", "dg053.sp"), _ml("MLB6", "dg053.sp", var="v1")])
    job = await _job(db, dono)
    await run_auto_link(db, job_id=job.id, integration_ids=[ml.id])

    await db.refresh(job)
    r = job.result
    assert (r["sku_atualizados"], r["repointed"], r["created"]) == (1, 1, 0)
    assert r["mudancas"] == [{
        "acao": "movido", "conta": "dream", "anuncio": "MLB6", "variacao": "v1",
        "sku_anuncio": "dg053.sp", "de": "dg053.ci", "para": "dg053.sp",
    }]
    links = await _links(db)
    assert links[("MLB5", None)].external_sku == "dg053.sp"
    assert links[("MLB6", "v1")].product_id == sp.id


@pytest.mark.asyncio
async def test_amostras_com_teto_por_conta(db: AsyncSession, monkeypatch):
    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml")
    for i in range(60):
        db.add(Product(user_id=dono.id, sku=f"p{i}.sp", name="x", stock=0))
    await db.commit()
    itens = [_ml(f"MLB{i}", f"p{i}.sp") for i in range(60)]
    itens += [_ml(f"MLX{i}", f"sem{i}.sp") for i in range(25)]
    _fake_ml(monkeypatch, itens)
    job = await _job(db, dono)
    await run_auto_link(db, job_id=job.id, integration_ids=[ml.id])

    await db.refresh(job)
    assert job.result["created"] == 60 and job.result["not_found"] == 25
    assert len(job.result["mudancas"]) == 50
    assert len(job.result["skus_sem_produto"]) == 20
    assert job.result["mudancas"][0]["acao"] == "criado"


@pytest.mark.asyncio
async def test_sufixo_de_lote_e_ordem_do_kit_nao_se_confundem(db: AsyncSession, monkeypatch):
    dono = await _dono(db)
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml")
    prods = {s: await _produto(db, dono, s)
             for s in ("dg053", "dg053.sp", "dg053.ci", "a.sp+b.sp", "b.sp+a.sp")}
    _fake_ml(monkeypatch, [_ml("MLB9", s, var=f"v{i}") for i, s in enumerate(prods)])
    job = await _job(db, dono)
    await run_auto_link(db, job_id=job.id, integration_ids=[ml.id])

    links = await _links(db)
    assert len(links) == 5  # um vínculo por variação
    for i, (sku, p) in enumerate(prods.items()):
        assert links[("MLB9", f"v{i}")].product_id == p.id, sku


@pytest.mark.asyncio
async def test_sku_ambiguo_nao_vincula(db: AsyncSession, monkeypatch):
    dono = await _dono(db)
    outro = User(open_id=f"email:o-{uuid.uuid4().hex[:6]}@x", email=f"o-{uuid.uuid4().hex[:6]}@x",
                 role=UserRole.USER, status=UserStatus.ACTIVE)
    db.add(outro)
    await db.commit()
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml")
    db.add_all([Product(user_id=dono.id, sku="ucm001m1.110", name="a", stock=0),
                Product(user_id=outro.id, sku="ucm001m1.110", name="b", stock=0)])
    await db.commit()
    _fake_ml(monkeypatch, [_ml("MLB1", "ucm001m1.110")])
    job = await _job(db, dono)
    await run_auto_link(db, job_id=job.id, integration_ids=[ml.id])

    await db.refresh(job)
    assert job.result["created"] == 0 and job.result["sku_ambiguo"] == 1
    assert await _links(db) == {}


# ─────────────── 4. espelho na aba Anúncios ──────────────────────────────────


@pytest.mark.asyncio
async def test_espelho_na_aba_anuncios(db: AsyncSession):
    dono = await _dono(db)
    x = await _conta(db, dono, IntegrationPlatform.ML, "conta-x")
    y = await _conta(db, dono, IntegrationPlatform.ML, "conta-y")  # fora dos ids
    z = await _conta(db, dono, IntegrationPlatform.ML, "conta-z", arquivada=True)
    p1, p2, p3 = [await _produto(db, dono, s) for s in ("p1.sp", "p2.sp", "p3.sp")]

    def _l(integ, ext, sku, prod=None):
        return Listing(user_id=dono.id, integration_id=integ.id,
                       platform=IntegrationPlatform.ML, external_id=ext, sku=sku,
                       title=ext, status=ListingStatus.ACTIVE,
                       product_id=prod.id if prod else None)

    def _v(integ, ext, var, prod, sku, *, morto=False):
        return ProductLink(user_id=dono.id, product_id=prod.id, integration_id=integ.id,
                           platform=IntegrationPlatform.ML, external_id=ext,
                           variation_id=var, external_sku=sku,
                           morto_desde=datetime.now(UTC) if morto else None)

    db.add_all([
        _l(x, "L1", "qualquer"), _v(x, "L1", None, p1, "p1.sp"),          # 1 produto → grava
        _l(x, "L2", "P3.SP", p1),                                         # SKU bate → prefere
        _v(x, "L2", "a", p2, "p2.sp"), _v(x, "L2", "b", p3, "p3.sp"),
        _l(x, "L3", "zzz", p1),                                           # vários, nenhum bate
        _v(x, "L3", "a", p2, "p2.sp"), _v(x, "L3", "b", p3, "p3.sp"),
        _l(x, "L4", "p1.sp"), _v(x, "L4", None, p1, "p1.sp", morto=True),  # só morto
        _l(x, "L5", "p1.sp"),                                             # sem vínculo
        _l(y, "L6", "p1.sp"), _v(y, "L6", None, p1, "p1.sp"),             # conta fora
        _l(z, "L7", "p1.sp"), _v(z, "L7", None, p1, "p1.sp"),             # arquivada
    ])
    await db.commit()
    skus_antes = {r.external_id: r.sku for r in (await db.execute(select(Listing))).scalars()}

    r = await pb.espelhar_vinculos_na_lista(db, [x.id, z.id])
    await db.commit()

    assert (r["passou_a_ter_produto"], r["trocou_produto"]) == (1, 1)
    assert {(a["anuncio"], a["de"], a["para"]) for a in r["amostra"]} == {
        ("L1", None, "p1.sp"), ("L2", "p1.sp", "p3.sp"),
    }
    rows = {}
    for row in (await db.execute(select(Listing))).scalars():
        await db.refresh(row)
        rows[row.external_id] = row
    assert rows["L1"].product_id == p1.id
    assert rows["L2"].product_id == p3.id
    assert rows["L3"].product_id == p1.id
    assert rows["L4"].product_id is None
    assert rows["L5"].product_id is None
    assert rows["L6"].product_id is None
    assert rows["L7"].product_id is None
    assert {k: v.sku for k, v in rows.items()} == skus_antes  # listings.sku intacto


# ─────────── espião: nenhum POST/PUT/PATCH no Atualizar nem no Vincular ──────

# A única escrita HTTP permitida: a busca de produtos do TikTok, que é um POST
# de CONSULTA (/product/202309/products/search, sem efeito na loja). Nada de
# token/refresh aqui: as credenciais estão válidas.
_POST_DE_LEITURA = {("POST", "open-api.tiktokglobalshop.com/product/202309/products/search")}


def _resposta(req: httpx.Request) -> httpx.Response:
    u = urlsplit(str(req.url))
    q = dict(req.url.params)
    host, path = u.hostname, u.path

    def _j(corpo: Any) -> httpx.Response:
        return httpx.Response(200, json=corpo, request=req)

    if host == "api.mercadolibre.com":
        if path.endswith("/items/search"):
            if q.get("scroll_id"):
                return _j({"results": [], "scroll_id": "s1"})
            return _j({"results": ["MLB1"], "scroll_id": "s1"})
        if path == "/items":
            return _j([{"code": 200, "body": {
                "id": "MLB1", "title": "Mala", "status": "active",
                "listing_type_id": "gold_special", "price": 100,
                "variations": [{"id": 111, "available_quantity": 3, "attribute_combinations": [],
                                "attributes": [{"id": "SELLER_SKU", "value_name": "dg053.sp"}]}],
            }}])
    if host == "partner.shopeemobile.com":
        if path == "/api/v2/product/get_item_list":
            if q.get("item_status") == "NORMAL":
                return _j({"response": {"item": [{"item_id": 555}], "has_next_page": False}})
            return _j({"response": {"item": []}})
        if path == "/api/v2/product/get_item_base_info":
            return _j({"response": {"item_list": [{
                "item_id": 555, "item_sku": "a.sp+b.sp", "has_model": False,
                "item_name": "Kit", "item_status": "NORMAL",
            }]}})
    if host == "open-api.tiktokglobalshop.com" and path.endswith("/products/search"):
        return _j({"code": 0, "data": {"next_page_token": "", "products": [{
            "id": "P1", "title": "Mala", "status": "ACTIVATE",
            "skus": [{"id": "S1", "seller_sku": "dg053.ci", "inventory": [{"quantity": 2}]}],
        }]}})
    if host == "api.bling.com.br":
        if path == "/Api/v3/produtos":
            dados = [{"id": 999, "codigo": "novo.sp", "situacao": "A"}]
            return _j({"data": dados if str(q.get("pagina")) == "1" else []})
        if path == "/Api/v3/produtos/999":
            return _j({"data": {"id": 999, "codigo": "novo.sp", "nome": "Novo",
                                "situacao": "A", "formato": "S",
                                "estoque": {"saldoVirtualTotal": 0}}})
    return httpx.Response(404, json={"erro": "rota não simulada"}, request=req)


@pytest.mark.asyncio
async def test_espiao_httpx_atualizar_e_vincular_so_leem(db: AsyncSession, monkeypatch):
    from app.services.marketplaces import bling as bling_mod

    chamadas: list[tuple[str, str]] = []

    async def _send(self, request, **kw):
        u = urlsplit(str(request.url))
        chamadas.append((request.method, f"{u.hostname}{u.path}"))
        return _resposta(request)

    async def _sem_fila(*a, **kw):
        return None

    monkeypatch.setattr(httpx.AsyncClient, "send", _send)
    monkeypatch.setattr(bling_mod, "_acquire_bling_rate_slot", _sem_fila)
    monkeypatch.setattr(al, "AUTOLINK_BACKOFF_SECONDS", [0.0, 0.0])

    dono = await _dono(db)
    await _conta(db, dono, IntegrationPlatform.BLING, "bling",
                 creds={"access_token": "b", "expires_at": _FUTURO})
    ml = await _conta(db, dono, IntegrationPlatform.ML, "ml",
                      creds={"access_token": "x", "user_id": 1, "expires_at": _FUTURO})
    sh = await _conta(db, dono, IntegrationPlatform.SHOPEE, "sh",
                      creds={"access_token": "x", "shop_id": 1, "expires_at": _FUTURO,
                             "partner_id": 1, "partner_key": "k"})
    tt = await _conta(db, dono, IntegrationPlatform.TIKTOK, "tt",
                      creds={"app_key": "k", "app_secret": "s", "access_token": "t",
                             "shop_cipher": "c"})
    prods = {s: await _produto(db, dono, s) for s in ("dg053.sp", "a.sp+b.sp", "dg053.ci")}

    # ATUALIZAR (Bling + listas + o Vincular do fim)
    job = await _job(db, dono, BackgroundJobType.IMPORT_LISTINGS)
    await pb.run_atualizar(job.id, [ml.id, sh.id, tt.id])
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED, job.error
    assert job.result["anuncios"]["ok"] == 3
    assert job.result["bling"]["criados"] == 1
    assert job.result["vincular"]["created"] == 3

    # VINCULAR (o botão: run_auto_link com o espelho)
    job2 = await _job(db, dono)
    await run_auto_link(db, job_id=job2.id, integration_ids=[ml.id, sh.id, tt.id],
                        espelhar_lista=True)
    await db.refresh(job2)
    assert job2.status == BackgroundJobStatus.SUCCEEDED
    assert job2.result["already_present"] == 3

    escritas = [c for c in chamadas if c[0] != "GET" and c not in _POST_DE_LEITURA]
    assert escritas == [], escritas
    hosts = {h.split("/")[0] for _, h in chamadas}
    assert hosts == {"api.mercadolibre.com", "partner.shopeemobile.com",
                     "open-api.tiktokglobalshop.com", "api.bling.com.br"}
    assert not [c for c in chamadas if "token" in c[1] or "oauth" in c[1]]
    links = await _links(db)
    assert {(k, link.product_id) for k, link in links.items()} == {
        (("MLB1", "111"), prods["dg053.sp"].id),
        (("555", "0"), prods["a.sp+b.sp"].id),
        (("P1", "S1"), prods["dg053.ci"].id),
    } | {
        ((link.external_id, link.variation_id), link.product_id)
        for link in links.values() if link.platform == IntegrationPlatform.BLING
    }
