"""Produtos 2 › estoque do produto › "Transferir" (M-44, Harry 24/11/2026).

Usa a rota de transferência que já existe com `mesma_numeracao: true`: só entre lotes do MESMO
produto (numeração igual, tag diferente), conferido no servidor no "conferir" e no "transferir".
As tags são a lista "Tag de envio" de Cadastros › Produtos. Sem o campo (Controle de Estoque ›
Transferir estoque) nada muda: troca de produto continua aceita."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, ProdutoCadastro, ProdutoCadastroTipo, UserRole
from app.models.produto_cadastro import CHAVE_TAG_ENVIO
from app.services import devolution_stock_return as dsr

pytestmark = pytest.mark.asyncio

URL = "/api/estoque/transferencias"
TAGS = (".cd", ".ci", ".pi", ".ra", ".sa", ".sp")


class FakeBling:
    def __init__(self, saldos: dict[int, tuple[int, int]]):
        self.saldos = saldos
        self.lancamentos: list[tuple[int, int, str]] = []

    async def _request(self, method, path, *, params=None, json=None):
        assert (method, path) == ("GET", "/estoques/saldos")
        ids = [int(v) for k, v in params if k == "idsProdutos[]"]
        data = [
            {
                "produto": {"id": i},
                "saldoFisicoTotal": self.saldos[i][0],
                "saldoVirtualTotal": self.saldos[i][1],
            }
            for i in ids
            if i in self.saldos
        ]
        return httpx.Response(200, json={"data": data}, request=httpx.Request(method, "http://x"))

    async def update_stock_by_id(self, pid, qty, *, operation, **_kw):
        self.lancamentos.append((int(pid), int(qty), operation))
        return {}


@pytest.fixture
async def cenario(db: AsyncSession, make_user):
    dono = await make_user()
    tipo = ProdutoCadastroTipo(nome="Tag de envio", chave=CHAVE_TAG_ENVIO)
    db.add(tipo)
    await db.flush()
    db.add_all(ProdutoCadastro(tipo_id=tipo.id, nome=t) for t in TAGS)
    skus = {
        "dg019.ra": 880401,
        "dg019.sp": 880402,
        "z2819": 880403,
        "z2819.sp": 880404,
        "dg107.ci": 880405,
        "dg108.ci": 880406,
    }
    db.add_all(
        Product(
            user_id=dono.id,
            sku=sku,
            name=f"Produto {sku}",
            situacao="A",
            formato="S",
            bling_product_id=bid,
            bling_cost_price=Decimal("10"),
        )
        for sku, bid in skus.items()
    )
    await db.commit()


@pytest.fixture
def bling(monkeypatch):
    fake = FakeBling({880401: (95, 95), 880403: (10, 10), 880405: (5, 5)})

    async def _client(_session):
        return fake

    monkeypatch.setattr(dsr, "_get_bling_client", _client)
    return fake


@pytest.fixture
async def admin(db: AsyncSession, make_user, auth_as: Callable):
    u = await make_user(role=UserRole.ADMIN)
    u.name = "heisenberg"
    await db.commit()
    auth_as(u)
    return u


def _corpo(*linhas, mesma_numeracao: bool | None = None):
    corpo: dict = {
        "linhas": [{"origem": o, "destino": d, "quantidade": q} for o, d, q in linhas],
    }
    if mesma_numeracao is not None:
        corpo["mesma_numeracao"] = mesma_numeracao
    return corpo


async def test_conferir_com_mesma_numeracao(client: AsyncClient, cenario, bling, admin):
    r = await client.post(
        f"{URL}/conferir",
        json=_corpo(
            ("dg019.ra", "dg019.sp", 10),
            ("z2819", "z2819.sp", 1),
            ("dg107.ci", "dg108.ci", 1),
            ("dg019.ra", "dg019.ra", 1),
            mesma_numeracao=True,
        ),
    )
    assert r.status_code == 200, r.text
    erros = [x["erros"] for x in r.json()["linhas"]]
    assert erros[0] == []
    assert erros[1] == []
    assert erros[2] == ["dg107.ci → dg108.ci: a numeração precisa ser a mesma"]
    assert "dg019.ra → dg019.ra: as tags precisam ser diferentes" in erros[3]
    assert bling.lancamentos == []


async def test_transferir_com_mesma_numeracao_recusa_e_aceita(
    client: AsyncClient, cenario, bling, admin
):
    r = await client.post(
        URL,
        json=_corpo(("dg107.ci", "dg108.ci", 1), mesma_numeracao=True),
        headers={"Idempotency-Key": "chave-mn-0001"},
    )
    assert r.status_code == 422
    linha = r.json()["detail"]["linhas"][0]
    assert linha["erros"] == ["dg107.ci → dg108.ci: a numeração precisa ser a mesma"]
    r = await client.post(
        URL,
        json=_corpo(("dg019.ra", "dg019.ra", 1), mesma_numeracao=True),
        headers={"Idempotency-Key": "chave-mn-0002"},
    )
    assert r.status_code == 422
    assert bling.lancamentos == []

    r = await client.post(
        URL,
        json=_corpo(("dg019.ra", "dg019.sp", 10), mesma_numeracao=True),
        headers={"Idempotency-Key": "chave-mn-0003"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "ok"
    r = await client.post(
        URL,
        json=_corpo(("z2819", "z2819.sp", 1), mesma_numeracao=True),
        headers={"Idempotency-Key": "chave-mn-0004"},
    )
    assert r.status_code == 200, r.text
    assert bling.lancamentos == [
        (880401, 10, "S"),
        (880402, 10, "E"),
        (880403, 1, "S"),
        (880404, 1, "E"),
    ]


async def test_sem_o_campo_controle_de_estoque_continua_igual(
    client: AsyncClient, cenario, bling, admin
):
    for corpo in (
        _corpo(("dg107.ci", "dg108.ci", 1)),
        _corpo(("dg107.ci", "dg108.ci", 1), mesma_numeracao=False),
    ):
        r = await client.post(f"{URL}/conferir", json=corpo)
        linha = r.json()["linhas"][0]
        assert linha["erros"] == []
        assert linha["troca_produto"] is True
    r = await client.post(
        URL, json=_corpo(("dg107.ci", "dg108.ci", 1)), headers={"Idempotency-Key": "chave-mn-0005"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "ok"
    assert bling.lancamentos == [(880405, 1, "S"), (880406, 1, "E")]


async def test_so_admin(client: AsyncClient, make_user, auth_as: Callable, cenario, bling):
    auth_as(await make_user(role=UserRole.USER))
    r = await client.post(
        f"{URL}/conferir", json=_corpo(("dg019.ra", "dg019.sp", 1), mesma_numeracao=True)
    )
    assert r.status_code == 403
