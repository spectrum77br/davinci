"""O que conta como venda (services/vendas_periodo.py, 02/11/2026).

Coluna "30d" da Tabela de Preços e da Importação: fora cancelado (o espelho
grava o NÚMERO '12'), excluído, devolvido, as transferências das lojas
internas e quantidade ≤ 0; situação NULL conta; o kit `a+b` credita cada peça
sem deixar a chave composta (senão a base do celular contaria duas vezes); o
kit de base do Bling ("Kit 6 Malas" b047) credita as peças pela composição.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from structlog.testing import capture_logs
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingKitComponent, BlingOrder, Product, StoreInfo, User, UserRole, UserStatus
from app.services.vendas_periodo import kits_de_base, pecas_do_codigo, vendas_por_chave

LOJA_CD = "205632678"
LOJA_BALANCO = "205660518"
LOJA_SHOPEE = "300100200"
EM_ABERTO = "6"


async def _lojas(db: AsyncSession) -> User:
    dono = User(
        open_id=f"email:vp-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"vp-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(dono)
    await db.flush()
    db.add_all([
        StoreInfo(user_id=dono.id, platform="interno", account_name="Centro de Distribuição",
                  bling_store_id=LOJA_CD),
        StoreInfo(user_id=dono.id, platform=" Interno ", account_name="Devolução/Balanço",
                  bling_store_id=LOJA_BALANCO),
        StoreInfo(user_id=dono.id, platform="shopee", account_name="Loja Shopee",
                  bling_store_id=LOJA_SHOPEE),
        # Ficha sem loja do Bling: o NULL não pode derrubar o filtro.
        StoreInfo(user_id=dono.id, platform="interno", account_name="Sem Bling"),
    ])
    await db.commit()
    return dono


def _pedido(
    codigo: str | None,
    quantidade: int | None,
    *,
    dias_atras: float = 1,
    situacao: str | None = EM_ABERTO,
    loja: str | None = LOJA_SHOPEE,
) -> BlingOrder:
    return BlingOrder(
        numero=uuid.uuid4().hex[:8],
        data=datetime.now(UTC) - timedelta(days=dias_atras),
        situacao=situacao,
        loja=loja,
        item_codigo=codigo,
        item_quantidade=quantidade,
    )


def test_pecas_do_codigo():
    assert pecas_do_codigo("dg053.sp+a001.sp") == ["dg053.sp", "a001.sp"]
    assert pecas_do_codigo(" DG053.CI + a001.ci +") == ["dg053.ci", "a001.ci"]
    assert pecas_do_codigo("a019.cd") == ["a019.cd"]
    assert pecas_do_codigo("") == []
    assert pecas_do_codigo(None) == []


@pytest.mark.asyncio
async def test_so_venda_real_entra(db: AsyncSession):
    await _lojas(db)
    db.add_all([
        _pedido("x001.sp", 2),                            # conta
        _pedido("x001.sp", 3, situacao=None),             # NULL conta
        _pedido(" X001.SP ", 1),                          # espaço/maiúscula: mesma chave
        _pedido("x001.sp", 1, loja=None),                 # pedido sem loja conta
        _pedido("x001.sp", 1, loja="999999"),             # loja sem ficha conta
        _pedido("x001.sp", 5, situacao="12"),             # cancelado (número)
        _pedido("x001.sp", 7, situacao="excluido"),       # excluído no Bling
        _pedido("x001.sp", 11, situacao="Cancelado"),     # nome antigo
        _pedido("x001.sp", 13, situacao="Devolvido"),
        _pedido("x001.sp", 600, loja=LOJA_CD),            # transferência entre lotes
        _pedido("x001.sp", -600, loja=LOJA_CD),
        _pedido("x001.sp", 17, loja=LOJA_BALANCO),        # " Interno " também é interna
        _pedido("x001.sp", 0),                            # quantidade ≤ 0
        _pedido("x001.sp", -4),
        _pedido("x001.sp", None),
        _pedido("", 9),                                   # sem SKU
        _pedido("   ", 9),
        _pedido(None, 9),
    ])
    await db.commit()

    vendas = await vendas_por_chave(db, [30])
    assert vendas == {"x001.sp": [8]}


@pytest.mark.asyncio
async def test_kit_credita_cada_peca_sem_duplicar_a_base(db: AsyncSession):
    await _lojas(db)
    db.add_all([
        _pedido("dg053.sp+a001.sp", 2),
        _pedido("dg053.sp", 1),
        _pedido("a001.sp", 4),
        _pedido(" DG053.CI + a001.ci +", 1),
        _pedido("dg053.sp+a001.sp", 50, situacao="12"),  # kit cancelado não credita ninguém
    ])
    await db.commit()

    vendas = await vendas_por_chave(db, [30])
    assert vendas == {
        "dg053.sp": [3],
        "a001.sp": [6],
        "dg053.ci": [1],
        "a001.ci": [1],
    }
    assert not any("+" in chave for chave in vendas)
    # A base do celular (tudo antes do 1º ponto) conta o aparelho uma vez
    # por unidade vendida: 3 (.sp) + 1 (.ci), não 3 + 2 do kit de novo.
    assert sum(v[0] for k, v in vendas.items() if k.split(".")[0] == "dg053") == 4
    assert sum(v[0] for k, v in vendas.items() if k.split(".")[0] == "a001") == 7


@pytest.mark.asyncio
async def test_janelas_acumuladas_na_ordem_pedida(db: AsyncSession):
    await _lojas(db)
    db.add_all([
        _pedido("y001", 1, dias_atras=1),
        _pedido("y001", 2, dias_atras=10),
        _pedido("y001", 4, dias_atras=40),
        _pedido("y001", 8, dias_atras=200),   # fora da maior janela
        _pedido("y002", 16, dias_atras=50),   # só na de 60 dias
    ])
    await db.commit()

    vendas = await vendas_por_chave(db, [30, 7, 60])
    assert vendas == {"y001": [3, 1, 7], "y002": [0, 0, 16]}


@pytest.mark.asyncio
async def test_sem_janela_e_janela_invalida(db: AsyncSession):
    assert await vendas_por_chave(db, []) == {}
    with pytest.raises(ValueError, match="positiva"):
        await vendas_por_chave(db, [30, 0])


def test_kits_de_base():
    composicao = [
        ("B047", "b047.12", Decimal("2.0000")),
        ("b047", " B047.8 ", 1),
        ("b047", "b047.18", 1),
        ("b047", "b047.12", 2),  # a mesma peça em outra ficha: conta uma vez
        ("b048", "b048.12", 1),
        ("b048", "a075", 1),  # peça que não é "kit.número": o kit sai inteiro
        ("b049", "b049.12", Decimal("0.5")),  # quantidade quebrada: sai
        ("b050", "b051.12", 1),  # peça de outra base: sai
        ("b045.12.18", "b045.12", 1),  # tamanho no SKU: é a regra dos tamanhos
        ("dg053.sp+a001.sp", "dg053.sp", 1),  # '+': repartido pelo SKU
        (None, "b052.12", 1),
    ]
    assert kits_de_base(composicao) == {"b047": {"b047.12": 2, "b047.8": 1, "b047.18": 1}}


@pytest_asyncio.fixture
async def sem_composicao(db: AsyncSession):
    """bling_kit_components não está na limpeza do conftest: começa e termina vazia."""
    await db.execute(text("DELETE FROM bling_kit_components"))
    await db.commit()
    yield
    await db.execute(text("DELETE FROM bling_kit_components"))
    await db.commit()


@pytest.mark.asyncio
async def test_kit_de_base_credita_as_pecas_pela_composicao(db: AsyncSession, sem_composicao: None):
    dono = await _lojas(db)
    # "Kit 6 Malas" b047 (formato E no Bling): 8, 12 ×2, 18, 20 e 24.
    db.add(Product(user_id=dono.id, sku="B047", name="Kit 6 Malas", formato="E",
                   bling_product_id=9000))
    for i, (sku, no_kit) in enumerate(
        {"b047.8": 1, "b047.12": 2, "b047.18": 1, "b047.20": 1, "b047.24": 1}.items(), start=1
    ):
        db.add(Product(user_id=dono.id, sku=sku, name=sku, formato="S", bling_product_id=9000 + i))
        db.add(BlingKitComponent(
            kit_bling_product_id=9000, component_bling_product_id=9000 + i, quantidade=no_kit,
        ))
    db.add_all([
        _pedido("b047", 3),
        _pedido("B047", 1, dias_atras=10),
        _pedido("b047", 5, situacao="12"),  # kit cancelado não credita ninguém
        _pedido("b047.12", 1),
        _pedido("b047.12.24", 2),  # conjunto: fica com a chave dele
    ])
    await db.commit()

    vendas = await vendas_por_chave(db, [30, 7])
    assert vendas == {
        "b047": [4, 3],  # a linha do próprio kit continua com as vendas dele
        "b047.8": [4, 3],
        "b047.12": [1 + 4 * 2, 1 + 3 * 2],
        "b047.18": [4, 3],
        "b047.20": [4, 3],
        "b047.24": [4, 3],
        "b047.12.24": [2, 2],
    }


@pytest.mark.asyncio
async def test_kit_de_base_sem_composicao_fica_so_com_a_chave_e_avisa(
    db: AsyncSession, sem_composicao: None,
):
    """Kit novo no meio da semana (o cache da composição é de domingo): as
    vendas não chegam às peças, a chave do kit fica e o log avisa."""
    dono = await _lojas(db)
    db.add_all([
        Product(user_id=dono.id, sku="b099", name="Kit 6 Malas novo", formato="E", bling_product_id=9100),
        Product(user_id=dono.id, sku="a015", name="Balança", formato="S", bling_product_id=9101),
        _pedido("b099", 10),
        _pedido("a015", 2),  # SKU sem ponto que não é kit: sem aviso
    ])
    await db.commit()

    with capture_logs() as logs:
        vendas = await vendas_por_chave(db, [30])
    assert vendas == {"b099": [10], "a015": [2]}
    avisos = [log for log in logs if log["event"] == "vendas_periodo.kit_sem_composicao"]
    assert len(avisos) == 1
    assert avisos[0]["kits"] == ["b099"]
