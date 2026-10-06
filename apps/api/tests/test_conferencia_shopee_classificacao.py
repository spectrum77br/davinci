"""Conferência Shopee — o que é Eletro, item a item (services/conferencia_shopee/classificacao).

Ordem: 1) vínculo do DaVinci (vence a categoria da Shopee, para os dois
lados); 2) sem vínculo, a categoria de nível 1 da Shopee (100010/100636);
3) sem vínculo e sem categoria, o título (sem acento, sem maiúscula). E a
parte do banco: conta → store_info → pricing_accounts → product_links →
products (SKU, categoria do Bling, segmento debaixo de `eletro`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Integration,
    IntegrationPlatform,
    PricingAccount,
    PricingPlatform,
    Product,
    ProductCategory,
    ProductLink,
    Segment,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.services.conferencia_shopee import classificacao as cl

# ───────────────────────────────────────────────────────────── regra pura


def test_vinculo_eletro_vence():
    assert cl.classificar("1", "Capinha", {"1": 100013}, {"1": True}) == (True, "davinci")


def test_vinculo_nao_eletro_vence_a_categoria_da_shopee():
    # A Shopee diz Eletrodomésticos e o título diz air fryer: o DaVinci manda.
    assert cl.classificar("1", "Air Fryer 4L", {"1": 100010}, {"1": False}) == (
        False,
        "davinci",
    )


def test_sem_vinculo_vale_a_categoria():
    assert cl.classificar("2", "Qualquer", {"2": 100010}, {}) == (True, "categoria")
    assert cl.classificar("2", "Qualquer", {"2": 100636}, {}) == (True, "categoria")
    # Outra categoria: não é eletro, mesmo com título de air fryer.
    assert cl.classificar("2", "Air Fryer", {"2": 100013}, {}) == (False, "categoria")


@pytest.mark.parametrize(
    "nome",
    [
        "AirFryer de Vidro 5L",
        "Air Fryer Digital",
        "Fritadeira Elétrica",
        "CAFETEIRA expresso",
        "Máquina de Slush",
        "Jogo Cookware 5 peças",
        "Panela Elétrica",
        "Smart Cook Multi",
        "SmartCooker",
        "Sorvetéira",
        "Sorvete Maker",
    ],
)
def test_sem_vinculo_e_sem_categoria_vale_o_titulo(nome):
    assert cl.classificar("3", nome, {}, {}) == (True, "titulo")


def test_titulo_que_nao_casa_nao_e_eletro():
    assert cl.classificar("4", "Capinha iPhone 15", {}, {}) == (False, "nenhuma")
    assert cl.classificar("4", None, {}, {}) == (False, "nenhuma")
    # Anúncio sem item (da loja): só o título decide.
    assert cl.classificar(None, "Air fryer da loja", {}, {}) == (True, "titulo")


def test_categorias_por_item_pega_a_primeira_de_qualquer_semana():
    dados = {
        "semanas": [
            {"afiliados_itens": None},
            {
                "afiliados_itens": [
                    {"item_id": 10, "categoria_id": 100010},
                    {"item_id": "11", "categoria_id": "100013"},
                    {"item_id": None, "categoria_id": 100010},
                    {"item_id": "12", "categoria_id": None},
                ]
            },
            {"afiliados_itens": [{"item_id": "10", "categoria_id": 100013}]},
        ]
    }
    assert cl.categorias_por_item(dados) == {"10": 100010, "11": 100013}
    assert cl.categorias_por_item(None) == {}


def test_divergencia_entre_davinci_e_shopee():
    assert cl.divergencia("1", {"1": 100013}, {"1": True}) == {
        "davinci": "eletro",
        "categoria_shopee": 100013,
    }
    assert cl.divergencia("2", {"2": 100010}, {"2": False}) == {
        "davinci": "outro",
        "categoria_shopee": 100010,
    }
    assert cl.divergencia("3", {"3": 100636}, {"3": True}) is None  # concordam
    assert cl.divergencia("4", {"4": 100010}, {}) is None  # sem vínculo
    assert cl.divergencia("5", {}, {"5": True}) is None  # sem categoria


def test_produto_eletro_pelos_tres_sinais():
    seg = uuid.uuid4()
    cats = {"501": "Eletro Kit", "502": "Celular"}
    assert cl.produto_eletro("u001", None, None, cats, set())
    # Sufixo regional vence o prefixo u (sku_tags): precisa de outro sinal.
    assert not cl.produto_eletro("u001.sp", None, None, cats, set())
    assert cl.produto_eletro("u001.sp", "501", None, cats, set())
    assert cl.produto_eletro("dg1.sp", "Eletrodomésticos", None, cats, set())
    assert not cl.produto_eletro("dg1.sp", "502", None, cats, set())
    assert not cl.produto_eletro("dg1.sp", "999", None, cats, set())  # id desconhecido
    assert cl.produto_eletro("dg1.sp", None, seg, cats, {seg})
    assert not cl.produto_eletro("dg1.sp", None, seg, cats, set())


# ───────────────────────────────────────────────────────────── banco


@pytest_asyncio.fixture
async def dono(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:conf-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"conf-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _cenario(db: AsyncSession, u: User) -> dict:
    integ = {
        nome: Integration(
            user_id=u.id, platform=IntegrationPlatform.SHOPEE, name=nome, credentials=b"x"
        )
        for nome in ("barbosa", "outra", "mega")
    }
    db.add_all(integ.values())
    raiz_eletro = Segment(name="Eletro", slug="eletro")
    raiz_celular = Segment(name="Celular", slug="celular")
    db.add_all([raiz_eletro, raiz_celular])
    await db.flush()
    filho = Segment(name="1", slug="1", parent_id=raiz_eletro.id)
    db.add(filho)
    await db.flush()
    neto = Segment(name="a", slug="a", parent_id=filho.id)
    db.add(neto)
    db.add_all(
        [
            ProductCategory(bling_category_id=501, name="Eletro"),
            ProductCategory(bling_category_id=502, name="Celular"),
        ]
    )
    lojas = {
        "barbosa": StoreInfo(user_id=u.id, platform="Shopee", account_name=" Barbosa "),
        "mega": StoreInfo(
            user_id=u.id, platform="shopee", account_name="mega", integration_id=integ["mega"].id
        ),
        "arquivada": StoreInfo(
            user_id=u.id,
            platform="shopee",
            account_name="velha",
            archived_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        "ml": StoreInfo(user_id=u.id, platform="mercadolivre", account_name="barbosa"),
    }
    db.add_all(lojas.values())
    await db.flush()
    db.add_all(
        [
            PricingAccount(
                user_id=u.id,
                name="barbosa",
                platform=PricingPlatform.SHOPEE,
                segment_id=raiz_celular.id,
                store_info_id=lojas["barbosa"].id,
                integration_id=integ["barbosa"].id,
            ),
            PricingAccount(
                user_id=u.id,
                name="velha",
                platform=PricingPlatform.SHOPEE,
                segment_id=raiz_celular.id,
                store_info_id=lojas["arquivada"].id,
                integration_id=integ["outra"].id,
            ),
        ]
    )
    produtos = {
        "sku": Product(user_id=u.id, sku="u001", name="Air fryer"),
        "cat_id": Product(user_id=u.id, sku="dg001.sp", name="x", category="501"),
        "seg": Product(user_id=u.id, sku="dg002.sp", name="x", segment_id=neto.id),
        "celular": Product(user_id=u.id, sku="dg003.sp", name="x", category="502"),
        "cat_nome": Product(user_id=u.id, sku="dg004.ci", name="x", category="Eletro Kit"),
        "nada": Product(user_id=u.id, sku="dg005.sp", name="x"),
    }
    db.add_all(produtos.values())
    await db.flush()

    def link(item: str, prod: str, conta: str = "barbosa", **extra) -> ProductLink:
        return ProductLink(
            user_id=u.id,
            product_id=produtos[prod].id,
            integration_id=integ[conta].id,
            platform=extra.pop("platform", IntegrationPlatform.SHOPEE),
            external_id=item,
            variation_id=extra.pop("variation_id", "0"),
            **extra,
        )

    db.add_all(
        [
            link("100", "sku"),
            # Um item com duas variações: uma de celular, uma de eletro → eletro.
            link("200", "celular", variation_id="1"),
            link("200", "cat_id", variation_id="2"),
            link("300", "celular"),
            link("400", "seg"),
            link("500", "cat_nome"),
            link("600", "sku", morto_desde=datetime(2026, 9, 1, tzinfo=UTC)),
            link("700", "nada"),
            link("900", "sku", platform=IntegrationPlatform.ML),
            link("800", "sku", conta="outra"),
            link("110", "cat_id", conta="mega"),
        ]
    )
    await db.commit()
    return integ


@pytest.mark.asyncio
async def test_mapa_do_davinci_por_conta(db: AsyncSession, dono: User):
    await _cenario(db, dono)
    mapa = await cl.carregar_mapa_davinci(db, ["barbosa", "MEGA", "luno", "velha", None, ""])
    assert mapa["barbosa"] == {
        "100": True,  # SKU u…
        "200": True,  # qualquer variação eletro
        "300": False,  # vinculado, nenhum eletro
        "400": True,  # segmento debaixo da raiz eletro (neto)
        "500": True,  # categoria pelo nome
        "700": False,  # sem sinal nenhum
    }
    # Pela integração da própria store_info (sem pricing_account).
    assert mapa["mega"] == {"110": True}
    # Loja sem cadastro e loja arquivada: mapa vazio (regras 2 e 3).
    assert mapa["luno"] == {}
    assert mapa["velha"] == {}
    assert set(mapa) == {"barbosa", "mega", "luno", "velha"}


@pytest.mark.asyncio
async def test_mapa_do_davinci_filtra_pelos_itens(db: AsyncSession, dono: User):
    await _cenario(db, dono)
    mapa = await cl.carregar_mapa_davinci(db, ["barbosa"], item_ids=["100", "300", "999"])
    assert mapa == {"barbosa": {"100": True, "300": False}}
    assert await cl.carregar_mapa_davinci(db, ["barbosa"], item_ids=[]) == {"barbosa": {}}
    assert await cl.carregar_mapa_davinci(db, []) == {}
