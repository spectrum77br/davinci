"""Quem vê o Flex (`flex_usuarios`, Eduardo 05/10/2026: "por enquanto somente
os usuários heisenberg e o thorfinn podem ver").

Para quem não está na lista — inclusive admin — o Flex não existe: toda rota
/api/flex/* é o 404 de rota desconhecida, a Logística não tem `?envio=flex`
nem o selo (`envio_flex`/`envio_tipo` saem vazios) e o Controle de Estoque não
marca pedido Flex.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import BlingOrder, FlexPedido, Logistica, UserRole, UserStatus
from app.services import flex_config


async def _pessoa(db, make_user, nome, *, role=UserRole.ADMIN, status=UserStatus.ACTIVE):
    u = await make_user(role=role, status=status)
    u.name = nome
    await db.commit()
    await db.refresh(u)
    return u


@pytest.fixture
def lista(monkeypatch):
    monkeypatch.setattr(get_settings(), "flex_usuarios", " Heisenberg, thorfinn ,")


def test_padrao_ninguem_ve():
    assert Settings.model_fields["flex_usuarios"].default == ""
    assert flex_config.usuarios("") == frozenset()
    assert flex_config.usuarios(" Heisenberg, thorfinn ,") == {"heisenberg", "thorfinn"}


ROTAS = [
    ("GET", "/api/flex/acesso"),
    ("GET", "/api/flex/config"),
    ("GET", "/api/flex/anuncios"),
    ("POST", "/api/flex/sincronizar"),
    ("POST", "/api/flex/emergencia"),
    ("GET", "/api/flex/emergencia/ultima"),
    ("GET", "/api/flex/log"),
    ("GET", "/api/flex/pedidos"),
    ("POST", "/api/flex/pedidos/123/acertado"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("metodo", "rota"), ROTAS)
async def test_admin_fora_da_lista_recebe_404_de_rota_que_nao_existe(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable, lista, metodo, rota
):
    auth_as(await _pessoa(db, make_user, "eduardo felipe"))
    r = await client.request(metodo, rota)
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


@pytest.mark.asyncio
async def test_quem_esta_na_lista_ve(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable, lista
):
    auth_as(await _pessoa(db, make_user, "HEISENBERG"))
    assert (await client.get("/api/flex/acesso")).json() == {"ok": True}
    assert (await client.get("/api/flex/config")).status_code == 200
    auth_as(await _pessoa(db, make_user, "thorfinn", role=UserRole.USER))
    # Na lista, mas as rotas continuam pedindo a permissão da Logística.
    assert (await client.get("/api/flex/acesso")).status_code == 200
    assert (await client.get("/api/flex/config")).status_code == 403


@pytest.mark.asyncio
async def test_na_lista_mas_suspenso_ou_lista_vazia_nao_ve(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable, monkeypatch
):
    monkeypatch.setattr(get_settings(), "flex_usuarios", "heisenberg")
    auth_as(await _pessoa(db, make_user, "heisenberg", status=UserStatus.SUSPENDED))
    assert (await client.get("/api/flex/acesso")).status_code == 404
    monkeypatch.setattr(get_settings(), "flex_usuarios", "")
    auth_as(await _pessoa(db, make_user, "heisenberg"))
    assert (await client.get("/api/flex/acesso")).status_code == 404


@pytest.mark.asyncio
async def test_rota_do_flex_fora_da_documentacao(client: AsyncClient):
    r = await client.get("/api/openapi.json")
    assert r.status_code == 200
    assert not [p for p in r.json()["paths"] if p.startswith("/api/flex")]


async def _linhas_logistica(db: AsyncSession) -> None:
    db.add_all([
        Logistica(pedido_bling="V1", plataforma="Mercado Livre", meli_status={},
                  envio_flex=True, envio_tipo="self_service"),
        Logistica(pedido_bling="V2", plataforma="Mercado Livre", meli_status={},
                  envio_flex=False, envio_tipo="cross_docking"),
    ])
    await db.commit()


@pytest.mark.asyncio
async def test_logistica_sem_selo_e_sem_aba_para_quem_nao_ve(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable, lista
):
    await _linhas_logistica(db)
    auth_as(await _pessoa(db, make_user, "eduardo felipe"))
    r = await client.get("/api/logistica?plataforma=ml")
    assert r.status_code == 200
    por_pedido = {c["pedido_bling"]: c for c in r.json()}
    assert {"V1", "V2"} <= set(por_pedido)
    for p in ("V1", "V2"):
        assert por_pedido[p]["envio_flex"] is None
        assert por_pedido[p]["envio_tipo"] is None
    assert (await client.get("/api/logistica?envio=flex")).status_code == 404


@pytest.mark.asyncio
async def test_logistica_com_selo_para_quem_ve(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable, lista
):
    await _linhas_logistica(db)
    auth_as(await _pessoa(db, make_user, "heisenberg"))
    r = await client.get("/api/logistica?plataforma=ml")
    por_pedido = {c["pedido_bling"]: c for c in r.json()}
    assert por_pedido["V1"]["envio_flex"] is True
    assert por_pedido["V1"]["envio_tipo"] == "self_service"
    assert por_pedido["V2"]["envio_flex"] is False
    r = await client.get("/api/logistica?envio=flex")
    assert r.status_code == 200
    assert [c["pedido_bling"] for c in r.json()] == ["V1"]


@pytest.mark.asyncio
async def test_controle_de_estoque_so_marca_flex_para_quem_ve(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable, lista
):
    d = date(2026, 5, 28)
    db.add_all([
        BlingOrder(bling_id=940001, numero="940001", item_codigo="sku-1", item_index=0,
                   situacao="15", em_andamento_data=d),
        FlexPedido(bling_id=940001, plataforma="shopee", envio_tipo="90022"),
    ])
    await db.commit()
    url = "/api/estoque/pedidos?data_inicio=2026-05-28&data_fim=2026-05-28"

    auth_as(await _pessoa(db, make_user, "eduardo felipe"))
    r = await client.get(url)
    assert r.status_code == 200, r.text
    assert {linha["flex"] for linha in r.json()["data"]} == {False}

    auth_as(await _pessoa(db, make_user, "thorfinn"))
    r = await client.get(url)
    assert {linha["flex"] for linha in r.json()["data"]} == {True}
