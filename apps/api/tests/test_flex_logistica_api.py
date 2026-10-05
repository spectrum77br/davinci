"""Aba Flex da Logística: `GET /api/logistica?envio=flex` e os substatus do Flex.

A aba é uma VISÃO A MAIS: o pedido Flex continua nas abas ML/Shopee (com o
selo, `envio_flex`) e aparece também na aba Flex, que junta as duas
plataformas. Linha ainda não lida (`envio_flex` NULL) não entra.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica, User, UserRole, UserStatus
from app.services import flex_config, logistica_rules


@pytest.fixture(autouse=True)
def _flex_visivel(monkeypatch):
    # Estes testes são do Flex em si; quem vê (`flex_usuarios`) tem teste
    # próprio em test_flex_visibilidade.py.
    monkeypatch.setattr(flex_config, "pode_ver", lambda user: True)


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _linhas(db: AsyncSession) -> dict[str, Logistica]:
    linhas = {
        "ml_flex": Logistica(
            pedido_bling="F1",
            plataforma="Mercado Livre",
            meli_status={},
            envio_flex=True,
            envio_tipo="self_service",
        ),
        "ml_comum": Logistica(
            pedido_bling="F2",
            plataforma="Mercado Livre",
            meli_status={},
            envio_flex=False,
            envio_tipo="cross_docking",
        ),
        "ml_nao_lido": Logistica(pedido_bling="F3", plataforma="Mercado Livre", meli_status={}),
        "shopee_flex": Logistica(
            pedido_bling="F4",
            plataforma="Shopee",
            meli_status={},
            envio_flex=True,
            envio_tipo="90022 · Shopee Entrega Direta",
        ),
        "shopee_spx": Logistica(
            pedido_bling="F5",
            plataforma="Shopee",
            meli_status={},
            envio_flex=False,
            envio_tipo="90001 · Shopee Xpress",
        ),
        # Defesa: plataforma sem Flex nunca entra na aba, nem com a marca.
        "amazon": Logistica(
            pedido_bling="F6",
            plataforma="Amazon",
            meli_status={},
            envio_flex=True,
        ),
    }
    db.add_all(linhas.values())
    await db.commit()
    return {k: str(v.id) for k, v in linhas.items()}


@pytest.mark.asyncio
async def test_aba_flex_junta_ml_e_shopee_so_com_envio_flex(
    client: AsyncClient, db: AsyncSession, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    ids = await _linhas(db)

    r = await client.get("/api/logistica?envio=flex")
    assert r.status_code == 200
    por_id = {c["id"]: c for c in r.json()}
    assert set(por_id) == {ids["ml_flex"], ids["shopee_flex"]}
    assert por_id[ids["ml_flex"]]["envio_flex"] is True
    assert por_id[ids["ml_flex"]]["envio_tipo"] == "self_service"
    assert por_id[ids["shopee_flex"]]["plataforma"] == "Shopee"

    # Dá para combinar com a plataforma (o seletor ML/Shopee da aba).
    r = await client.get("/api/logistica?envio=flex&plataforma=shopee")
    assert {c["id"] for c in r.json()} == {ids["shopee_flex"]}


@pytest.mark.asyncio
async def test_aba_ml_continua_mostrando_o_pedido_flex_com_o_selo(
    client: AsyncClient, db: AsyncSession, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    ids = await _linhas(db)

    r = await client.get("/api/logistica?plataforma=ml")
    por_id = {c["id"]: c for c in r.json()}
    assert {ids["ml_flex"], ids["ml_comum"], ids["ml_nao_lido"]} <= set(por_id)
    assert por_id[ids["ml_flex"]]["envio_flex"] is True
    assert por_id[ids["ml_comum"]]["envio_flex"] is False
    assert por_id[ids["ml_nao_lido"]]["envio_flex"] is None
    assert por_id[ids["ml_nao_lido"]]["envio_tipo"] is None


@pytest.mark.asyncio
async def test_envio_desconhecido_e_recusado(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    # Valor errado não pode virar "sem filtro" (a tela acharia que só vê Flex).
    auth_as(admin)
    r = await client.get("/api/logistica?envio=turbo")
    assert r.status_code == 422


@pytest.mark.parametrize(
    ("substatus", "rotulo"),
    [
        ("buyer_rescheduled", "Comprador reagendou"),
        ("delivery_blocked", "Entregue longe do endereço (aguarda confirmação)"),
        ("waiting_for_confirmation", "Marcado entregue após o prazo (aguarda confirmação)"),
        # Os do Flex que já existiam continuam iguais (chaves da aba Status).
        ("soon_deliver", "A caminho"),
        ("receiver_absent", "Destinatário ausente"),
        ("bad_address", "Endereço incorreto"),
        ("out_for_delivery", "Saiu p/ entrega"),
        ("refused_delivery", "Entrega recusada"),
    ],
)
def test_substatus_do_flex_traduzidos(substatus, rotulo):
    assert logistica_rules.traduzir_valor("ship_substatus", substatus) == rotulo
    meli = {"order_status": "paid", "ship_status": "shipped", "ship_substatus": substatus}
    assert logistica_rules.assinatura_pt(meli) == f"Pago | Enviado | {rotulo}"
    assert logistica_rules.localizacao_pt(meli) == rotulo
