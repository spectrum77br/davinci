"""Catálogo ML na Tabela de Preços (06/10/2026) — backend.

Especificação: conta de catálogo ("filha") ligada por conta ML de kit, coluna
Catálogo (preco_catalogo) como custo base, resolvedor único de anúncios por
canal, /grid com o campo `catalogo` por célula, envio com `canal_esperado`
conferido no item vivo, marca de catálogo gravada pela varredura de vínculos,
fim do Catálogo antigo (⭐/catalog-listings/push-catalog) e Lojas recusando o
tipo 'catalogo'. O Mercado Livre é SIMULADO aqui (nenhuma chamada real).
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    Base,
    Company,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Marketplace,
    PricingAccount,
    PricingOverride,
    PricingPlatform,
    PricingProduct,
    Product,
    ProductLink,
    Segment,
    Store,
    StoreInfo,
    StoreStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services.marketplaces import ml as ml_mod
from app.services.marketplaces.base import SyncStatus
from app.services.pricing import anuncios
from app.services.pricing.anuncios import (
    info_celula_catalogo,
    resolver_anuncios,
)
from app.services.pricing.calc import calculate

PERM_FULL = {
    "tabela_precos": {"view": True, "edit": True, "delete": True},
    "tabela_precos_contas": {"view": True, "edit": True, "delete": True},
    "tabela_precos_produtos": {"view": True, "edit": True, "delete": True},
    "lojas_info": {"view": True, "edit": True, "delete": True},
}


def _ml_creds() -> dict[str, Any]:
    # Credenciais de mentira — o ML é simulado em todos os testes.
    return {
        "client_id": "x",
        "client_secret": "y",
        "access_token": "tok-teste",
        "refresh_token": "ref-teste",
        "user_id": 1234,
        "expires_at": int(time.time()) + 3600,
    }


# =========================================================== ML simulado


class MLFalso:
    """Estado mínimo do ML: itens por id, log de chamadas, PUT de preço."""

    def __init__(self) -> None:
        self.itens: dict[str, dict] = {}
        self.chamadas: list[tuple[str, str, Any]] = []
        self.recusa_automacao: set[str] = set()
        self.pausa_falha: set[str] = set()

    def item(self, mlb: str, **campos) -> None:
        self.itens[mlb] = {
            "id": mlb,
            "status": "active",
            "price": 100,
            "catalog_listing": False,
            "item_relations": [],
            "listing_type_id": "gold_special",
            "variations": [],
            **campos,
        }

    async def request(self, method: str, path: str, *, params=None, json=None):
        self.chamadas.append((method, path, json))
        req = httpx.Request(method, f"https://ml.falso{path}")
        mlb = path.rsplit("/", 1)[-1]
        if method == "GET" and path.startswith("/items/"):
            if mlb not in self.itens:
                return httpx.Response(404, json={"message": "not_found"}, request=req)
            return httpx.Response(200, json=self.itens[mlb], request=req)
        if method == "PUT" and path.startswith("/items/"):
            body = json or {}
            if "status" in body:
                if body["status"] == "paused" and mlb in self.pausa_falha:
                    return httpx.Response(500, json={"message": "erro"}, request=req)
                self.itens[mlb]["status"] = body["status"]
                return httpx.Response(200, json=self.itens[mlb], request=req)
            if mlb in self.recusa_automacao:
                return httpx.Response(
                    400,
                    json={
                        "message": "Price automation is active for this item",
                        "error": "validation_error",
                        "cause": [{"code": "item.price.automation", "message": "x"}],
                    },
                    request=req,
                )
            if self.itens[mlb]["status"] == "paused":
                return httpx.Response(
                    400,
                    json={"message": "item.price.not_modifiable", "cause": []},
                    request=req,
                )
            self.itens[mlb]["price"] = body.get("price")
            return httpx.Response(200, json=self.itens[mlb], request=req)
        return httpx.Response(404, json={}, request=req)

    def puts_de_preco(self) -> list[tuple[str, Any]]:
        return [
            (p.rsplit("/", 1)[-1], j)
            for m, p, j in self.chamadas
            if m == "PUT" and j and "price" in j
        ]


@pytest.fixture
def ml_falso(monkeypatch) -> MLFalso:
    falso = MLFalso()

    async def _req(self, method, path, *, params=None, json=None):
        return await falso.request(method, path, params=params, json=json)

    async def _dorme(_s):
        return None

    monkeypatch.setattr(ml_mod.MercadoLivreClient, "_request", _req)
    monkeypatch.setattr(ml_mod.asyncio, "sleep", _dorme)
    return falso


def _cliente_ml() -> ml_mod.MercadoLivreClient:
    return ml_mod.MercadoLivreClient(_ml_creds())


# =========================================================== cenário no banco


@pytest_asyncio.fixture
async def dono(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:cat-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"cat-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_FULL,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _segmentos(db: AsyncSession) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ordem, slug in enumerate(("celular", "mala", "eletro", "catalogo")):
        raiz = Segment(name=slug.title(), slug=slug, sort_order=ordem)
        db.add(raiz)
        await db.flush()
        filhos = []
        for i in range(5):
            f = Segment(
                name=f"{slug}-{i + 1}", slug=f"{slug}-{i + 1}", sort_order=i, parent_id=raiz.id
            )
            db.add(f)
            filhos.append(f)
        await db.flush()
        out[slug] = raiz
        out[f"{slug}_filhos"] = filhos
    return out


async def _integracao_ml(db: AsyncSession, user: User, nome: str) -> Integration:
    company = Company(razao_social=f"ACME {nome}", apelido=f"acme-{uuid.uuid4().hex[:6]}")
    db.add(company)
    await db.flush()
    store = Store(company_id=company.id, marketplace=Marketplace.ML, status=StoreStatus.ACTIVE)
    db.add(store)
    await db.flush()
    integ = Integration(
        user_id=user.id,
        store_id=store.id,
        platform=IntegrationPlatform.ML,
        name=nome,
        credentials=encrypt_json(_ml_creds()),
    )
    db.add(integ)
    await db.flush()
    return integ


def _link(user: User, integ: Integration, produto: Product, mlb: str, **campos) -> ProductLink:
    return ProductLink(
        user_id=user.id,
        product_id=produto.id,
        integration_id=integ.id,
        store_id=integ.store_id,
        platform=IntegrationPlatform.ML,
        external_id=mlb,
        external_sku=produto.sku,
        last_sync_status=LinkSyncStatus.OK,
        **campos,
    )


@pytest_asyncio.fixture
async def cenario(db: AsyncSession, dono: User) -> dict[str, Any]:
    """counhago (ML, clássico) com:
      - a003 (celular, acessório, custo 40, preço de catálogo 55);
      - anúncio comum a003.sa (MLB100), kit a003.sa+a001 (MLB101),
        catálogo livre (MLB200), catálogo premium (MLB201, outro tipo),
        vínculo com marca não lida (MLB202, catalog_listing NULL);
      - dg052 (celular, sem preço de catálogo) com catálogo sincronizado
        com o comum (MLB210) e catálogo pausado (MLB211).
    """
    seg = await _segmentos(db)
    integ = await _integracao_ml(db, dono, "counhago")

    p_a003 = Product(user_id=dono.id, sku="a003.sa", name="Fone")
    p_kit = Product(user_id=dono.id, sku="a003.sa+a001", name="Fone kit")
    p_dg = Product(user_id=dono.id, sku="dg052.sp", name="Celular")
    db.add_all([p_a003, p_kit, p_dg])
    await db.flush()

    db.add_all([
        _link(dono, integ, p_a003, "MLB100", listing_type="gold_special", catalog_listing=False),
        _link(dono, integ, p_kit, "MLB101", listing_type="gold_special", catalog_listing=False),
        _link(dono, integ, p_a003, "MLB200", listing_type="gold_special", catalog_listing=True,
              catalog_product_id="MLB-CAT-1", anuncio_status="active"),
        _link(dono, integ, p_a003, "MLB201", listing_type="gold_pro", catalog_listing=True,
              anuncio_status="active"),
        _link(dono, integ, p_a003, "MLB202", listing_type="gold_special", catalog_listing=None),
        _link(dono, integ, p_dg, "MLB210", listing_type="gold_special", catalog_listing=True,
              catalogo_relacionado="MLB300", anuncio_status="active"),
        _link(dono, integ, p_dg, "MLB211", listing_type="gold_special", catalog_listing=True,
              anuncio_status="paused"),
    ])

    base = PricingAccount(
        user_id=dono.id,
        name="counhago",
        platform=PricingPlatform.ML,
        listing_type="ml classico",
        segment_id=seg["celular"].id,
        kit_number=1,
        commission=Decimal("0.10"),
        margin1=Decimal("0.20"),
        shipping1=Decimal("5.00"),
        margin3=Decimal("0.30"),
        shipping3=Decimal("10.00"),
        discount="5%",
        integration_id=integ.id,
        sort_order=3,
    )
    sem_tipo = PricingAccount(
        user_id=dono.id,
        name="eron",
        platform=PricingPlatform.ML,
        listing_type=None,
        segment_id=seg["celular"].id,
        commission=Decimal("0.12"),
        margin1=Decimal("0.20"),
        integration_id=integ.id,
    )
    shopee = PricingAccount(
        user_id=dono.id,
        name="loja shopee",
        platform=PricingPlatform.SHOPEE,
        segment_id=seg["celular"].id,
        commission=Decimal("0.14"),
        margin1=Decimal("0.20"),
    )
    a003 = PricingProduct(
        user_id=dono.id,
        sku="a003",
        name="Fone a003",
        segment_id=seg["celular_filhos"][0].id,  # acessório (slot 1)
        cost_kit1=Decimal("40"),
        preco_catalogo=Decimal("55"),
    )
    dg052 = PricingProduct(
        user_id=dono.id,
        sku="dg052",
        name="Celular dg052",
        segment_id=seg["celular_filhos"][2].id,  # regular (slot 3)
        cost_kit1=Decimal("500"),
        preco_catalogo=None,
    )
    db.add_all([base, sem_tipo, shopee, a003, dg052])
    await db.commit()
    for o in (base, sem_tipo, shopee, a003, dg052, integ):
        await db.refresh(o)
    return {
        "seg": seg, "integ": integ, "base": base, "sem_tipo": sem_tipo, "shopee": shopee,
        "a003": a003, "dg052": dg052,
    }


async def _ligar(client: AsyncClient, conta_id, ativo: bool = True):
    return await client.post(f"/api/pricing/accounts/{conta_id}/catalogo", json={"ativo": ativo})


# =========================================================== migration


_MIGRATION = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0378_catalogo_ml.py"


def _carregar_migration():
    spec = importlib.util.spec_from_file_location("migration_0378", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _estrutura(db: AsyncSession, schema: str) -> dict[str, list]:
    colunas = (
        await db.execute(
            text(
                """
                SELECT table_name, column_name, udt_name, is_nullable, column_default
                  FROM information_schema.columns
                 WHERE table_schema = :s
                   AND (table_name, column_name) IN (
                        ('pricing_products', 'preco_catalogo'),
                        ('pricing_accounts', 'canal'),
                        ('pricing_accounts', 'conta_base_id'),
                        ('product_links', 'catalog_listing'),
                        ('product_links', 'catalog_product_id'),
                        ('product_links', 'catalogo_relacionado'),
                        ('product_links', 'catalogo_lido_em'),
                        ('product_links', 'anuncio_status'))
                 ORDER BY table_name, column_name
                """
            ),
            {"s": schema},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                """
                SELECT co.conname, pg_get_constraintdef(co.oid)
                  FROM pg_constraint co
                  JOIN pg_class cl ON cl.oid = co.conrelid
                  JOIN pg_namespace n ON n.oid = cl.relnamespace
                 WHERE n.nspname = :s AND cl.relname = 'pricing_accounts'
                   AND co.conname IN ('ck_pricing_accounts_canal_valido',
                                      'ck_pricing_accounts_canal_conta_base',
                                      'fk_pricing_accounts_conta_base_id_pricing_accounts')
                 ORDER BY co.conname
                """
            ),
            {"s": schema},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                """
                SELECT indexname, indexdef FROM pg_indexes
                 WHERE schemaname = :s
                   AND indexname IN ('uq_pricing_accounts_catalogo_por_base',
                                     'ix_product_links_catalogo')
                 ORDER BY indexname
                """
            ),
            {"s": schema},
        )
    ).all()
    return {
        "colunas": [tuple(r) for r in colunas],
        "constraints": [(r[0], r[1].replace(f"{schema}.", "")) for r in constraints],
        "indices": [(r[0], r[1].replace(f"{schema}.", "")) for r in indices],
    }


@pytest.mark.asyncio
async def test_migration_0378_cria_o_que_o_model_declara_e_o_downgrade_desfaz(db: AsyncSession):
    mod = _carregar_migration()
    assert mod.revision == "0378_catalogo_ml"
    assert mod.down_revision == "0377_conferencia_shopee"
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0378"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que a 0378 toca (as colunas antigas que os CHECKs citam).
    await db.execute(text(f'CREATE TABLE "{rascunho}".pricing_products (id uuid PRIMARY KEY)'))
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".pricing_accounts '
            "(id uuid PRIMARY KEY, integration_id uuid)"
        )
    )
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".product_links '
            "(id uuid PRIMARY KEY, integration_id uuid NOT NULL)"
        )
    )
    # Uma conta de antes: vira canal='kit' e passa nos CHECKs novos.
    antiga = "00000000-0000-0000-0000-0000000000aa"
    await db.execute(
        text(f'INSERT INTO "{rascunho}".pricing_accounts (id) VALUES (:i)'), {"i": antiga}
    )
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()

        da_migration = await _estrutura(db, rascunho)
        do_model = await _estrutura(db, schema_model)
        assert len(da_migration["colunas"]) == 8
        assert da_migration == do_model

        assert (
            await db.execute(
                text(f'SELECT canal, conta_base_id FROM "{rascunho}".pricing_accounts')
            )
        ).one() == ("kit", None)

        # Filha válida entra; segunda filha da mesma base não; filha com
        # integração não; kit com base não.
        filha = "00000000-0000-0000-0000-0000000000bb"
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".pricing_accounts (id, canal, conta_base_id) '
                "VALUES (:f, 'catalogo', :b)"
            ),
            {"f": filha, "b": antiga},
        )
        await db.commit()
        for sql in (
            f"INSERT INTO \"{rascunho}\".pricing_accounts (id, canal, conta_base_id) "
            f"VALUES (gen_random_uuid(), 'catalogo', '{antiga}')",
            f"INSERT INTO \"{rascunho}\".pricing_accounts (id, canal, conta_base_id, "
            f"integration_id) VALUES (gen_random_uuid(), 'catalogo', '{filha}', "
            "gen_random_uuid())",
            f"INSERT INTO \"{rascunho}\".pricing_accounts (id, canal, conta_base_id) "
            f"VALUES (gen_random_uuid(), 'kit', '{antiga}')",
            f"INSERT INTO \"{rascunho}\".pricing_accounts (id, canal) "
            "VALUES (gen_random_uuid(), 'outro')",
        ):
            with pytest.raises(IntegrityError):
                await db.execute(text(sql))
            await db.rollback()
        # Apagar a base leva a filha.
        await db.execute(
            text(f'DELETE FROM "{rascunho}".pricing_accounts WHERE id = :i'), {"i": antiga}
        )
        await db.commit()
        assert (
            await db.execute(text(f'SELECT count(*) FROM "{rascunho}".pricing_accounts'))
        ).scalar_one() == 0

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert await _estrutura(db, rascunho) == {"colunas": [], "constraints": [], "indices": []}
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()


@pytest.mark.asyncio
async def test_migration_0378_com_product_links_ocupada_nao_trava_a_tabela_de_precos(
    db: AsyncSession, monkeypatch,
):
    """Cenário do deploy às 13h UTC: a varredura de vínculos segura uma
    transação aberta em product_links. A 0378 espera o lock_timeout e cai —
    sem ter travado pricing_products/pricing_accounts nesse meio tempo (a
    Tabela de Preços segue abrindo) e sem deixar nada aplicado."""
    import asyncio

    from app import db as app_db
    from sqlalchemy.exc import DBAPIError

    mod = _carregar_migration()
    rascunho = f"{Base.metadata.schema}_mig0378_trava"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    for tabela, colunas in (
        ("pricing_products", "id uuid PRIMARY KEY"),
        ("pricing_accounts", "id uuid PRIMARY KEY, integration_id uuid"),
        ("product_links", "id uuid PRIMARY KEY, integration_id uuid NOT NULL"),
    ):
        await db.execute(text(f'CREATE TABLE "{rascunho}".{tabela} ({colunas})'))
    await db.commit()
    monkeypatch.setattr(mod, "SCHEMA", rascunho)
    monkeypatch.setattr(mod, "LOCK_TIMEOUT", "2s")

    def _upgrade(conn) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            mod.upgrade()

    async def _migrar() -> None:
        async with app_db.engine.connect() as conn:
            async with conn.begin():
                await conn.run_sync(_upgrade)

    varredura = await app_db.engine.connect()
    try:
        # A "varredura": transação aberta lendo product_links (ACCESS SHARE).
        await varredura.begin()
        await varredura.execute(text(f'SELECT count(*) FROM "{rascunho}".product_links'))

        migracao = asyncio.create_task(_migrar())
        await asyncio.sleep(0.5)  # a migration já está na fila da trava
        assert not migracao.done()
        # Enquanto ela espera, a Tabela de Preços lê normalmente.
        async with app_db.engine.connect() as tela:
            await tela.execute(text("SET lock_timeout = '500ms'"))
            for tabela in ("pricing_products", "pricing_accounts"):
                await tela.execute(text(f'SELECT count(*) FROM "{rascunho}".{tabela}'))
            await tela.rollback()

        with pytest.raises(DBAPIError, match="lock timeout"):
            await migracao
        # Nada aplicado: o rollback é total.
        assert (await _estrutura(db, rascunho)) == {"colunas": [], "constraints": [], "indices": []}

        # Varredura terminou: rodar de novo aplica tudo.
        await varredura.rollback()
        await _migrar()
        assert len((await _estrutura(db, rascunho))["colunas"]) == 8
    finally:
        await varredura.close()
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()


# =========================================================== ligar/desligar


@pytest.mark.asyncio
async def test_ligar_e_desligar_catalogo_da_conta(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    auth_as(dono)
    base = cenario["base"]

    r = await _ligar(client, base.id)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["ativo"] is True
    filha = corpo["conta_catalogo"]
    assert filha["canal"] == "catalogo"
    assert filha["conta_base_id"] == str(base.id)
    assert filha["integration_id"] is None
    assert filha["name"] == "counhago"
    assert filha["listing_type"] == "ml classico"
    assert filha["department"] == "celular"
    assert filha["sort_order"] == 3
    # Números e anotações da base (a filha não guarda).
    assert Decimal(filha["commission"]) == Decimal("0.10")
    assert Decimal(filha["margin3"]) == Decimal("0.30")
    assert filha["discount"] == "5%"
    assert corpo["conta"]["conta_catalogo_id"] == filha["id"]
    linha = await db.get(PricingAccount, uuid.UUID(filha["id"]))
    assert linha.commission is None and linha.margin1 is None

    # Idempotente.
    r2 = await _ligar(client, base.id)
    assert r2.status_code == 200
    assert r2.json()["conta_catalogo"]["id"] == filha["id"]

    # /accounts devolve as duas, com canal e o vínculo.
    contas = (await client.get("/api/pricing/accounts")).json()
    por_id = {c["id"]: c for c in contas}
    assert por_id[filha["id"]]["canal"] == "catalogo"
    assert por_id[str(base.id)]["canal"] == "kit"
    assert por_id[str(base.id)]["conta_catalogo_id"] == filha["id"]

    # A filha não se edita direto.
    r = await client.patch(f"/api/pricing/accounts/{filha['id']}", json={"commission": "0.5"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "conta_catalogo_herda_da_base"
    r = await client.post(
        f"/api/pricing/accounts/{filha['id']}/department", json={"department": "mala"}
    )
    assert r.status_code == 409

    # Mudar a base arrasta tipo/nome/ordem da filha.
    r = await client.patch(
        f"/api/pricing/accounts/{base.id}",
        json={"listing_type": "ml premium", "name": "counhago 2", "sort_order": 7},
    )
    assert r.status_code == 200, r.text
    assert r.json()["conta_catalogo_id"] == filha["id"]
    await db.refresh(linha)
    assert (linha.listing_type, linha.name, linha.sort_order) == ("ml premium", "counhago 2", 7)

    # Preço fixado na filha vai junto quando desliga.
    db.add(
        PricingOverride(
            user_id=dono.id,
            pricing_product_id=cenario["a003"].id,
            pricing_account_id=linha.id,
            price_override=Decimal("99"),
        )
    )
    await db.commit()
    r = await _ligar(client, base.id, False)
    assert r.status_code == 200
    assert r.json() == {**r.json(), "ativo": False, "conta_catalogo": None}
    assert r.json()["conta"]["conta_catalogo_id"] is None
    assert (
        await db.execute(select(PricingAccount).where(PricingAccount.canal == "catalogo"))
    ).first() is None
    assert (await db.execute(select(PricingOverride))).first() is None
    # Desligar de novo: nada a fazer.
    assert (await _ligar(client, base.id, False)).status_code == 200


@pytest.mark.asyncio
async def test_ligar_catalogo_exige_tipo_e_conta_ml_de_kit(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    auth_as(dono)
    r = await _ligar(client, cenario["sem_tipo"].id)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "tipo_obrigatorio"

    r = await _ligar(client, cenario["shopee"].id)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "conta_nao_ml_kit"

    filha = (await _ligar(client, cenario["base"].id)).json()["conta_catalogo"]
    r = await _ligar(client, filha["id"])
    assert r.status_code == 400

    r = await _ligar(client, uuid.uuid4())
    assert r.status_code == 404

    # Sem permissão de editar contas → 403.
    leitor = User(
        open_id=f"email:l-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"l-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={"tabela_precos_contas": {"view": True}},
    )
    db.add(leitor)
    await db.commit()
    auth_as(leitor)
    assert (await _ligar(client, cenario["base"].id)).status_code == 403


@pytest.mark.asyncio
async def test_apagar_a_base_apaga_a_filha_e_base_fora_do_ml_perde_o_catalogo(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    auth_as(dono)
    base = cenario["base"]
    assert (await _ligar(client, base.id)).status_code == 200
    r = await client.patch(f"/api/pricing/accounts/{base.id}", json={"platform": "shopee"})
    assert r.status_code == 200
    assert r.json()["conta_catalogo_id"] is None
    assert (
        await db.execute(select(PricingAccount).where(PricingAccount.canal == "catalogo"))
    ).first() is None

    await client.patch(f"/api/pricing/accounts/{base.id}", json={"platform": "mercadolivre"})
    assert (await _ligar(client, base.id)).status_code == 200
    assert (await client.delete(f"/api/pricing/accounts/{base.id}")).status_code == 204
    assert (
        await db.execute(select(PricingAccount).where(PricingAccount.canal == "catalogo"))
    ).first() is None


@pytest.mark.asyncio
async def test_auto_match_nao_liga_integracao_na_coluna_de_catalogo(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    auth_as(dono)
    assert (await _ligar(client, cenario["base"].id)).status_code == 200
    r = await client.post("/api/pricing/accounts/auto-match")
    assert r.status_code == 200, r.text
    filha = (
        await db.execute(select(PricingAccount).where(PricingAccount.canal == "catalogo"))
    ).scalar_one()
    assert filha.integration_id is None


# =========================================================== cálculo


def _conta(**kw):
    base = {
        "canal": "kit", "kit_number": 1, "commission": Decimal("0.10"),
        **{f"margin{i}": None for i in range(1, 6)},
        **{f"shipping{i}": None for i in range(1, 6)},
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _produto(**kw):
    base = {f"cost_kit{i}": None for i in range(1, 9)}
    base.update({"cost_kit1": Decimal("40"), "preco_catalogo": None})
    base.update(kw)
    return SimpleNamespace(**base)


def test_calculo_da_filha_usa_preco_catalogo_e_os_numeros_da_base():
    base = _conta(commission=Decimal("0.10"), margin1=Decimal("0.20"), shipping1=Decimal("5"))
    # A filha não guarda números: se o cálculo lesse dela, daria sem margem.
    filha = _conta(canal="catalogo", commission=None, kit_number=1)
    prod = _produto(preco_catalogo=Decimal("55"), cost_kit1=Decimal("40"))

    out = calculate(filha, prod, None, 1, conta_base=base)
    # (55 × 1.2 + 5) / 0.9 = 78.89 → 79
    assert out.source == "computed"
    assert out.price == Decimal("79")
    assert out.inputs["canal"] == "catalogo"
    assert out.inputs["cost"] == "55"

    # A conta de kit continua no Kit 1: (40 × 1.2 + 5) / 0.9 = 58.89 → 59
    assert calculate(base, prod, None, 1).price == Decimal("59")


@pytest.mark.parametrize("preco", [None, Decimal("0")])
def test_filha_sem_preco_de_catalogo_nao_cai_no_kit1(preco):
    base = _conta(margin1=Decimal("0.20"))
    filha = _conta(canal="catalogo")
    out = calculate(filha, _produto(preco_catalogo=preco), None, 1, conta_base=base)
    assert out.price is None
    assert out.source == "missing_inputs"
    assert out.detail == "sem_preco_catalogo"


def test_filha_sem_base_e_override_na_filha():
    filha = _conta(canal="catalogo")
    assert calculate(filha, _produto(preco_catalogo=Decimal("55")), None, 1).detail == (
        "sem_conta_base"
    )
    ovr = SimpleNamespace(cell_status="manual", price_override=Decimal("88"))
    out = calculate(filha, _produto(), ovr, 1, conta_base=_conta(margin1=Decimal("0.2")))
    assert (out.source, out.price) == ("override", Decimal("88.00"))


# =========================================================== resolvedor


def _lk(mlb, pid, *, tipo="gold_special", cat=None, rel=None, status=None, morto=False):
    return SimpleNamespace(
        id=uuid.uuid4(), external_id=mlb, variation_id=None, product_id=pid,
        listing_type=tipo, catalog_listing=cat, catalogo_relacionado=rel,
        anuncio_status=status, morto_desde=datetime.now(UTC) if morto else None,
    )


def test_resolvedor_canal_kit_exclui_catalogo():
    a, k = uuid.uuid4(), uuid.uuid4()
    skus = {a: "a003.sa", k: "a003.sa+a001"}
    links = [
        _lk("COMUM", a, cat=False),
        _lk("NAO_LIDO", a, cat=None),
        _lk("CAT", a, cat=True),
    ]
    res = resolver_anuncios(links, skus, pricing_sku="a003", dept="celular", plataforma="ml",
                            canal="kit", listing_type_conta="ml classico")
    assert sorted(lk.external_id for lk in res.links) == ["COMUM", "NAO_LIDO"]
    assert res.bloqueados == []
    # Havendo kit, o celular manda só para o kit (regra de sempre).
    links.append(_lk("KIT", k, cat=False))
    res = resolver_anuncios(links, skus, pricing_sku="a003", dept="celular", plataforma="ml",
                            canal="kit", listing_type_conta="ml classico")
    assert [lk.external_id for lk in res.links] == ["KIT"]
    # Fora do ML a marca de catálogo não existe: nada muda.
    res = resolver_anuncios(links[:3], skus, pricing_sku="a003", dept="celular",
                            plataforma="shopee", canal="kit", listing_type_conta=None)
    assert len(res.links) == 3


def test_resolvedor_canal_catalogo_regras():
    a, k, a_ci = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    skus = {a: "a003.sa", k: "a003.sa+a001", a_ci: "a003.ci"}
    links = [
        _lk("COMUM", a, cat=False),
        _lk("NAO_LIDO", a, cat=None),
        _lk("KIT_CAT", k, cat=True),
        _lk("PREMIUM", a, cat=True, tipo="gold_pro"),
        _lk("CAT_SA", a, cat=True),
        _lk("CAT_CI", a_ci, cat=True),
        _lk("CAT_SA", a, cat=True),  # mesmo anúncio duas vezes (variações) → 1
    ]
    res = resolver_anuncios(links, skus, pricing_sku="a003", dept="celular", plataforma="ml",
                            canal="catalogo", listing_type_conta="ml classico")
    assert [lk.external_id for lk in res.links] == ["CAT_SA", "CAT_CI"]
    res = resolver_anuncios(links, skus, pricing_sku="a003", dept="celular", plataforma="ml",
                            canal="catalogo", listing_type_conta="ml premium")
    assert [lk.external_id for lk in res.links] == ["PREMIUM"]
    # Tipo obrigatório.
    for tipo in (None, "atlas"):
        res = resolver_anuncios(links, skus, pricing_sku="a003", dept="celular",
                                plataforma="ml", canal="catalogo", listing_type_conta=tipo)
        assert res.sem_tipo and not res.links


def test_resolvedor_catalogo_mala_exato_e_eletro_por_codigo_base():
    m12, m12sp, m14 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    skus = {m12: "b054.12", m12sp: "b054.12.sp", m14: "b054.14"}
    links = [_lk("M12", m12, cat=True), _lk("M12SP", m12sp, cat=True), _lk("M14", m14, cat=True)]
    res = resolver_anuncios(links, skus, pricing_sku="b054.12", dept="mala", plataforma="ml",
                            canal="catalogo", listing_type_conta="classico")
    assert [lk.external_id for lk in res.links] == ["M12"]

    e110, e2l = uuid.uuid4(), uuid.uuid4()
    skus = {e110: "uaf001m1.110", e2l: "uaf001m1.2l"}
    links = [_lk("E110", e110, cat=True), _lk("E2L", e2l, cat=True)]
    res = resolver_anuncios(links, skus, pricing_sku="uaf001m1.110,uaf001m1.220",
                            dept="eletro", plataforma="ml", canal="catalogo",
                            listing_type_conta="classico")
    # Eletro casa pelo código base (como a coluna de kit) — inclusive o 2L.
    assert sorted(lk.external_id for lk in res.links) == ["E110", "E2L"]


def test_resolvedor_catalogo_bloqueia_sincronizado_pausado_revisao_encerrado(monkeypatch):
    a = uuid.uuid4()
    skus = {a: "a003.sa"}
    links = [
        _lk("SINC", a, cat=True, rel="MLB9,MLB8"),
        _lk("PAUSA", a, cat=True, status="paused"),
        _lk("REV", a, cat=True, status="under_review"),
        _lk("FIM", a, cat=True, status="closed"),
        _lk("MORTO", a, cat=True, morto=True),
    ]
    kw = {"pricing_sku": "a003", "dept": "celular", "plataforma": "ml", "canal": "catalogo",
          "listing_type_conta": "classico"}
    res = resolver_anuncios(links, skus, **kw)
    assert res.links == []
    assert {b.link.external_id: b.motivo for b in res.bloqueados} == {
        "SINC": "sincronizado", "PAUSA": "pausado", "REV": "em_revisao",
        "FIM": "encerrado", "MORTO": "encerrado",
    }
    info = info_celula_catalogo(res, sem_preco=False)
    assert info["bloqueio"] == "sincronizado"
    assert [a["external_id"] for a in info["anuncios"]] == ["SINC", "PAUSA", "REV", "FIM", "MORTO"]
    assert info["anuncios"][0]["sincronizado_com"] == ["MLB9", "MLB8"]
    assert "MLB9" in info["texto"]

    # D2 desligado: o sincronizado passa a receber.
    monkeypatch.setattr(anuncios, "CATALOGO_SINCRONIZADO_BLOQUEIA", False)
    res = resolver_anuncios(links, skus, **kw)
    assert [lk.external_id for lk in res.links] == ["SINC"]
    info = info_celula_catalogo(res, sem_preco=False)
    assert info["bloqueio"] is None
    assert info["texto"].startswith("Envia para SINC; pula ")


def test_info_celula_ordem_dos_bloqueios():
    vazio = resolver_anuncios([], {}, pricing_sku="a003", dept="celular", plataforma="ml",
                              canal="catalogo", listing_type_conta=None)
    assert info_celula_catalogo(vazio, sem_preco=True)["bloqueio"] == "sem_tipo"
    vazio = resolver_anuncios([], {}, pricing_sku="a003", dept="celular", plataforma="ml",
                              canal="catalogo", listing_type_conta="classico")
    assert info_celula_catalogo(vazio, sem_preco=True)["bloqueio"] == "sem_anuncio"
    a = uuid.uuid4()
    livre = resolver_anuncios([_lk("CAT", a, cat=True)], {a: "a003.sa"}, pricing_sku="a003",
                              dept="celular", plataforma="ml", canal="catalogo",
                              listing_type_conta="classico")
    assert info_celula_catalogo(livre, sem_preco=True)["bloqueio"] == "sem_preco_catalogo"
    ok = info_celula_catalogo(livre, sem_preco=False)
    assert ok == {
        "anuncios": [{"external_id": "CAT", "status": None, "listing_type": "gold_special",
                      "sincronizado_com": [], "bloqueio": None}],
        "bloqueio": None,
        "texto": "Envia para CAT",
    }


# =========================================================== update_price


@pytest.mark.asyncio
async def test_update_price_confere_o_canal_no_item_vivo(ml_falso: MLFalso):
    cli = _cliente_ml()
    ml_falso.item("MLB1")  # comum
    ml_falso.item("MLB2", catalog_listing=True)
    ml_falso.item("MLB3", catalog_listing=True, item_relations=[{"id": "MLB1"}])
    ml_falso.item("MLB4", catalog_listing=True, status="paused")
    ml_falso.item("MLB5", catalog_listing=True, status="under_review")
    ml_falso.item("MLB6", catalog_listing=True, status="closed")

    casos = {
        ("MLB1", "catalogo"): "canal_errado",
        ("MLB2", "kit"): "canal_errado",
        ("MLB3", "catalogo"): "sincronizado",
        ("MLB4", "catalogo"): "pausado",
        ("MLB5", "catalogo"): "em_revisao",
        ("MLB6", "catalogo"): "encerrado",
    }
    for (mlb, canal), codigo in casos.items():
        r = await cli.update_price(mlb, 120.0, canal_esperado=canal)
        assert (r.status, r.error_code) == (SyncStatus.SKIPPED, codigo), (mlb, r)
    assert ml_falso.puts_de_preco() == []

    r = await cli.update_price("MLB2", 120.4, canal_esperado="catalogo")
    assert r.status == SyncStatus.OK
    r = await cli.update_price("MLB1", 99.0, canal_esperado="kit")
    assert r.status == SyncStatus.OK
    assert ml_falso.puts_de_preco() == [("MLB2", {"price": 120}), ("MLB1", {"price": 99})]
    # Sem canal: comportamento antigo (manda até para o de catálogo).
    assert (await cli.update_price("MLB3", 50.0)).status == SyncStatus.OK


@pytest.mark.asyncio
async def test_flag_d2_desligada_libera_o_sincronizado_no_item_vivo(
    ml_falso: MLFalso, monkeypatch,
):
    ml_falso.item("MLB3", catalog_listing=True, item_relations=[{"id": "MLB1"}])
    monkeypatch.setattr(anuncios, "CATALOGO_SINCRONIZADO_BLOQUEIA", False)
    r = await _cliente_ml().update_price("MLB3", 77.0, canal_esperado="catalogo")
    assert r.status == SyncStatus.OK
    assert ml_falso.puts_de_preco() == [("MLB3", {"price": 77})]


@pytest.mark.asyncio
async def test_update_price_confere_o_tipo_no_item_vivo(ml_falso: MLFalso):
    """O vínculo diz clássico (varredura das 13h UTC), mas o vendedor passou o
    anúncio para premium depois: o preço da coluna clássica não vai."""
    cli = _cliente_ml()
    ml_falso.item("MLB1", catalog_listing=True, listing_type_id="gold_pro")
    ml_falso.item("MLB2", listing_type_id="gold_pro")
    ml_falso.item("MLB3", listing_type_id="gold_premium")  # apelido antigo do gold_pro
    ml_falso.item("MLB4")
    del ml_falso.itens["MLB4"]["listing_type_id"]  # item sem o campo: não confere

    for mlb, canal in (("MLB1", "catalogo"), ("MLB2", "kit")):
        r = await cli.update_price(mlb, 120.0, canal_esperado=canal, tipo_esperado="gold_special")
        assert (r.status, r.error_code) == (SyncStatus.SKIPPED, "tipo_errado"), (mlb, r)
        assert "premium no ML e a coluna é clássico" in r.error_detail
    assert ml_falso.puts_de_preco() == []

    r = await cli.update_price("MLB1", 120.0, canal_esperado="catalogo", tipo_esperado="gold_pro")
    assert r.status == SyncStatus.OK
    r = await cli.update_price("MLB3", 90.0, canal_esperado="kit", tipo_esperado="gold_pro")
    assert r.status == SyncStatus.OK
    r = await cli.update_price("MLB4", 80.0, canal_esperado="kit", tipo_esperado="gold_special")
    assert r.status == SyncStatus.OK
    # Conta sem tipo (None): não confere — manda como antes.
    r = await cli.update_price("MLB2", 70.0, canal_esperado="kit")
    assert r.status == SyncStatus.OK
    assert [m for m, _ in ml_falso.puts_de_preco()] == ["MLB1", "MLB3", "MLB4", "MLB2"]


@pytest.mark.asyncio
async def test_envio_pula_anuncio_que_mudou_de_tipo_depois_da_varredura(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
    ml_falso: MLFalso,
):
    auth_as(dono)
    base = cenario["base"]  # ml classico
    filha_id = (await _ligar(client, base.id)).json()["conta_catalogo"]["id"]
    # MLB200 gravado como gold_special (clássico), mas o vivo já é premium.
    ml_falso.item("MLB200", catalog_listing=True, listing_type_id="gold_pro")
    out = await _push(client, filha_id, cenario["a003"].id)
    assert (out["ok"], out["code"]) == (False, "all_skipped"), out
    assert "MLB200 (tipo_errado)" in out["detail"]
    assert ml_falso.puts_de_preco() == []

    # Coluna de kit da conta tipada: o mesmo (o kit MLB101 virou premium).
    ml_falso.item("MLB101", listing_type_id="gold_pro")
    out = await _push(client, base.id, cenario["a003"].id)
    assert (out["ok"], out["code"]) == (False, "all_skipped"), out
    assert "MLB101 (tipo_errado)" in out["detail"]
    assert ml_falso.puts_de_preco() == []

    # Conta sem tipo (eron, mesma integração): não confere o tipo.
    out = await _push(client, cenario["sem_tipo"].id, cenario["a003"].id)
    assert out["ok"] is True, out
    assert [m for m, _ in ml_falso.puts_de_preco()] == ["MLB101"]


@pytest.mark.asyncio
async def test_update_price_automacao_do_ml_vira_erro_claro(ml_falso: MLFalso):
    ml_falso.item("MLB1", status="paused")
    ml_falso.recusa_automacao.add("MLB1")
    r = await _cliente_ml().update_price("MLB1", 120.0, canal_esperado="kit")
    assert r.status == SyncStatus.FATAL
    assert r.error_code == "automacao_ml"
    assert "automação de preços" in r.error_detail
    # Não reativou o anúncio pausado.
    assert all(j != {"status": "active"} for _m, _p, j in ml_falso.chamadas)


# Corpo exato da documentação do ML (automatizações de preços, 18/03/2026):
# o código é o mesmo do pausado (item.price.not_modifiable); só o texto muda.
_RECUSA_DOC_ML = {
    "message": "Cannot modify price on items with dynamic pricing",
    "error": "item.price.not_modifiable",
    "status": 400,
    "cause": [],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("status_item", ["active", "paused"])
async def test_update_price_automacao_com_o_corpo_da_documentacao(
    ml_falso: MLFalso, monkeypatch, status_item: str,
):
    ml_falso.item("MLB1", status=status_item)

    async def _req(self, method, path, *, params=None, json=None):
        if method == "PUT" and json and "price" in json:
            ml_falso.chamadas.append((method, path, json))
            return httpx.Response(
                400, json=_RECUSA_DOC_ML, request=httpx.Request(method, "https://ml.falso"),
            )
        return await ml_falso.request(method, path, params=params, json=json)

    monkeypatch.setattr(ml_mod.MercadoLivreClient, "_request", _req)
    r = await _cliente_ml().update_price("MLB1", 120.0, canal_esperado="kit")
    assert r.status == SyncStatus.FATAL
    assert r.error_code == "automacao_ml"
    # Pausado com automação: não reativa nem pausa de novo.
    assert all(not (j or {}).get("status") for _m, _p, j in ml_falso.chamadas)


@pytest.mark.asyncio
async def test_update_price_200_com_warning_de_automacao_e_erro(
    ml_falso: MLFalso, monkeypatch,
):
    """PUT com variações volta 200, mas o ML ignora o preço e avisa."""
    ml_falso.item("MLB1", variations=[{"id": 1}, {"id": 2}])

    async def _req(self, method, path, *, params=None, json=None):
        if method == "PUT" and json and "variations" in json:
            ml_falso.chamadas.append((method, path, json))
            return httpx.Response(
                200,
                json={
                    "id": "MLB1",
                    "warnings": [{
                        "department": "items",
                        "cause_id": 502,
                        "code": "item.price.not_modifiable",
                        "message": "Cannot modify price on items with dynamic pricing",
                        "references": ["item.price"],
                    }],
                },
                request=httpx.Request(method, "https://ml.falso"),
            )
        return await ml_falso.request(method, path, params=params, json=json)

    monkeypatch.setattr(ml_mod.MercadoLivreClient, "_request", _req)
    r = await _cliente_ml().update_price("MLB1", 120.0, canal_esperado="kit")
    assert r.error_code == "automacao_ml"

    # Warning comum (sem automação) continua OK.
    async def _req_ok(self, method, path, *, params=None, json=None):
        if method == "PUT":
            return httpx.Response(
                200,
                json={"id": "MLB1", "warnings": [{"code": "item.title.automatically_fixed",
                                                  "message": "Title adjusted"}]},
                request=httpx.Request(method, "https://ml.falso"),
            )
        return await ml_falso.request(method, path, params=params, json=json)

    monkeypatch.setattr(ml_mod.MercadoLivreClient, "_request", _req_ok)
    r = await _cliente_ml().update_price("MLB1", 120.0, canal_esperado="kit")
    assert r.status == SyncStatus.OK


@pytest.mark.asyncio
async def test_kit_pausado_reativa_troca_e_pausa_de_novo_conferindo(ml_falso: MLFalso):
    cli = _cliente_ml()
    ml_falso.item("MLB1", status="paused")
    r = await cli.update_price("MLB1", 120.0, canal_esperado="kit")
    assert r.status == SyncStatus.OK
    assert r.payload["via"] == "unpause_repause"
    assert r.payload["repausa_falhou"] is False
    assert ml_falso.itens["MLB1"]["status"] == "paused"
    assert ml_falso.itens["MLB1"]["price"] == 120

    # A pausa não voltou: o resultado avisa (e o log de erro registra).
    ml_falso.item("MLB2", status="paused")
    ml_falso.pausa_falha.add("MLB2")
    r = await cli.update_price("MLB2", 130.0, canal_esperado="kit")
    assert r.status == SyncStatus.OK
    assert r.payload["repausa_falhou"] is True
    assert ml_falso.itens["MLB2"]["status"] == "active"


@pytest.mark.asyncio
async def test_not_modifiable_em_anuncio_que_nao_estava_pausado_nao_reativa(
    ml_falso: MLFalso, monkeypatch,
):
    ml_falso.item("MLB1", status="inactive")

    async def _req(self, method, path, *, params=None, json=None):
        if method == "PUT" and json and "price" in json:
            ml_falso.chamadas.append((method, path, json))
            return httpx.Response(
                400, json={"message": "item.price.not_modifiable", "cause": []},
                request=httpx.Request(method, "https://ml.falso"),
            )
        return await ml_falso.request(method, path, params=params, json=json)

    monkeypatch.setattr(ml_mod.MercadoLivreClient, "_request", _req)
    r = await _cliente_ml().update_price("MLB1", 120.0, canal_esperado="kit")
    assert r.status == SyncStatus.FATAL
    assert all(not (j or {}).get("status") for _m, _p, j in ml_falso.chamadas)


# =========================================================== /grid


@pytest.mark.asyncio
async def test_grid_devolve_catalogo_por_celula(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    auth_as(dono)
    base = cenario["base"]
    filha_id = (await _ligar(client, base.id)).json()["conta_catalogo"]["id"]

    r = await client.get("/api/pricing/grid", params={"department": "celular"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    contas = {a["id"]: a for a in corpo["accounts"]}
    assert contas[filha_id]["canal"] == "catalogo"
    assert contas[filha_id]["conta_base_id"] == str(base.id)
    assert Decimal(contas[filha_id]["commission"]) == Decimal("0.10")
    celulas = {(c["pricing_account_id"], c["pricing_product_id"]): c for c in corpo["cells"]}

    a003 = str(cenario["a003"].id)
    dg052 = str(cenario["dg052"].id)
    # Kit: sem campo catalogo; Kit 1 = 40 → (40 × 1.2 + 5) / 0.9 = 59.
    kit = celulas[(str(base.id), a003)]
    assert kit["catalogo"] is None
    assert Decimal(kit["price"]) == Decimal("59")
    # Catálogo do a003: preço sobre 55 com os números da base; vai pro MLB200
    # (clássico, livre). O premium (MLB201) e o não lido (MLB202) não entram.
    cat = celulas[(filha_id, a003)]
    assert Decimal(cat["price"]) == Decimal("79")
    assert cat["catalogo"]["bloqueio"] is None
    assert [a["external_id"] for a in cat["catalogo"]["anuncios"]] == ["MLB200"]
    assert cat["catalogo"]["texto"] == "Envia para MLB200"
    # dg052: sem preço de catálogo e anúncios todos bloqueados → o bloqueio
    # mostrado é o do anúncio (sincronizado), o preço fica vazio.
    cat_dg = celulas[(filha_id, dg052)]
    assert cat_dg["price"] is None
    assert cat_dg["source"] == "missing_inputs"
    assert cat_dg["catalogo"]["bloqueio"] == "sincronizado"
    motivos = {a["external_id"]: a["bloqueio"] for a in cat_dg["catalogo"]["anuncios"]}
    assert motivos == {"MLB210": "sincronizado", "MLB211": "pausado"}
    assert cat_dg["catalogo"]["anuncios"][0]["sincronizado_com"] == ["MLB300"]


@pytest.mark.asyncio
async def test_grid_sem_preco_e_sem_anuncio(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    auth_as(dono)
    base = cenario["base"]
    filha_id = (await _ligar(client, base.id)).json()["conta_catalogo"]["id"]
    # a003 sem preço de catálogo, com anúncio livre → sem_preco_catalogo.
    cenario["a003"].preco_catalogo = None
    db.add(
        PricingProduct(
            user_id=dono.id, sku="z999", name="Sem anúncio",
            segment_id=cenario["seg"]["celular_filhos"][0].id,
            cost_kit1=Decimal("10"), preco_catalogo=Decimal("12"),
        )
    )
    await db.commit()
    corpo = (await client.get("/api/pricing/grid", params={"department": "celular"})).json()
    nomes = {p["id"]: p["sku"] for p in corpo["products"]}
    por_sku = {
        nomes[c["pricing_product_id"]]: c
        for c in corpo["cells"]
        if c["pricing_account_id"] == filha_id
    }
    assert por_sku["a003"]["catalogo"]["bloqueio"] == "sem_preco_catalogo"
    assert por_sku["a003"]["price"] is None
    assert por_sku["z999"]["catalogo"]["bloqueio"] == "sem_anuncio"
    assert por_sku["z999"]["price"] is not None
    # Produtos devolvem o preco_catalogo.
    assert {p["sku"]: p["preco_catalogo"] for p in corpo["products"]}["z999"] == "12.00"


@pytest.mark.asyncio
async def test_filha_some_quando_a_base_some_do_grid(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    auth_as(dono)
    base = cenario["base"]
    filha_id = (await _ligar(client, base.id)).json()["conta_catalogo"]["id"]
    loja = StoreInfo(user_id=dono.id, platform="ml", account_name="counhago",
                     archived_at=datetime.now(UTC))
    db.add(loja)
    await db.flush()
    base.store_info_id = loja.id
    db.add(base)
    await db.commit()
    for url in ("/api/pricing/grid", "/api/pricing/accounts"):
        corpo = (await client.get(url)).json()
        ids = {a["id"] for a in (corpo["accounts"] if url.endswith("grid") else corpo)}
        assert str(base.id) not in ids
        assert filha_id not in ids


# =========================================================== envio (push)


async def _push(client: AsyncClient, conta_id, produto_id):
    r = await client.post(
        "/api/pricing/push",
        json={"items": [{"pricing_account_id": str(conta_id),
                         "pricing_product_id": str(produto_id)}]},
    )
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


@pytest.mark.asyncio
async def test_envio_catalogo_vai_so_para_o_anuncio_de_catalogo(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
    ml_falso: MLFalso,
):
    auth_as(dono)
    base = cenario["base"]
    filha_id = (await _ligar(client, base.id)).json()["conta_catalogo"]["id"]
    for mlb in ("MLB100", "MLB101", "MLB202"):
        ml_falso.item(mlb)
    ml_falso.item("MLB200", catalog_listing=True)
    ml_falso.item("MLB201", catalog_listing=True, listing_type_id="gold_pro")

    out = await _push(client, filha_id, cenario["a003"].id)
    assert out["ok"] is True, out
    assert out["code"] == "ok"
    assert Decimal(out["price"]) == Decimal("79")
    assert out["item_id"] == "MLB200"
    assert ml_falso.puts_de_preco() == [("MLB200", {"price": 79})]

    # A coluna de kit do a003 segue a regra de sempre (no celular, havendo
    # kit, só o kit) e nunca vai para o anúncio de catálogo.
    ml_falso.chamadas.clear()
    out = await _push(client, base.id, cenario["a003"].id)
    assert out["ok"] is True, out
    assert [m for m, _ in ml_falso.puts_de_preco()] == ["MLB101"]

    # Sem o kit: comum + o vínculo de marca não lida (MLB202 — é o ML vivo
    # que diz que ele é comum); o de catálogo (MLB200) continua de fora.
    await db.execute(
        ProductLink.__table__.delete().where(ProductLink.external_id == "MLB101")
    )
    await db.commit()
    ml_falso.chamadas.clear()
    out = await _push(client, base.id, cenario["a003"].id)
    assert out["ok"] is True, out
    assert sorted(m for m, _ in ml_falso.puts_de_preco()) == ["MLB100", "MLB202"]


@pytest.mark.asyncio
async def test_envio_kit_pula_anuncio_que_virou_catalogo_no_ml(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
    ml_falso: MLFalso,
):
    """Marca gravada como comum (ou não lida), mas o item VIVO é de catálogo:
    na conta com o catálogo ligado (D3), a conferência antes do PUT pula
    (canal_errado) e o preço não vai."""
    auth_as(dono)
    await _ligar(client, cenario["base"].id)
    ml_falso.item("MLB101", catalog_listing=True)
    out = await _push(client, cenario["base"].id, cenario["a003"].id)
    assert (out["ok"], out["code"]) == (False, "all_skipped")
    # O detalhe diz qual anúncio pulou e por quê (não "encerrado/moderação").
    assert "MLB101 (canal_errado)" in out["detail"], out
    assert ml_falso.puts_de_preco() == []

    await db.execute(
        ProductLink.__table__.delete().where(ProductLink.external_id == "MLB101")
    )
    await db.commit()
    ml_falso.item("MLB100")
    ml_falso.item("MLB202", catalog_listing=True)
    from app.services.pricing.push import push_one

    out = await push_one(db, user=dono, account_id=cenario["base"].id,
                         product_id=cenario["a003"].id)
    assert (out.ok, out.code) == (True, "partial")
    assert [m for m, _ in ml_falso.puts_de_preco()] == ["MLB100"]
    pulado = next(e for e in out.payload["links"] if e["externalId"] == "MLB202")
    assert (pulado["skipped"], pulado["error_code"]) == (True, "canal_errado")


@pytest.mark.asyncio
async def test_envio_de_celula_bloqueada_nao_chama_o_ml(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
    ml_falso: MLFalso,
):
    auth_as(dono)
    filha_id = (await _ligar(client, cenario["base"].id)).json()["conta_catalogo"]["id"]

    # dg052: sem preço de catálogo → bloqueado antes de tudo.
    out = await _push(client, filha_id, cenario["dg052"].id)
    assert (out["ok"], out["code"]) == (False, "bloqueado")

    # Com preço, mas os dois anúncios bloqueados (sincronizado + pausado).
    cenario["dg052"].preco_catalogo = Decimal("400")
    db.add(cenario["dg052"])
    await db.commit()
    out = await _push(client, filha_id, cenario["dg052"].id)
    assert (out["ok"], out["code"]) == (False, "bloqueado")
    assert "MLB210" in out["detail"] and "MLB300" in out["detail"]
    assert ml_falso.chamadas == []

    # Conta sem tipo: a filha não nasce (409), então nada a enviar por ela.
    # Célula sem anúncio de catálogo: bloqueado, sem status de erro gravado.
    z = PricingProduct(user_id=dono.id, sku="z999", name="x",
                       segment_id=cenario["seg"]["celular_filhos"][0].id,
                       cost_kit1=Decimal("10"), preco_catalogo=Decimal("12"))
    db.add(z)
    await db.commit()
    out = await _push(client, filha_id, z.id)
    assert (out["ok"], out["code"]) == (False, "bloqueado")
    assert (await db.execute(select(PricingOverride))).first() is None
    assert ml_falso.chamadas == []


@pytest.mark.asyncio
async def test_envio_parcial_pula_o_bloqueado_e_avisa(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
    ml_falso: MLFalso,
):
    auth_as(dono)
    filha_id = (await _ligar(client, cenario["base"].id)).json()["conta_catalogo"]["id"]
    # Um segundo anúncio de catálogo do a003, pausado: o MLB200 recebe, ele não.
    p = (await db.execute(select(Product).where(Product.sku == "a003.sa"))).scalar_one()
    db.add(_link(dono, cenario["integ"], p, "MLB220", listing_type="gold_special",
                 catalog_listing=True, anuncio_status="paused"))
    await db.commit()
    ml_falso.item("MLB200", catalog_listing=True)
    from app.services.pricing.push import push_one

    out = await push_one(db, user=dono, account_id=uuid.UUID(filha_id),
                         product_id=cenario["a003"].id)
    assert out.ok and out.code == "ok"
    assert "MLB220 (pausado)" in out.detail
    entradas = {e["externalId"]: e for e in out.payload["links"]}
    assert entradas["MLB200"]["success"] is True
    assert entradas["MLB220"]["skipped"] is True
    assert entradas["MLB220"]["error_code"] == "pausado"
    assert [m for m, _ in ml_falso.puts_de_preco()] == ["MLB200"]


# =========================================================== mesmo anúncio em duas linhas


def test_dois_precos_no_mesmo_anuncio_tiram_ele_das_duas_linhas():
    """Eletro casa pelo código base: as linhas 110/220 e 2L do uaf001m1 caem no
    mesmo anúncio de catálogo — nenhuma das duas manda (senão vale o último)."""
    e110, e2l, outro = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    skus = {e110: "uaf001m1.110", e2l: "uaf001m1.2l", outro: "uaf001m1.220"}
    links = [_lk("CAT110", e110, cat=True), _lk("CAT220", outro, cat=True)]
    kw = {"dept": "eletro", "plataforma": "ml", "canal": "catalogo",
          "listing_type_conta": "classico"}
    linha_110 = resolver_anuncios(links, skus, pricing_sku="uaf001m1.110,uaf001m1.220", **kw)
    linha_2l = resolver_anuncios(links[:1], skus, pricing_sku="uaf001m1.2l", **kw)
    assert [lk.external_id for lk in linha_2l.links] == ["CAT110"]

    anuncios.separar_anuncios_disputados(
        {"A": linha_110, "B": linha_2l},
        {"A": "uaf001m1.110,uaf001m1.220", "B": "uaf001m1.2l"},
    )
    # A linha 110/220 continua mandando para o anúncio que é só dela.
    assert [lk.external_id for lk in linha_110.links] == ["CAT220"]
    assert [(b.link.external_id, b.motivo, b.outras_linhas) for b in linha_110.bloqueados] == [
        ("CAT110", "mesmo_anuncio", ["uaf001m1.2l"])
    ]
    assert linha_2l.links == []
    info = info_celula_catalogo(linha_2l, sem_preco=False)
    assert info["bloqueio"] == "mesmo_anuncio"
    assert "CAT110 também casa com a linha uaf001m1.110,uaf001m1.220" in info["texto"]
    info = info_celula_catalogo(linha_110, sem_preco=False)
    assert info["bloqueio"] is None
    assert info["texto"].startswith("Envia para CAT220; pula CAT110 também casa")
    # Sem disputa (uma linha só), nada muda.
    sozinha = resolver_anuncios(links[:1], skus, pricing_sku="uaf001m1.2l", **kw)
    anuncios.separar_anuncios_disputados({"B": sozinha}, {"B": "uaf001m1.2l"})
    assert [lk.external_id for lk in sozinha.links] == ["CAT110"]


def test_celula_manda_preco():
    com_preco = SimpleNamespace(price=Decimal("10"), source="computed")
    assert anuncios.celula_manda_preco(com_preco, None)
    assert not anuncios.celula_manda_preco(SimpleNamespace(price=None, source="missing_inputs"))
    na = SimpleNamespace(cell_status=SimpleNamespace(value="NA"))
    assert not anuncios.celula_manda_preco(com_preco, na)
    assert anuncios.celula_manda_preco(com_preco, SimpleNamespace(cell_status="manual"))


@pytest_asyncio.fixture
async def eletro(db: AsyncSession, dono: User, cenario) -> dict[str, Any]:
    """Eletro na mesma integração: linhas uaf001m1.110,uaf001m1.220 (custo 240,
    catálogo 260) e uaf001m1.2l (60 / 70); anúncios de catálogo MLB400 (110V)
    e MLB401 (220V). Pelo código base, as DUAS linhas casam com os dois."""
    seg = cenario["seg"]
    p110 = Product(user_id=dono.id, sku="uaf001m1.110", name="Air fryer 110")
    p220 = Product(user_id=dono.id, sku="uaf001m1.220", name="Air fryer 220")
    db.add_all([p110, p220])
    await db.flush()
    db.add_all([
        _link(dono, cenario["integ"], p110, "MLB400", listing_type="gold_special",
              catalog_listing=True, anuncio_status="active"),
        _link(dono, cenario["integ"], p220, "MLB401", listing_type="gold_special",
              catalog_listing=True, anuncio_status="active"),
    ])
    conta = PricingAccount(
        user_id=dono.id, name="zorvex", platform=PricingPlatform.ML,
        listing_type="ml classico", segment_id=seg["eletro"].id, kit_number=1,
        commission=Decimal("0.10"), margin1=Decimal("0.20"), shipping1=Decimal("5.00"),
        integration_id=cenario["integ"].id,
    )
    l110 = PricingProduct(
        user_id=dono.id, sku="uaf001m1.110,uaf001m1.220", name="Air fryer",
        segment_id=seg["eletro_filhos"][0].id, cost_kit1=Decimal("240"),
        preco_catalogo=Decimal("260"),
    )
    l2l = PricingProduct(
        user_id=dono.id, sku="uaf001m1.2l", name="Air fryer 2L",
        segment_id=seg["eletro_filhos"][0].id, cost_kit1=Decimal("60"),
        preco_catalogo=Decimal("70"),
    )
    db.add_all([conta, l110, l2l])
    await db.commit()
    for o in (conta, l110, l2l):
        await db.refresh(o)
    return {"conta": conta, "l110": l110, "l2l": l2l}


async def _celulas_eletro(client: AsyncClient, filha_id: str) -> dict[str, dict]:
    corpo = (await client.get("/api/pricing/grid", params={"department": "eletro"})).json()
    nomes = {p["id"]: p["sku"] for p in corpo["products"]}
    return {
        nomes[c["pricing_product_id"]]: c
        for c in corpo["cells"]
        if c["pricing_account_id"] == filha_id
    }


@pytest.mark.asyncio
async def test_grid_e_envio_travam_o_anuncio_disputado_por_duas_linhas(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, eletro, auth_as: Callable,
    ml_falso: MLFalso,
):
    auth_as(dono)
    filha_id = (await _ligar(client, eletro["conta"].id)).json()["conta_catalogo"]["id"]
    ml_falso.item("MLB400", catalog_listing=True)
    ml_falso.item("MLB401", catalog_listing=True)

    cel = await _celulas_eletro(client, filha_id)
    c110, c2l = cel["uaf001m1.110,uaf001m1.220"], cel["uaf001m1.2l"]
    # As duas linhas mandam preço e casam com os mesmos anúncios → as duas
    # células travadas, cada uma citando a outra linha.
    for cel_, outra in ((c2l, "uaf001m1.110,uaf001m1.220"), (c110, "uaf001m1.2l")):
        assert cel_["price"] is not None
        assert cel_["catalogo"]["bloqueio"] == "mesmo_anuncio", cel_
        assert f"MLB400 também casa com a linha {outra}" in cel_["catalogo"]["texto"]
        assert {a["external_id"]: a["bloqueio"] for a in cel_["catalogo"]["anuncios"]} == {
            "MLB400": "mesmo_anuncio", "MLB401": "mesmo_anuncio",
        }

    # O servidor trava sozinho (sem depender da tela): nenhuma das duas chama o ML.
    for linha, outra in ((eletro["l2l"], "uaf001m1.110,uaf001m1.220"),
                         (eletro["l110"], "uaf001m1.2l")):
        out = await _push(client, filha_id, linha.id)
        assert (out["ok"], out["code"]) == (False, "bloqueado"), out
        assert outra in out["detail"]
    assert ml_falso.chamadas == []

    # A 2L sem preço de catálogo não disputa mais: a 110/220 volta a mandar
    # para o MLB400.
    eletro["l2l"].preco_catalogo = None
    db.add(eletro["l2l"])
    await db.commit()
    cel = await _celulas_eletro(client, filha_id)
    assert cel["uaf001m1.2l"]["catalogo"]["bloqueio"] == "sem_preco_catalogo"
    assert {a["external_id"]: a["bloqueio"]
            for a in cel["uaf001m1.110,uaf001m1.220"]["catalogo"]["anuncios"]} == {
        "MLB400": None, "MLB401": None,
    }
    ml_falso.chamadas.clear()
    out = await _push(client, filha_id, eletro["l110"].id)
    assert out["ok"] is True, out
    assert sorted(m for m, _ in ml_falso.puts_de_preco()) == ["MLB400", "MLB401"]


@pytest.mark.asyncio
async def test_celula_na_nao_disputa_o_anuncio(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, eletro, auth_as: Callable,
    ml_falso: MLFalso,
):
    from app.models import CellStatus

    auth_as(dono)
    filha_id = (await _ligar(client, eletro["conta"].id)).json()["conta_catalogo"]["id"]
    db.add(PricingOverride(
        user_id=dono.id, pricing_account_id=uuid.UUID(filha_id),
        pricing_product_id=eletro["l2l"].id, cell_status=CellStatus.NA,
    ))
    await db.commit()
    ml_falso.item("MLB400", catalog_listing=True)
    ml_falso.item("MLB401", catalog_listing=True)
    cel = await _celulas_eletro(client, filha_id)
    assert cel["uaf001m1.110,uaf001m1.220"]["catalogo"]["bloqueio"] is None
    assert {a["bloqueio"] for a in cel["uaf001m1.110,uaf001m1.220"]["catalogo"]["anuncios"]} == {None}
    out = await _push(client, filha_id, eletro["l110"].id)
    assert out["ok"] is True, out
    assert sorted(m for m, _ in ml_falso.puts_de_preco()) == ["MLB400", "MLB401"]


# =========================================================== /actual-prices


@pytest.mark.asyncio
async def test_actual_prices_usa_o_mesmo_resolvedor(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
    ml_falso: MLFalso,
):
    auth_as(dono)
    base = cenario["base"]
    filha_id = (await _ligar(client, base.id)).json()["conta_catalogo"]["id"]
    ml_falso.item("MLB101", price=150)
    ml_falso.item("MLB200", catalog_listing=True, price=99)
    r = await client.get(
        f"/api/pricing/actual-prices/{cenario['a003'].id}", params={"department": "celular"}
    )
    assert r.status_code == 200, r.text
    precos = r.json()
    assert precos[filha_id] == 99.0
    assert precos[str(base.id)] == 150.0
    assert precos[str(cenario["shopee"].id)] is None


# =========================================================== Catálogo antigo


@pytest.mark.asyncio
async def test_rotas_do_catalogo_antigo_sairam(
    client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    auth_as(dono)
    pid = cenario["a003"].id
    assert (await client.post(f"/api/pricing/products/{pid}/catalog")).status_code in (404, 405)
    assert (await client.get("/api/pricing/catalog-listings")).status_code in (404, 405)
    r = await client.post("/api/pricing/push-catalog", json={"items": []})
    assert r.status_code in (404, 405)
    # O filtro in_catalog não existe mais (ignorado) e o campo saiu da saída.
    r = await client.get("/api/pricing/products", params={"in_catalog": "true"})
    assert r.status_code == 200
    assert {p["sku"] for p in r.json()} == {"a003", "dg052"}
    assert all("in_catalog" not in p for p in r.json())


@pytest.mark.asyncio
async def test_produto_grava_preco_catalogo(
    client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    auth_as(dono)
    pid = cenario["dg052"].id
    r = await client.patch(f"/api/pricing/products/{pid}", json={"preco_catalogo": "321.50"})
    assert r.status_code == 200, r.text
    assert r.json()["preco_catalogo"] == "321.50"
    r = await client.patch(f"/api/pricing/products/{pid}", json={"preco_catalogo": None})
    assert r.json()["preco_catalogo"] is None
    r = await client.post(
        "/api/pricing/products",
        json={"sku": "novo1", "name": "Novo", "department": "celular", "product_type": 2,
              "preco_catalogo": "10"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["preco_catalogo"] == "10.00"


@pytest.mark.asyncio
async def test_importacao_sem_a_coluna_catalogo_nao_apaga_o_preco(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    auth_as(dono)
    item = {"sku": "a003", "name": "Fone a003", "department": "celular", "product_type": 1,
            "cost_kit1": "41"}
    r = await client.post("/api/pricing/products/import", json={"items": [item]})
    assert r.status_code == 200, r.text
    await db.refresh(cenario["a003"])
    assert cenario["a003"].cost_kit1 == Decimal("41")
    assert cenario["a003"].preco_catalogo == Decimal("55")
    r = await client.post(
        "/api/pricing/products/import",
        json={"items": [{**item, "preco_catalogo": "60"}]},
    )
    assert r.status_code == 200
    await db.refresh(cenario["a003"])
    assert cenario["a003"].preco_catalogo == Decimal("60")


@pytest.mark.asyncio
async def test_lojas_recusa_catalogo_mas_deixa_tirar_o_antigo(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    auth_as(dono)
    loja = StoreInfo(user_id=dono.id, platform="ml", account_name="nova loja")
    db.add(loja)
    await db.commit()
    r = await client.post(
        f"/api/pricing/store-info/{loja.id}/department", json={"department": "catalogo"}
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "departamento_invalido"
    assert (
        await db.execute(select(PricingAccount).where(PricingAccount.name == "nova loja"))
    ).first() is None

    # Conta antiga no tipo catálogo: o DELETE ainda tira.
    db.add(PricingAccount(user_id=dono.id, name="nova loja", platform=PricingPlatform.ML,
                          segment_id=cenario["seg"]["catalogo"].id, store_info_id=loja.id))
    await db.commit()
    r = await client.delete(f"/api/pricing/store-info/{loja.id}/department/catalogo")
    assert r.status_code == 204
    assert (
        await db.execute(select(PricingAccount).where(PricingAccount.name == "nova loja"))
    ).first() is None


@pytest.mark.asyncio
async def test_lojas_nao_conta_nem_liga_a_coluna_de_catalogo(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
):
    """A filha tem o nome da base e nenhuma loja: o casamento por nome de
    Lojas não pode ligá-la à loja nem contar o tipo por ela."""
    auth_as(dono)
    assert (await _ligar(client, cenario["base"].id)).status_code == 200
    loja = StoreInfo(user_id=dono.id, platform="ml", account_name="counhago")
    db.add(loja)
    await db.commit()
    r = await client.post(
        f"/api/pricing/store-info/{loja.id}/department", json={"department": "celular"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == str(cenario["base"].id)
    filha = (
        await db.execute(select(PricingAccount).where(PricingAccount.canal == "catalogo"))
    ).scalar_one()
    assert filha.store_info_id is None
    lojas = (await client.get("/api/pricing/store-info")).json()
    assert next(x for x in lojas if x["id"] == str(loja.id))["departments"] == ["celular"]


# =========================================================== varredura


@pytest.mark.asyncio
async def test_varredura_grava_a_marca_de_catalogo(
    db: AsyncSession, dono: User, monkeypatch,
):
    from app.services.auto_link import run_auto_link

    integ = await _integracao_ml(db, dono, "zorvex")
    p = Product(user_id=dono.id, sku="a003.sa", name="Fone")
    db.add(p)
    await db.flush()
    existente = _link(dono, integ, p, "MLB500", listing_type=None, catalog_listing=None)
    db.add(existente)
    job = BackgroundJob(type=BackgroundJobType.AUTO_LINK, status=BackgroundJobStatus.PENDING,
                        created_by=dono.id)
    db.add(job)
    await db.commit()

    def _item(mlb, **raw):
        corpo = {"id": mlb, "status": raw.pop("status", "active"),
                 "listing_type_id": raw.pop("listing_type_id", "gold_special"), **raw}
        return {
            "external_id": mlb, "variation_id": None, "sku": "a003.sa", "title": "Fone",
            "listing_type": corpo["listing_type_id"], "status": corpo["status"], "stock": 3,
            "raw": corpo,
        }

    async def _lista(self, **_kw):
        yield _item("MLB500", catalog_listing=True, catalog_product_id="MLBCAT",
                    item_relations=[{"id": "MLB600", "variation_id": None}],
                    listing_type_id="gold_pro", status="paused")
        yield _item("MLB600", catalog_listing=False)
        # Sem a chave catalog_listing: marca fica não lida.
        yield {**_item("MLB700"), "raw": {"id": "MLB700"}}

    monkeypatch.setattr(ml_mod.MercadoLivreClient, "list_listings", _lista)
    await run_auto_link(db, job_id=job.id, integration_ids=[integ.id])

    links = {
        lk.external_id: lk
        for lk in (
            await db.execute(
                select(ProductLink)
                .where(ProductLink.integration_id == integ.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    e = links["MLB500"]
    assert e.id == existente.id
    assert (e.catalog_listing, e.catalog_product_id, e.catalogo_relacionado) == (
        True, "MLBCAT", "MLB600",
    )
    assert e.listing_type == "gold_pro"
    assert e.anuncio_status == "paused"
    assert e.catalogo_lido_em is not None
    novo = links["MLB600"]
    assert (novo.catalog_listing, novo.catalogo_relacionado) == (False, None)
    assert novo.catalogo_lido_em is not None
    assert links["MLB700"].catalog_listing is None
    assert links["MLB700"].catalogo_lido_em is None


# =========================================================== revisão final (07/10/2026)
# D3 só com o catálogo ligado; Kit "só catálogo" = bloqueado (não no_link);
# marca ainda não lida; GET 403/404 do ML sem insistir; escopo de equipe.


def test_d3_so_vale_com_o_catalogo_ligado(monkeypatch):
    a = uuid.uuid4()
    skus = {a: "a003.sa"}
    links = [_lk("COMUM", a, cat=False), _lk("CAT", a, cat=True)]
    kw = {"pricing_sku": "a003", "dept": "celular", "plataforma": "ml", "canal": "kit",
          "listing_type_conta": "ml classico"}
    assert anuncios.kit_pula_catalogo(True) is True
    assert anuncios.kit_pula_catalogo(False) is False
    # Sem o catálogo ligado: a regra de antes (manda também para o catálogo).
    res = resolver_anuncios(links, skus, pula_catalogo=False, **kw)
    assert sorted(lk.external_id for lk in res.links) == ["CAT", "COMUM"]
    assert res.so_catalogo == []
    res = resolver_anuncios(links, skus, pula_catalogo=True, **kw)
    assert [lk.external_id for lk in res.links] == ["COMUM"]
    # Flag desligada: a D3 vale em toda conta ML de Kit.
    monkeypatch.setattr(anuncios, "KIT_PULA_CATALOGO_SO_COM_CATALOGO_LIGADO", False)
    assert anuncios.kit_pula_catalogo(False) is True


def test_kit_so_com_anuncio_de_catalogo_vira_so_catalogo():
    a, b = uuid.uuid4(), uuid.uuid4()
    skus = {a: "a003.sa", b: "b999.sa"}
    kw = {"pricing_sku": "a003", "dept": "celular", "plataforma": "ml", "canal": "kit",
          "listing_type_conta": "ml classico"}
    links = [
        _lk("CAT", a, cat=True),
        _lk("CAT_PREMIUM", a, cat=True, tipo="gold_pro"),  # outro tipo: não conta
        _lk("OUTRO", b, cat=True),  # outra linha: não conta
    ]
    res = resolver_anuncios(links, skus, pula_catalogo=True, **kw)
    assert res.links == []
    assert [lk.external_id for lk in res.so_catalogo] == ["CAT"]
    info = anuncios.info_celula_kit_so_catalogo(res)
    assert info["bloqueio"] == "so_catalogo"
    assert info["texto"] == (
        "Este produto só tem anúncio de catálogo nesta conta — o preço vai pela coluna Catálogo"
    )
    assert [x["external_id"] for x in info["anuncios"]] == ["CAT"]
    # Havendo anúncio comum, a célula envia normalmente (sem "só catálogo").
    res = resolver_anuncios([*links, _lk("COMUM", a, cat=False)], skus, pula_catalogo=True, **kw)
    assert [lk.external_id for lk in res.links] == ["COMUM"]
    assert res.so_catalogo == [] and anuncios.info_celula_kit_so_catalogo(res) is None
    # Sem a D3 (catálogo desligado) não existe "só catálogo": vai para o CAT.
    res = resolver_anuncios(links, skus, pula_catalogo=False, **kw)
    assert [lk.external_id for lk in res.links] == ["CAT"]
    assert anuncios.info_celula_kit_so_catalogo(res) is None


@pytest.mark.asyncio
async def test_kit_sem_catalogo_ligado_manda_para_o_anuncio_de_catalogo_como_antes(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
    ml_falso: MLFalso, monkeypatch,
):
    """Nada muda para quem não ligou o catálogo: a coluna de Kit continua
    mandando para o anúncio de catálogo (banco E item vivo)."""
    auth_as(dono)
    base = cenario["base"]
    await db.execute(ProductLink.__table__.delete().where(ProductLink.external_id == "MLB101"))
    await db.commit()
    ml_falso.item("MLB100")
    ml_falso.item("MLB200", catalog_listing=True)
    ml_falso.item("MLB202", catalog_listing=True)  # marca não lida; vivo é catálogo

    out = await _push(client, base.id, cenario["a003"].id)
    assert (out["ok"], out["code"]) == (True, "ok"), out
    assert sorted(m for m, _ in ml_falso.puts_de_preco()) == ["MLB100", "MLB200", "MLB202"]

    # Ligou o catálogo: a D3 vale — o MLB200 sai pelo banco e o MLB202 pelo
    # item vivo (canal_errado).
    await _ligar(client, base.id)
    ml_falso.chamadas.clear()
    out = await _push(client, base.id, cenario["a003"].id)
    assert (out["ok"], out["code"]) == (True, "partial"), out
    assert [m for m, _ in ml_falso.puts_de_preco()] == ["MLB100"]
    assert out["detail"] == "1/2 variations ok; 1 pulado(s)"

    # Desligou: volta a ser como antes.
    await _ligar(client, base.id, False)
    ml_falso.chamadas.clear()
    out = await _push(client, base.id, cenario["a003"].id)
    assert sorted(m for m, _ in ml_falso.puts_de_preco()) == ["MLB100", "MLB200", "MLB202"]

    # Flag trocada (D3 em toda conta ML de Kit): pula mesmo sem o catálogo ligado.
    monkeypatch.setattr(anuncios, "KIT_PULA_CATALOGO_SO_COM_CATALOGO_LIGADO", False)
    ml_falso.chamadas.clear()
    out = await _push(client, base.id, cenario["a003"].id)
    assert [m for m, _ in ml_falso.puts_de_preco()] == ["MLB100"]
    assert out["detail"] == "1/2 variations ok; 1 pulado(s)"


@pytest.mark.asyncio
async def test_actual_prices_e_grid_do_kit_sem_catalogo_ligado_ficam_como_antes(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
    ml_falso: MLFalso,
):
    auth_as(dono)
    base = cenario["base"]
    a003 = cenario["a003"]
    # O a003 do counhago passa a ter SÓ o anúncio de catálogo do tipo da conta.
    await db.execute(
        ProductLink.__table__.delete().where(
            ProductLink.external_id.in_(["MLB100", "MLB101", "MLB202"])
        )
    )
    await db.commit()
    ml_falso.item("MLB200", catalog_listing=True, price=99)
    url = f"/api/pricing/actual-prices/{a003.id}"

    # Sem o catálogo ligado: o Kit lê (e mandaria para) o anúncio de catálogo.
    precos = (await client.get(url, params={"department": "celular"})).json()
    assert precos[str(base.id)] == 99.0
    grid = (await client.get("/api/pricing/grid", params={"department": "celular"})).json()
    par = (str(base.id), str(a003.id))
    kit = next(c for c in grid["cells"]
               if (c["pricing_account_id"], c["pricing_product_id"]) == par)
    assert kit["catalogo"] is None

    # Com o catálogo ligado: o Kit não lê mais o de catálogo; a filha lê.
    filha_id = (await _ligar(client, base.id)).json()["conta_catalogo"]["id"]
    precos = (await client.get(url, params={"department": "celular"})).json()
    assert precos[str(base.id)] is None
    assert precos[filha_id] == 99.0


@pytest.mark.asyncio
async def test_kit_com_catalogo_ligado_so_catalogo_e_bloqueado_nao_sem_vinculo(
    db: AsyncSession, client: AsyncClient, dono: User, cenario, auth_as: Callable,
    ml_falso: MLFalso,
):
    """Achado da revisão: a célula de Kit cujo único anúncio que casa é de
    catálogo não pode virar 'no_link' (sem vínculo) — o vínculo está certo, o
    preço é que vai pela coluna Catálogo."""
    auth_as(dono)
    base = cenario["base"]
    a003 = cenario["a003"]
    await db.execute(
        ProductLink.__table__.delete().where(
            ProductLink.external_id.in_(["MLB100", "MLB101", "MLB202"])
        )
    )
    await db.commit()
    ml_falso.item("MLB200", catalog_listing=True)
    await _ligar(client, base.id)

    out = await _push(client, base.id, a003.id)
    assert (out["ok"], out["code"]) == (False, "bloqueado"), out
    assert out["detail"].startswith(
        "Este produto só tem anúncio de catálogo nesta conta — o preço vai pela coluna Catálogo"
    )
    assert "MLB200" in out["detail"]
    assert "no product_links" not in out["detail"]
    assert ml_falso.chamadas == []
    assert (await db.execute(select(PricingOverride))).first() is None

    from app.services.pricing.push import push_one

    res = await push_one(db, user=dono, account_id=base.id, product_id=a003.id)
    assert res.payload["bloqueio"] == "so_catalogo"
    assert [e["externalId"] for e in res.payload["links"]] == ["MLB200"]

    # /grid: a célula de Kit mostra o cadeado 'so_catalogo', sem status de erro.
    grid = (await client.get("/api/pricing/grid", params={"department": "celular"})).json()
    celulas = {(c["pricing_account_id"], c["pricing_product_id"]): c for c in grid["cells"]}
    kit = celulas[(str(base.id), str(a003.id))]
    assert kit["cell_status"] == "auto"
    assert kit["catalogo"]["bloqueio"] == "so_catalogo"
    assert kit["catalogo"]["texto"] == (
        "Este produto só tem anúncio de catálogo nesta conta — o preço vai pela coluna Catálogo"
    )
    assert [x["external_id"] for x in kit["catalogo"]["anuncios"]] == ["MLB200"]
    # A outra conta de Kit da mesma integração (eron, sem catálogo ligado)
    # continua sem o campo — nada muda para ela.
    assert celulas[(str(cenario["sem_tipo"].id), str(a003.id))]["catalogo"] is None
    # dg052 só tem anúncio de catálogo também (sincronizado/pausado).
    assert celulas[(str(base.id), str(cenario["dg052"].id))]["catalogo"]["bloqueio"] == (
        "so_catalogo"
    )