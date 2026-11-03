"""Importação — coluna "30d", estoque somado no celular e prazos (02/11/2026).

  * vendas_30d: só venda real do Bling (services/vendas_periodo), casada por
    categoria — celular pela base inteira (a019.cd conta todo a019 e as peças
    a019 dos kits), mala pela regra dos tamanhos, eletro exato. Nunca o
    consumo manual gravado.
  * Estoque do celular: soma dos lotes da base, com as parcelas em
    estoque_partes; mala/eletro: SKU exato, só ativo.
  * PATCH /config: prazos de 0 a 999 dias.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingKitComponent,
    BlingOrder,
    ImportProduct,
    Product,
    User,
    UserRole,
    UserStatus,
)

PERM = {"importacao": {"view": True, "edit": True, "delete": False}}


@pytest_asyncio.fixture
async def comprador(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:iv-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"iv-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _pedido(codigo: str, quantidade: int, *, dias_atras: float = 1, situacao="6") -> BlingOrder:
    return BlingOrder(
        numero=uuid.uuid4().hex[:8],
        data=datetime.now(UTC) - timedelta(days=dias_atras),
        situacao=situacao,
        item_codigo=codigo,
        item_quantidade=quantidade,
    )


def _produto(dono: User, sku: str, estoque: int, *, situacao: str = "A") -> Product:
    return Product(
        user_id=dono.id, sku=sku, name=sku, stock=estoque, situacao=situacao, formato="S"
    )


async def _listar(client: AsyncClient, categoria: str) -> dict[str, dict]:
    r = await client.get(f"/api/importacao/products?categoria={categoria}")
    assert r.status_code == 200, r.text
    return {p["sku"]: p for p in r.json()}


@pytest.mark.asyncio
async def test_celular_conta_a_base_inteira_e_soma_os_lotes(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    comprador: User,
):
    db.add_all([
        ImportProduct(categoria="celular", sku="a019.cd", modelo_bling="Airtag"),
        ImportProduct(categoria="celular", sku="dg053.ci", modelo_bling="A17 - Preto"),
        _produto(comprador, "a019.cd", 2598),
        _produto(comprador, "a019.ci", 100),
        _produto(comprador, "a019.us", 7),  # usado: fora do estoque
        _produto(comprador, "a019.sp", 3, situacao="I"),  # inativo: fora
        _produto(comprador, "dg053.ci", 172),
        _produto(comprador, "dg053.sp", 209),
        _pedido("a019.sp", 3),
        _pedido("dg053.sp+a019.sp", 2),  # o kit credita a019 e dg053
        _pedido("a019.us", 1),  # venda do lote usado também é venda da base
        _pedido("a019.cd", 1, dias_atras=40),  # fora dos 30 dias
        _pedido("a019.ci", 4, situacao="12"),  # cancelado
    ])
    await db.commit()
    auth_as(comprador)

    linhas = await _listar(client, "celular")
    airtag = linhas["a019.cd"]
    assert airtag["vendas_30d"] == 6
    assert airtag["estoque_bling"] == 2698
    assert airtag["estoque_partes"] == [
        {"sku": "a019.cd", "estoque": 2598},
        {"sku": "a019.ci", "estoque": 100},
    ]
    # Os baldes mensais continuam alimentando o consumo (que a tela não
    # mostra mais): 6 no último mês, 1 no anterior.
    assert Decimal(airtag["consumo_diario"]) == Decimal(6) / 30
    assert Decimal(airtag["maior_media_30d"]) == Decimal(6) / 30

    a17 = linhas["dg053.ci"]
    assert a17["vendas_30d"] == 2
    assert a17["estoque_bling"] == 381


@pytest.mark.asyncio
async def test_mala_regra_dos_tamanhos_e_eletro_exato(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    comprador: User,
):
    db.add_all([
        ImportProduct(categoria="mala", sku="b045.18", modelo_bling="M5 mista"),
        ImportProduct(categoria="eletro", sku="uaf001m1.110", modelo_bling="Airfryer"),
        _produto(comprador, "b045.18", 12),
        _produto(comprador, "b045.18.ci", 50),  # mala não soma lote
        _produto(comprador, "uaf001m1.110", 4),
        _pedido("b045.18", 1),
        _pedido("b045.12.18", 2),  # conjunto com o tamanho 18
        _pedido("b045.20", 9),  # outro tamanho
        _pedido("b045.18+a075", 5),  # kit com acessório: a mala saiu
        _pedido("uaf001m1.110", 2),
        _pedido("uaf001m1.220", 5),  # outra voltagem
    ])
    await db.commit()
    auth_as(comprador)

    mala = (await _listar(client, "mala"))["b045.18"]
    assert mala["vendas_30d"] == 8
    assert mala["estoque_bling"] == 12
    assert mala["estoque_partes"] is None

    eletro = (await _listar(client, "eletro"))["uaf001m1.110"]
    assert eletro["vendas_30d"] == 2
    assert eletro["estoque_bling"] == 4
    assert eletro["estoque_partes"] is None


@pytest_asyncio.fixture
async def sem_composicao(db: AsyncSession):
    """bling_kit_components não está na limpeza do conftest: começa e termina vazia."""
    await db.execute(text("DELETE FROM bling_kit_components"))
    await db.commit()
    yield
    await db.execute(text("DELETE FROM bling_kit_components"))
    await db.commit()


@pytest.mark.asyncio
async def test_mala_kit_6_malas_conta_em_cada_tamanho(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    comprador: User, sem_composicao: None,
):
    """O "Kit 6 Malas" (SKU só a base, b047) entra no 30d de cada mala pela
    composição do Bling — a 12 em dobro, como o Bling baixa."""
    kit = Product(user_id=comprador.id, sku="b047", name="Kit 6 Malas", stock=0,
                  situacao="A", formato="E", bling_product_id=9000)
    db.add(kit)
    for i, (sku, no_kit) in enumerate(
        {"b047.8": 1, "b047.12": 2, "b047.18": 1, "b047.20": 1, "b047.24": 1}.items(), start=1
    ):
        peca = _produto(comprador, sku, 10)
        peca.bling_product_id = 9000 + i
        db.add(peca)
        db.add(BlingKitComponent(
            kit_bling_product_id=9000, component_bling_product_id=9000 + i, quantidade=no_kit,
        ))
    db.add_all([
        ImportProduct(categoria="mala", sku="b047.12", modelo_bling="M5 mista 12"),
        ImportProduct(categoria="mala", sku="b047.8", modelo_bling="M5 mista 8"),
        _pedido("b047", 3),
        _pedido("b047.12", 1),
        _pedido("b047.12.24", 2),  # conjunto com o 12
    ])
    await db.commit()
    auth_as(comprador)

    mala = await _listar(client, "mala")
    assert mala["b047.12"]["vendas_30d"] == 1 + 2 + 3 * 2
    assert mala["b047.8"]["vendas_30d"] == 3


@pytest.mark.asyncio
async def test_sem_venda_e_sem_sku_no_bling_vale_o_manual(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    comprador: User,
):
    """vendas_30d nunca usa o consumo manual; o estoque manual só vale quando
    o SKU não está (ativo) no Bling."""
    db.add_all([
        ImportProduct(
            categoria="mala", sku="b777.20", modelo_bling="Manual",
            estoque_bling=33, consumo_diario=Decimal("10"), maior_media_30d=Decimal("12"),
        ),
        ImportProduct(categoria="celular", sku="zz001.cd", modelo_bling="Sem Bling - Preto",
                      estoque_bling=5),
        _produto(comprador, "b777.20", 90, situacao="E"),  # excluído: não conta
    ])
    await db.commit()
    auth_as(comprador)

    mala = (await _listar(client, "mala"))["b777.20"]
    assert mala["vendas_30d"] == 0
    assert mala["estoque_bling"] == 33
    assert Decimal(mala["consumo_diario"]) == Decimal("10")
    assert Decimal(mala["maior_media_30d"]) == Decimal("12")

    celular = (await _listar(client, "celular"))["zz001.cd"]
    assert celular["vendas_30d"] == 0
    assert celular["estoque_bling"] == 5
    assert celular["estoque_partes"] is None


@pytest.mark.asyncio
async def test_patch_config_aceita_so_de_0_a_999_dias(
    client: AsyncClient, auth_as: Callable[[User | None], None], comprador: User,
):
    auth_as(comprador)
    for corpo in ({"tempo_reposicao": -1}, {"tempo_estoque": -30}, {"tempo_estoque": 1000}):
        r = await client.patch("/api/importacao/config?categoria=celular", json=corpo)
        assert r.status_code == 422, (corpo, r.text)

    r = await client.patch(
        "/api/importacao/config?categoria=celular",
        json={"tempo_reposicao": 0, "tempo_estoque": 999},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"categoria": "celular", "tempo_reposicao": 0, "tempo_estoque": 999}
