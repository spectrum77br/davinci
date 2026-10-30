"""Logística por equipe — os botões de UMA linha (30/10/2026, revisão das
equipes Londres/Israel/Marrocos). A lista já vinha cercada; as rotas por id
(atualizar, mensagem e situação no Bling, chamado, editar, excluir) não: quem
soubesse o id mexia na linha de outra equipe. Agora a linha de fora é 404,
como no Reembolso. Admin e quem não tem equipe: como antes."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Logistica, StoreInfo, User, UserRole, UserStatus

pytestmark = pytest.mark.asyncio

PERM = {"logistica": {"view": True, "edit": True}}


def _usuario(role: UserRole, equipes: list[int] | None) -> User:
    email = f"log-eq-{uuid.uuid4().hex[:6]}@davinci-test.com"
    return User(
        open_id=f"email:{email}",
        email=email,
        role=role,
        status=UserStatus.ACTIVE,
        permissions=PERM,
        sales_teams=equipes,
    )


async def _como(db: AsyncSession, auth_as, equipes: list[int] | None, role=UserRole.USER) -> User:
    u = _usuario(role, equipes)
    db.add(u)
    await db.commit()
    auth_as(u)
    return u


@pytest_asyncio.fixture
async def linhas(db: AsyncSession) -> dict[str, Logistica]:
    """Uma loja e uma linha de cada equipe (a da Israel casa pelo pedido)."""
    dono = _usuario(UserRole.ADMIN, None)
    db.add(dono)
    await db.flush()
    db.add_all(
        [
            StoreInfo(
                user_id=dono.id,
                platform="shopee",
                account_name="vortan",
                bling_store_id="b-lon",
                sales_team=101,
            ),
            StoreInfo(
                user_id=dono.id,
                platform="ml",
                account_name="atv",
                bling_store_id="b-isr",
                sales_team=102,
            ),
            BlingOrder(numero="910101", numeroloja="MKT-LON", loja="b-lon"),
            BlingOrder(numero="910102", numeroloja="MKT-ISR", loja="b-isr"),
        ]
    )
    lon = Logistica(pedido_bling="910101", plataforma="Shopee", conta="vortan")
    isr = Logistica(pedido_bling="910102", plataforma="Mercado Livre", conta="atv")
    db.add_all([lon, isr])
    await db.commit()
    return {"londres": lon, "israel": isr}


async def test_linha_de_outra_equipe_e_404_e_nada_muda(db, client, auth_as: Callable, linhas):
    isr, lon = linhas["israel"].id, linhas["londres"].id
    await _como(db, auth_as, [101])
    r = await client.patch(f"/api/logistica/{isr}", json={"rastreio": "XX"})
    assert r.status_code == 404
    assert (await client.delete(f"/api/logistica/{isr}")).status_code == 404
    for acao in ("atualizar-rastreio", "enviar-chamado", "mensagem-bling/preview"):
        assert (await client.post(f"/api/logistica/{isr}/{acao}")).status_code == 404, acao
    # A coluna direto do banco (o expire_all derrubaria o usuário do login de teste).
    rastreio = (
        await db.execute(select(Logistica.rastreio).where(Logistica.id == isr))
    ).scalar_one()
    assert rastreio is None

    # A da própria equipe passa da cerca.
    r = await client.patch(f"/api/logistica/{lon}", json={"rastreio": "LON123"})
    assert r.status_code == 200, r.text
    assert r.json()["rastreio"] == "LON123"


async def test_admin_e_sem_equipe_como_antes(db, client, auth_as: Callable, linhas):
    isr = linhas["israel"]
    await _como(db, auth_as, None)
    r = await client.patch(f"/api/logistica/{isr.id}", json={"rastreio": "SEM1"})
    assert r.status_code == 200, r.text
    await _como(db, auth_as, [101], role=UserRole.ADMIN)
    r = await client.patch(f"/api/logistica/{isr.id}", json={"rastreio": "ADM1"})
    assert r.status_code == 200, r.text
    assert (await client.delete(f"/api/logistica/{isr.id}")).status_code == 204


async def test_equipe_sem_loja_nao_mexe_em_nada(db, client, auth_as: Callable, linhas):
    await _como(db, auth_as, [104])
    for linha in linhas.values():
        r = await client.patch(f"/api/logistica/{linha.id}", json={"rastreio": "X"})
        assert r.status_code == 404
