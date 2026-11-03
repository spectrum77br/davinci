"""Consórcio: Imposto 1% editável (imposto1_manual, 0447) e PATCH com vários campos.

Eduardo, 03/11/2026: o 1% é calculado na tela (1% do crédito nas cotas com
lance); a coluna guarda só o valor digitado — NULL volta ao automático, 0 zera.
A tela passou a mandar num PATCH só todos os campos editados da linha.
"""

from decimal import Decimal

import pytest_asyncio
from sqlalchemy import delete, select

from app.models import FinanceiroConsorcio, UserRole

BASE = "/api/financeiro/consorcio"


@pytest_asyncio.fixture(autouse=True)
async def _limpar_consorcio(db):
    await db.execute(delete(FinanceiroConsorcio))
    await db.commit()
    yield
    await db.execute(delete(FinanceiroConsorcio))
    await db.commit()


async def _nova_linha(client, make_user, auth_as) -> str:
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.post(BASE, json={"emp": "Kia", "alienacao": "pago"})
    assert r.status_code == 201, r.text
    assert r.json()["imposto1_manual"] is None  # nasce no automático
    return r.json()["id"]


async def _no_banco(db, row_id):
    return (
        await db.execute(
            select(FinanceiroConsorcio)
            .where(FinanceiroConsorcio.id == row_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def test_imposto1_zera_digita_e_volta_ao_automatico(client, make_user, auth_as, db):
    row_id = await _nova_linha(client, make_user, auth_as)

    r = await client.patch(f"{BASE}/{row_id}", json={"imposto1_manual": 0})
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["imposto1_manual"]) == 0
    assert (await _no_banco(db, row_id)).imposto1_manual == Decimal("0.00")

    r = await client.patch(f"{BASE}/{row_id}", json={"imposto1_manual": 1141.80})
    assert Decimal(r.json()["imposto1_manual"]) == Decimal("1141.80")
    listed = (await client.get(BASE)).json()
    assert [Decimal(x["imposto1_manual"]) for x in listed] == [Decimal("1141.80")]

    # Outro campo não mexe no valor digitado.
    r = await client.patch(f"{BASE}/{row_id}", json={"obs": "1% pago 27/10"})
    assert Decimal(r.json()["imposto1_manual"]) == Decimal("1141.80")

    # Apagar a célula (null) volta ao automático.
    r = await client.patch(f"{BASE}/{row_id}", json={"imposto1_manual": None})
    assert r.json()["imposto1_manual"] is None
    assert (await _no_banco(db, row_id)).imposto1_manual is None


async def test_imposto1_recusa_negativo_e_mais_de_dois_decimais(client, make_user, auth_as, db):
    row_id = await _nova_linha(client, make_user, auth_as)
    for valor in (-1, "12.345"):
        r = await client.patch(f"{BASE}/{row_id}", json={"imposto1_manual": valor})
        assert r.status_code == 422, (valor, r.text)
    assert (await _no_banco(db, row_id)).imposto1_manual is None


async def test_patch_com_varios_campos_grava_todos(client, make_user, auth_as, db):
    """A tela junta Crédito + Lance (+ o 1%) editados em seguida num PATCH só."""
    row_id = await _nova_linha(client, make_user, auth_as)
    r = await client.patch(
        f"{BASE}/{row_id}",
        json={"credito": 125472, "lance": 62924.21, "imposto1_manual": 0},
    )
    assert r.status_code == 200, r.text
    linha = await _no_banco(db, row_id)
    assert (linha.credito, linha.lance, linha.imposto1_manual) == (
        Decimal("125472.00"),
        Decimal("62924.21"),
        Decimal("0.00"),
    )
