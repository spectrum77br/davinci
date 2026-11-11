"""Quem vê o Flex: admin ativo (Eduardo, 05/11/2026: "libere o flex somente
para os admin"; de 05/10 a 05/11 era a lista `flex_usuarios` — heisenberg e
thorfinn) e, desde 11/11/2026, cairo SA (pelo e-mail) e churchill (pelo nome).

Para os outros o Flex não existe: toda rota /api/flex/* é o 404 de rota
desconhecida, a Logística não tem `?envio=flex` nem o selo
(`envio_flex`/`envio_tipo` saem vazios) e o Controle de Estoque não marca
pedido Flex.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import BlingOrder, FlexPedido, Logistica, UserRole, UserStatus
from app.services import flex_config

# Usuário comum com tudo que as telas pedem — o que barra o Flex é o papel.
PERMS = {
    "logistica": {"view": True, "edit": True},
    "controle_estoque": {"view": True, "edit": True},
}


async def _pessoa(
    db, make_user, nome, *, role=UserRole.ADMIN, status=UserStatus.ACTIVE, email=None
):
    u = await make_user(role=role, status=status, permissions=PERMS, email=email)
    u.name = nome
    await db.commit()
    await db.refresh(u)
    return u


def test_lista_de_nomes_saiu():
    # A regra é o papel, não o nome: a chave velha do .env não volta a valer.
    assert "flex_usuarios" not in Settings.model_fields
    assert not hasattr(flex_config, "usuarios")


class _U:
    def __init__(self, role, status=UserStatus.ACTIVE, name="heisenberg", email="x@y.com"):
        self.role, self.status, self.name, self.email = role, status, name, email


def test_pode_ver_admin_ativo():
    assert flex_config.pode_ver(_U(UserRole.ADMIN)) is True
    assert flex_config.pode_ver(_U(UserRole.USER)) is False
    assert flex_config.pode_ver(_U(UserRole.ADMIN, UserStatus.SUSPENDED)) is False
    assert flex_config.pode_ver(None) is False


def test_pode_ver_cairo_sa_e_churchill():
    # 11/11/2026: cairo SA pelo e-mail (há outro "cairo", que segue sem ver) e
    # churchill pelo nome — a mesma régua da aba Estoque Negativo.
    assert flex_config.pode_ver(_U(UserRole.USER, name="cairo sa", email="SA.Geral@tutamail.com"))
    assert flex_config.pode_ver(_U(UserRole.USER, name="Churchill ")) is True
    assert flex_config.pode_ver(_U(UserRole.USER, name="cairo", email="outro@tuta.com")) is False
    assert flex_config.pode_ver(_U(UserRole.USER, name="churchil")) is False
    assert (
        flex_config.pode_ver(_U(UserRole.USER, UserStatus.SUSPENDED, name="churchill")) is False
    )


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
async def test_quem_nao_e_admin_recebe_404_de_rota_que_nao_existe(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable, metodo, rota
):
    # Usuário comum, mesmo com a permissão da Logística e o nome da lista velha.
    auth_as(await _pessoa(db, make_user, "thorfinn", role=UserRole.USER))
    r = await client.request(metodo, rota)
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


@pytest.mark.asyncio
async def test_admin_ve(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable
):
    # Qualquer admin — não só os nomes da lista velha.
    auth_as(await _pessoa(db, make_user, "joana dark"))
    assert (await client.get("/api/flex/acesso")).json() == {
        "ok": True,
        # O local de saída (de fábrica): os textos da Logística dizem de onde sai.
        "cidade": "São Bernardo do Campo",
        "lote": "sp",
    }
    assert (await client.get("/api/flex/config")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("nome", "email"), [("cairo sa", "sa.geral@tutamail.com"), ("churchill", None)]
)
async def test_cairo_sa_e_churchill_veem(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable, nome, email
):
    auth_as(await _pessoa(db, make_user, nome, role=UserRole.USER, email=email))
    assert (await client.get("/api/flex/acesso")).status_code == 200
    assert (await client.get("/api/flex/config")).status_code == 200
    assert (await client.get("/api/flex/anuncios")).status_code == 200
    # Trocar o local de saída continua só para admin.
    r = await client.put("/api/flex/local", json={"cidade": "Piracicaba", "lote": "pi"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_suspenso_nao_ve(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable
):
    auth_as(await _pessoa(db, make_user, "heisenberg", status=UserStatus.SUSPENDED))
    assert (await client.get("/api/flex/acesso")).status_code in (401, 403, 404)


@pytest.mark.asyncio
async def test_rota_do_flex_fora_da_documentacao(client: AsyncClient):
    r = await client.get("/api/openapi.json")
    assert r.status_code == 200
    assert not [p for p in r.json()["paths"] if p.startswith("/api/flex")]


async def _linhas_logistica(db: AsyncSession) -> None:
    db.add_all(
        [
            Logistica(
                pedido_bling="V1",
                plataforma="Mercado Livre",
                meli_status={},
                envio_flex=True,
                envio_tipo="self_service",
            ),
            Logistica(
                pedido_bling="V2",
                plataforma="Mercado Livre",
                meli_status={},
                envio_flex=False,
                envio_tipo="cross_docking",
            ),
        ]
    )
    await db.commit()


@pytest.mark.asyncio
async def test_logistica_sem_selo_e_sem_aba_para_quem_nao_ve(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable
):
    await _linhas_logistica(db)
    auth_as(await _pessoa(db, make_user, "eduardo felipe", role=UserRole.USER))
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
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable
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
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable
):
    d = date(2026, 5, 28)
    db.add_all(
        [
            BlingOrder(
                bling_id=940001,
                numero="940001",
                item_codigo="a003.ci",
                item_index=0,
                situacao="15",
                em_andamento_data=d,
            ),
            FlexPedido(bling_id=940001, plataforma="shopee", envio_tipo="90022"),
        ]
    )
    await db.commit()
    url = "/api/estoque/pedidos?data_inicio=2026-05-28&data_fim=2026-05-28"

    comum = await _pessoa(db, make_user, "eduardo felipe", role=UserRole.USER)
    comum.stock_tags = ["ci"]  # o Controle de Estoque pede uma tag a quem não é admin
    await db.commit()
    auth_as(comum)
    r = await client.get(url)
    assert r.status_code == 200, r.text
    assert [linha["flex"] for linha in r.json()["data"]] == [False]

    auth_as(await _pessoa(db, make_user, "thorfinn"))
    r = await client.get(url)
    assert {linha["flex"] for linha in r.json()["data"]} == {True}
