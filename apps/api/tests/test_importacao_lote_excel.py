"""Exportar Excel de conferência de um lote de importação.

GET /api/importacao/lotes/{id}/excel → .xlsx com uma linha por produto DA
CATEGORIA do lote, mesmo sem quantidade nele (Produto/SKU/Custo/Estoque/
Quantidade/Total). O Estoque é o mesmo da coluna Estoque da grade (GET
/api/importacao/products): no celular a soma dos lotes da base, na mala e no
eletro o SKU exato, só produto ativo; sem SKU no Bling, o estoque manual.
"""
from __future__ import annotations

import io
import uuid
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from openpyxl import load_workbook
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ImportLote,
    ImportLoteItem,
    ImportProduct,
    Product,
    User,
    UserRole,
    UserStatus,
)

PERM = {"importacao": {"view": True, "edit": True, "delete": True}}
CABECALHO = ("Produto", "SKU", "Custo", "Estoque", "Quantidade", "Total")


@pytest_asyncio.fixture
async def admin_view(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:le-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"le-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions=PERM,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _rows(content: bytes) -> list[tuple]:
    wb = load_workbook(io.BytesIO(content))
    ws = wb.active
    return [tuple(r) for r in ws.iter_rows(values_only=True)]


def _produto_bling(dono: User, sku: str, estoque: int, *, situacao: str = "A") -> Product:
    return Product(
        user_id=dono.id, sku=sku, name=sku, stock=estoque, situacao=situacao, formato="S"
    )


@pytest.mark.asyncio
async def test_excel_mala_todos_os_produtos_qtd_vezes_custo(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """Mala: todos os produtos da categoria, na ordem da grade; total =
    quantidade × custo_bling; sem quantidade no lote, Quantidade e Total em
    branco. Estoque: o SKU exato do Bling, senão o manual."""
    auth_as(admin_view)
    lt = ImportLote(
        id=uuid.uuid4(), categoria="mala", nome="ML25",
        abertura=date(2026, 6, 1),
    )
    db.add(lt)
    p1 = ImportProduct(
        id=uuid.uuid4(), categoria="mala", sku="a075",
        modelo_bling="Chaveiro Poofy", custo_bling=Decimal("9.00"),
    )
    p2 = ImportProduct(
        id=uuid.uuid4(), categoria="mala", sku="a076",
        modelo_bling="Encosto Cabeca", custo_bling=Decimal("18.00"), estoque_bling=7,
    )
    # Quantidade 0 nesse lote: entra mesmo assim (planilha completa).
    p3 = ImportProduct(
        id=uuid.uuid4(), categoria="mala", sku="a077",
        modelo_bling="Zerado", custo_bling=Decimal("5.00"),
    )
    # Outra categoria: fica fora.
    p4 = ImportProduct(
        id=uuid.uuid4(), categoria="eletro", sku="uaf001m1.110",
        modelo_bling="Airfryer", custo_bling=Decimal("100.00"),
    )
    db.add_all([p1, p2, p3, p4, _produto_bling(admin_view, "A075", 40)])
    await db.flush()
    db.add_all([
        ImportLoteItem(id=uuid.uuid4(), lote_id=lt.id, product_id=p1.id, quantidade=5040),
        ImportLoteItem(id=uuid.uuid4(), lote_id=lt.id, product_id=p2.id, quantidade=1680),
        ImportLoteItem(id=uuid.uuid4(), lote_id=lt.id, product_id=p3.id, quantidade=0),
    ])
    await db.commit()

    r = await client.get(f"/api/importacao/lotes/{lt.id}/excel")
    assert r.status_code == 200, r.text
    assert "spreadsheetml" in r.headers["content-type"]
    rows = _rows(r.content)
    assert rows[0] == CABECALHO
    assert rows[1:-1] == [
        ("Chaveiro Poofy", "a075", 9.0, 40, 5040, 45360.0),
        ("Encosto Cabeca", "a076", 18.0, 7, 1680, 30240.0),  # sem Bling: o manual
        ("Zerado", "a077", 5.0, None, None, None),
    ]
    # Rodapé: qtd 6720, valor 75600 (openpyxl lê vazio como None).
    assert rows[-1] == (None, "Total", None, None, 6720, 75600.0)


@pytest.mark.asyncio
async def test_excel_celular_usa_custo_brl(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """Celular: custo unitário = valor_usd × taxa × (1+frete) + adicional."""
    auth_as(admin_view)
    lt = ImportLote(
        id=uuid.uuid4(), categoria="celular", nome="C10",
        abertura=date(2026, 6, 1),
        taxa=Decimal("5"), frete_pct=Decimal("0"), adicional=Decimal("0"),
    )
    db.add(lt)
    p = ImportProduct(
        id=uuid.uuid4(), categoria="celular", sku="i999.sa",
        modelo_bling="Apple Test", custo_bling=Decimal("0"),
    )
    db.add(p)
    await db.flush()
    db.add(ImportLoteItem(
        id=uuid.uuid4(), lote_id=lt.id, product_id=p.id,
        quantidade=10, valor_usd=Decimal("200"),
    ))
    await db.commit()

    r = await client.get(f"/api/importacao/lotes/{lt.id}/excel")
    assert r.status_code == 200, r.text
    rows = _rows(r.content)
    # custoBRL = 200 × 5 = 1000; total = 10 × 1000 = 10000; sem Bling nem
    # estoque manual, o Estoque fica vazio.
    assert rows[1] == ("Apple Test", "i999.sa", 1000.0, None, 10, 10000.0)


@pytest.mark.asyncio
async def test_excel_celular_estoque_soma_os_lotes_como_a_grade(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """Celular: o Estoque soma os lotes ativos da base — o mesmo número da
    coluna Estoque da grade. Antes vinha o SKU exato (a019.cd = 2598)."""
    auth_as(admin_view)
    lt = ImportLote(
        id=uuid.uuid4(), categoria="celular", nome="AG232",
        abertura=date(2026, 6, 1),
    )
    db.add_all([
        lt,
        ImportProduct(categoria="celular", sku="a019.cd", modelo_bling="Airtag"),
        ImportProduct(
            categoria="celular", sku="zz001.cd", modelo_bling="Sem Bling - Preto",
            estoque_bling=5,
        ),
        _produto_bling(admin_view, "a019.cd", 2598),
        _produto_bling(admin_view, "a019.sp", 100),
        _produto_bling(admin_view, "a019.ra", 500, situacao="I"),  # inativo: fora
        _produto_bling(admin_view, "zz001.cd", 90, situacao="I"),  # inativo: vale o manual
    ])
    await db.commit()

    r = await client.get(f"/api/importacao/lotes/{lt.id}/excel")
    assert r.status_code == 200, r.text
    estoque_no_excel = {row[1]: row[3] for row in _rows(r.content)[1:-1]}
    assert estoque_no_excel == {"a019.cd": 2698, "zz001.cd": 5}

    r = await client.get("/api/importacao/products?categoria=celular")
    assert r.status_code == 200, r.text
    assert {p["sku"]: p["estoque_bling"] for p in r.json()} == estoque_no_excel


@pytest.mark.asyncio
async def test_excel_lote_404(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    auth_as(admin_view)
    r = await client.get(f"/api/importacao/lotes/{uuid.uuid4()}/excel")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "lote_not_found"


@pytest.mark.asyncio
async def test_excel_exige_auth(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    auth_as(None)
    r = await client.get(f"/api/importacao/lotes/{uuid.uuid4()}/excel")
    assert r.status_code in (401, 403)
